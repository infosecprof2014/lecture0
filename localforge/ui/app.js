// LocalForge AI web interface. Plain JavaScript, no build step, nothing loaded from the internet.
// All model output is inserted with textContent (never innerHTML).
"use strict";

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue; // null/undefined means "no attribute"
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

// Read a streamed NDJSON response one event at a time.
async function* ndjson(response) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let i;
    while ((i = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, i).trim();
      buffer = buffer.slice(i + 1);
      if (line) yield JSON.parse(line);
    }
  }
  if (buffer.trim()) yield JSON.parse(buffer);
}

async function postStream(path, body, signal) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try { message = (await response.json()).message || message; } catch { /* not JSON */ }
    throw new Error(message);
  }
  return response;
}

async function getJSON(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

// ---------------------------------------------------------------- status line

async function refreshStatus() {
  const status = $("#status");
  try {
    const h = await getJSON("/health");
    const ollama = h.ollama.reachable ? `Ollama ${h.ollama.version}` : "Ollama not running";
    status.textContent = `${h.configured_model} · ${ollama} · ${h.free_ram_mb} MB free${h.busy ? " · busy" : ""}`;
    status.className = "status " + (h.ollama.reachable ? "ok" : "bad");
  } catch {
    status.textContent = "Core service not reachable";
    status.className = "status bad";
  }
}

// ---------------------------------------------------------------- tabs

function showView(name) {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.setAttribute("aria-pressed", String(tab.dataset.view === name));
  }
  for (const view of ["chat", "tasks", "create"]) $(`#view-${view}`).hidden = name !== view;
  if (name === "tasks") loadTasks();
  if (name === "create") loadArtifacts();
}

// ---------------------------------------------------------------- chat

let conversationId = null;
let chatAbort = null;

function addMessage(role, text, meta) {
  const metaNode = meta ? el("span", { class: "meta" }, meta) : null;
  const body = el("span", {}, text);
  const node = el("div", { class: `msg ${role}` }, body, metaNode);
  $("#messages").append(node);
  node.scrollIntoView({ block: "end" });
  return { node, body };
}

async function loadConversations() {
  const list = $("#conv-list");
  try {
    const { conversations } = await getJSON("/conversations");
    list.replaceChildren(...conversations.map((c) => el("li", {},
      el("button", {
        type: "button", "aria-current": String(c.id === conversationId),
        onclick: () => openConversation(c.id),
      }, c.title, el("small", {}, `${c.message_count} messages`)))));
  } catch (err) {
    list.replaceChildren(el("li", { class: "error" }, err.message));
  }
}

async function openConversation(id) {
  conversationId = id;
  const conv = await getJSON(`/conversations/${id}`);
  $("#messages").replaceChildren();
  for (const m of conv.messages) {
    addMessage(m.role, m.content, m.status === "complete" ? null : `(${m.status})`);
  }
  loadConversations();
}

function newChat() {
  conversationId = null;
  $("#messages").replaceChildren();
  loadConversations();
  $("#chat-input").focus();
}

async function sendChat(event) {
  event.preventDefault();
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addMessage("user", text);
  const reply = addMessage("assistant", "");
  const messages = $("#messages");
  messages.setAttribute("aria-busy", "true");
  $("#chat-send").disabled = true;
  $("#chat-stop").hidden = false;
  chatAbort = new AbortController();
  try {
    const body = { message: text };
    if (conversationId) body.conversation_id = conversationId;
    const response = await postStream("/chat", body, chatAbort.signal);
    for await (const e of ndjson(response)) {
      if (e.type === "start") conversationId = e.conversation_id;
      else if (e.type === "token") reply.body.textContent += e.content;
      else if (e.type === "done") {
        reply.node.append(el("span", { class: "meta" }, `${e.tokens} tokens · ${(e.duration_ms / 1000).toFixed(1)} s · ${e.tokens_per_s ?? "?"} tokens/s`));
        speak(reply.body.textContent);
      } else if (e.type === "error") {
        reply.node.append(el("span", { class: "meta error" }, e.message));
      }
      reply.node.scrollIntoView({ block: "end" });
    }
  } catch (err) {
    const msg = err.name === "AbortError" ? "Stopped." : err.message;
    reply.node.append(el("span", { class: "meta error" }, msg));
  } finally {
    messages.setAttribute("aria-busy", "false");
    $("#chat-send").disabled = false;
    $("#chat-stop").hidden = true;
    chatAbort = null;
    loadConversations();
    refreshStatus();
  }
}

// ---------------------------------------------------------------- voice

