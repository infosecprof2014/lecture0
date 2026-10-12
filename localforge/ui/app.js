// LocalForge AI web interface. Plain JavaScript, no build step, nothing loaded from the internet.
// All model output is inserted with textContent (never innerHTML).
"use strict";

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
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
  $("#view-chat").hidden = name !== "chat";
  $("#view-tasks").hidden = name !== "tasks";
  if (name === "tasks") loadTasks();
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
  $("#task-cancel").addEventListener("click", cancelTask);
  refreshStatus();
  setInterval(refreshStatus, 15000);
  loadConversations();
  document.body.dataset.ready = "1"; // used by the acceptance check
}

init();