let recorder = null;
let recordTimer = null;
let caps = {};

function speak(text) {
  if (!$("#speak-answers").checked || !("speechSynthesis" in window) || !text.trim()) return;
  speechSynthesis.cancel();
  speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

function setMic(on) {
  $("#mic").setAttribute("aria-pressed", String(on));
  $("#mic").textContent = on ? "■ Stop recording" : "🎤 Speak";
}

async function toggleMic() {
  const note = $("#voice-note");
  if (recorder) { recorder.stop(); return; }
  if (!caps.voice_input) {
    note.textContent = "Voice input is not installed yet (run scripts\\install-phase7.ps1).";
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    note.textContent = `Microphone not available: ${err.message}`;
    return;
  }
  const chunks = [];
  recorder = new MediaRecorder(stream);
  recorder.addEventListener("dataavailable", (e) => chunks.push(e.data));
  recorder.addEventListener("stop", async () => {
    clearTimeout(recordTimer);
    stream.getTracks().forEach((t) => t.stop());
    const type = recorder.mimeType || "audio/webm";
    recorder = null;
    setMic(false);
    note.textContent = "Understanding your speech…";
    try {
      const r = await fetch("/speech/transcribe", { method: "POST", headers: { "Content-Type": type },
        body: new Blob(chunks, { type }) });
      const body = await r.json();
      if (!r.ok) throw new Error(body.message || `HTTP ${r.status}`);
      if (!body.text) { note.textContent = "I did not catch any words - try again."; return; }
      note.textContent = `Heard in ${body.seconds} s`;
      $("#chat-input").value = body.text;
      $("#chat-form").requestSubmit();
    } catch (err) {
      note.textContent = err.message;
    }
  });
  recorder.start();
  setMic(true);
  note.textContent = "Listening… click Stop recording when you are done.";
  recordTimer = setTimeout(() => recorder?.stop(), 60000);
}

// ---------------------------------------------------------------- create (files, images, videos)

let creating = false;
const CREATE_HINTS = {
  file: 'Describe the file (e.g. "a CSV of the 10 largest countries with population")',
  image: 'Describe the picture (e.g. "a lighthouse on a cliff at sunset, dramatic clouds")',
  video: 'Describe the video idea (e.g. "a short story about a robot learning to paint")',
};

function createKind() {
  return document.querySelector('input[name="kind"]:checked').value;
}

function updateCreateForm() {
  const kind = createKind();
  $("#create-label").textContent = CREATE_HINTS[kind];
  const note = $("#create-note");
  if (kind === "image" && !caps.images) note.textContent = "Image generation is not installed yet.";
  else if (kind === "video" && !caps.video) note.textContent = "Video (FFmpeg) is not installed yet.";
  else if (kind === "video" && !caps.images) note.textContent = "No image model: videos will use plain backgrounds.";
  else if (kind === "image") note.textContent = "About 30 s to 2 min per image on this PC.";
  else if (kind === "video") note.textContent = "Several minutes: one picture per scene.";
  else note.textContent = "";
}

function showArtifact(a) {
  const box = $("#create-result");
  const url = `/artifacts/${a.artifact_id ?? a.id}/content`;
  const title = el("h3", {}, `${a.kind}: ${a.name ?? a.meta?.filename ?? ""}`);
  let body;
  if (a.kind === "image") body = el("img", { src: url, alt: a.prompt ?? "generated image" });
  else if (a.kind === "video") body = el("video", { src: url, controls: "" });
  else body = el("a", { href: url, download: "" }, "Download the file");
  const meta = a.meta ? el("pre", {}, JSON.stringify(a.meta.storyboard ?? a.meta, null, 2)) : null;
  box.replaceChildren(el("section", { class: "preview" }, title, body, meta));
}

async function loadArtifacts() {
  const list = $("#artifact-list");
  try {
    const { artifacts } = await getJSON("/artifacts");
    list.replaceChildren(...artifacts.map((a) => el("li", {},
      el("button", { type: "button", disabled: a.status === "done" ? null : "",
        onclick: () => showArtifact(a) },
        `${{ file: "📄", image: "🖼️", video: "🎬" }[a.kind]} ${a.name ?? a.prompt.slice(0, 40)}`,
        el("small", {}, `${a.status}${a.duration_ms ? ` · ${(a.duration_ms / 1000).toFixed(0)} s` : ""}`)))));
  } catch (err) {
    list.replaceChildren(el("li", { class: "error" }, err.message));
  }
}

function describeCreate(e) {
  switch (e.type) {
    case "start": return [`Started (${e.kind})`];
    case "agent_start": return [`${e.agent} writing…`];
    case "agent_done": return [`${e.agent} finished: ${e.tokens ?? "?"} tokens in ${(e.duration_ms / 1000).toFixed(1)} s`];
    case "storyboard": return [`Storyboard "${e.title}": ${e.scenes.map((x) => x.caption).join(" → ")}`];
    case "stage": return [e.stage];
    case "progress": return [`… ${e.stage}: ${e.elapsed_s} s`];
    case "done": return [`Done in ${(e.duration_ms / 1000).toFixed(0)} s: ${e.name}`, "good"];
    case "end": return [`${e.status}${e.error ? `: ${e.error}` : ""}`, "bad"];
    default: return [null];
  }
}

async function runCreate(event) {
  event.preventDefault();
  const kind = createKind();
  const text = $("#create-input").value.trim();
  if (!text) return;
  $("#create-log").replaceChildren();
  $("#create-result").replaceChildren();
  $("#create-run").disabled = true;
  $("#create-cancel").disabled = false;
  creating = true;
  const body = kind === "image" ? { prompt: text } : { request: text };
  try {
    const response = await postStream(`/create/${kind}`, body);
    for await (const e of ndjson(response)) {
      const [line, cls] = describeCreate(e);
      if (line) {
        const log = $("#create-log");
        // Replace the previous heartbeat line instead of piling them up.
        if (e.type === "progress" && log.lastElementChild?.dataset.progress) log.lastElementChild.remove();
        const item = el("li", cls ? { class: cls } : {}, line);
        if (e.type === "progress") item.dataset.progress = "1";
        log.append(item);
      }
      if (e.type === "done") showArtifact({ ...e, prompt: text });
    }
  } catch (err) {
    $("#create-log").append(el("li", { class: "bad" }, err.message));
  } finally {
    creating = false;
    $("#create-run").disabled = false;
    $("#create-cancel").disabled = true;
    loadArtifacts();
    refreshStatus();
  }
}

async function cancelCreate() {
  if (!creating) return;
  $("#create-cancel").disabled = true;
  const r = await fetch("/create/cancel", { method: "POST" });
  const body = await r.json();
  $("#create-log").append(el("li", { class: "warn" }, r.ok ? `Cancel: ${body.status}` : body.message));
}

async function loadCaps() {
  try { caps = await getJSON("/media/status"); } catch { caps = {}; }
  $("#mic").title = caps.voice_input ? "Speak your message" : "Voice input not installed yet";
  updateCreateForm();
}

// ---------------------------------------------------------------- tasks

let runningTaskId = null;

async function loadTasks() {
  const list = $("#task-list");
  try {
    const { tasks } = await getJSON("/tasks");
    list.replaceChildren(...tasks.map((t) => el("li", {},
      el("button", { type: "button", onclick: () => showTask(t.id) },
        `#${t.id} ${t.request.slice(0, 60)}`,
        el("small", {}, `${t.status} · ${t.step_count} step(s)`)))));
  } catch (err) {
    list.replaceChildren(el("li", { class: "error" }, err.message));
  }
}

function logLine(text, cls) {
  const item = el("li", cls ? { class: cls } : {}, text);
  $("#task-log").append(item);
  item.scrollIntoView({ block: "nearest" });
}

function describe(e) {
  const s = (ms) => `${(ms / 1000).toFixed(1)} s`;
  switch (e.type) {
    case "task_start": return [`Task #${e.task_id} started`];
    case "agent_start": return [`${e.agent} working${e.step ? ` on step ${e.step}` : ""}…`];
    case "agent_done": return [`${e.agent} finished: ${e.tokens ?? "?"} tokens in ${s(e.duration_ms)}`, e.status === "ok" ? null : "bad"];
    case "plan": return [`Plan: ${e.steps.map((x) => x.title).join(" → ")}`];
    case "revise": return [`Step ${e.step} revision ${e.revision}: ${e.issues.join("; ")}`, "warn"];
    case "step_done": return [`Step ${e.step} done: ${e.verdict}, ${e.revisions} revision(s)`, e.verdict === "approve" ? "good" : "warn"];
    case "testing_start": return ["Writing project files and tests…"];
    case "test_run": return [`Test run ${e.round}: ${e.status}, ${e.passed} passed, ${e.failed} failed`, e.status === "passed" ? "good" : "bad"];
    case "fix": return [`Fixer rewrote the code (round ${e.round})`, "warn"];
    case "fix_rejected": return [`Fix rejected: ${e.problems.join("; ")}`, "bad"];
    case "testing_done": return [`Tests: ${e.test_status} ${e.note || ""}`, e.test_status === "passed" ? "good" : "bad"];
    case "task_end": return [`Task ${e.status}${e.error ? `: ${e.error}` : ""}`, e.status === "done" ? "good" : "bad"];
    default: return [null];
  }
}

async function runTask(event) {
  event.preventDefault();
  const request = $("#task-input").value.trim();
  if (!request) return;
  $("#task-log").replaceChildren();
  $("#task-detail").replaceChildren();
  $("#task-run").disabled = true;
  $("#task-cancel").disabled = false;
  try {
    const response = await postStream("/tasks", { request });
    for await (const e of ndjson(response)) {
      if (e.type === "task_start") runningTaskId = e.task_id;
      const [text, cls] = describe(e);
      if (text) logLine(text, cls);
      if (e.type === "step_done" || e.type === "task_end") loadTasks();
    }
    if (runningTaskId) await showTask(runningTaskId);
  } catch (err) {
    logLine(err.message, "bad");
  } finally {
    runningTaskId = null;
    $("#task-run").disabled = false;
    $("#task-cancel").disabled = true;
    refreshStatus();
  }
}

async function cancelTask() {
  if (!runningTaskId) return;
  $("#task-cancel").disabled = true;
  try {
    const r = await fetch(`/tasks/${runningTaskId}/cancel`, { method: "POST" });
    const body = await r.json();
    logLine(r.ok ? `Cancel: ${body.status} in ${body.stopped_in_ms} ms` : body.message, r.ok ? "warn" : "bad");
  } catch (err) {
    logLine(err.message, "bad");
  }
}

async function showTask(id) {
  const detail = $("#task-detail");
  try {
    const [task, files] = await Promise.all([getJSON(`/tasks/${id}`), getJSON(`/tasks/${id}/files`)]);
    const parts = [el("section", {},
      el("h3", {}, `Task #${task.id}: ${task.status}${task.test_status ? ` · tests ${task.test_status}` : ""}`),
      el("p", {}, task.request),
      task.error ? el("p", { class: "error" }, task.error) : null)];
    for (const step of task.steps) {
      parts.push(el("section", {},
        el("h3", {}, `Step ${step.number}: ${step.title}`),
        el("p", {}, step.description),
        el("p", { class: step.verdict === "approve" ? "good" : "warn" },
          `Reviewer: ${step.verdict ?? "-"} · ${step.revisions} revision(s)${step.issues.length ? ` · ${step.issues.join("; ")}` : ""}`),
        el("pre", {}, step.code || "")));
    }
    for (const run of task.test_runs || []) {
      parts.push(el("section", {}, el("h3", { class: run.status === "passed" ? "good" : "bad" },
        `Test run ${run.round}: ${run.status}`), el("pre", {}, run.output || "")));
    }
    for (const [name, text] of Object.entries(files.files || {})) {
      parts.push(el("section", {}, el("h3", {}, name), el("pre", {}, text)));
    }
    detail.replaceChildren(...parts);
  } catch (err) {
    detail.replaceChildren(el("p", { class: "error" }, err.message));
  }
}

// ---------------------------------------------------------------- start

function init() {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => showView(tab.dataset.view));
  }
  $("#new-chat").addEventListener("click", newChat);
  $("#chat-form").addEventListener("submit", sendChat);
  $("#chat-stop").addEventListener("click", () => chatAbort?.abort());
  $("#chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.ctrlKey) $("#chat-form").requestSubmit();
  });
  $("#task-form").addEventListener("submit", runTask);
  $("#mic").addEventListener("click", toggleMic);
  $("#create-form").addEventListener("submit", runCreate);
  $("#create-cancel").addEventListener("click", cancelCreate);
  for (const radio of document.querySelectorAll('input[name="kind"]')) radio.addEventListener("change", updateCreateForm);
  try { $("#speak-answers").checked = localStorage.getItem("speakAnswers") === "1"; } catch { /* storage blocked */ }
  $("#speak-answers").addEventListener("change", (e) => {
    try { localStorage.setItem("speakAnswers", e.target.checked ? "1" : "0"); } catch { /* storage blocked */ }
    if (!e.target.checked && "speechSynthesis" in window) speechSynthesis.cancel();
  });
  loadCaps();
  $("#task-cancel").addEventListener("click", cancelTask);
  refreshStatus();
  setInterval(refreshStatus, 15000);
  loadConversations();
  document.body.dataset.ready = "1"; // used by the acceptance check
}

init();
