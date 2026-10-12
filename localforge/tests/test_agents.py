"""Fast tests for the agents and /tasks (Ollama is faked)."""

from __future__ import annotations

import ast
import asyncio
import json
import threading
import time

import pytest

from agents.base import load_prompt
from agents.coder import extract_code, syntax_problem
from agents.orchestrator import Orchestrator
from core.config import Settings
from core.db import Database
from core.ollama_client import OllamaClient

from .fake_ollama import FakeOllama

GOOD = "```python\ndef add(a, b):\n    \"\"\"Add.\"\"\"\n    return a + b\n```"
BAD = "```python\ndef add(a, b)\n    return a + b\n```"
APPROVE = json.dumps({"verdict": "approve", "issues": []})


def plan(*titles: str) -> str:
    return json.dumps({"steps": [{"title": t, "description": f"Write {t}"} for t in titles]})


def revise(issue: str = "Handle negative numbers") -> str:
    return json.dumps({"verdict": "revise", "issues": [issue]})


def run_task(client, request: str = "Write an add function") -> list[dict]:
    with client.stream("POST", "/tasks", json={"request": request}) as r:
        if r.headers.get("content-type", "").startswith("application/json"):
            r.read()
            return [{"http_status": r.status_code, **r.json()}]
        assert r.status_code == 200
        return [json.loads(line) for line in r.iter_lines() if line.strip()]


def agents_called(fake: FakeOllama) -> list[str]:
    out = []
    for req in fake.chat_requests:
        system = req["messages"][0]["content"]
        out.append("planner" if "the Planner" in system else "coder" if "the Coder" in system else "reviewer")
    return out


# ---------------------------------------------------------------- helpers

def test_extract_code_and_syntax_check():
    assert extract_code(GOOD).startswith("def add")
    assert extract_code("def f():\n    return 1") == "def f():\n    return 1"
    assert extract_code("```python\ndef f():\n    return 1") == "def f():\n    return 1"  # cut-off fence
    assert syntax_problem(extract_code(GOOD)) is None
    assert "SyntaxError on line 1" in syntax_problem(extract_code(BAD))
    assert syntax_problem("") == "the answer contained no code"


def test_prompt_files_load():
    assert "between 1 and 5 steps" in load_prompt("planner", max_steps=5)
    assert "the Coder" in load_prompt("coder")
    assert '"verdict"' in load_prompt("reviewer")


# ---------------------------------------------------------------- full task over HTTP

def test_task_happy_path(client, fake):
    fake.scripted = [plan("add function", "sub function"), GOOD, APPROVE, GOOD, APPROVE]
    events = run_task(client)
    types = [e["type"] for e in events]
    assert types[0] == "task_start" and types[-1] == "task_end"
    assert events[-1]["status"] == "done"
    assert [e["agent"] for e in events if e["type"] == "agent_start"] == \
        ["planner", "coder", "reviewer", "coder", "reviewer"]
    # planner and reviewer asked for structured JSON; coder did not
    assert ["format" in r for r in fake.chat_requests] == [True, False, True, False, True]

    task = client.get(f"/tasks/{events[0]['task_id']}").json()
    assert task["status"] == "done" and len(task["steps"]) == 2
    for step in task["steps"]:
        assert step["verdict"] == "approve" and step["revisions"] == 0
        ast.parse(step["code"])
    assert len(task["agent_runs"]) == 5 and all(r["status"] == "ok" for r in task["agent_runs"])
    assert task["agent_summary"]["coder"]["calls"] == 2


def test_reviewer_revise_then_approve(client, fake):
    fake.scripted = [plan("add"), GOOD, revise("Handle None"), GOOD, APPROVE]
    events = run_task(client)
    assert events[-1]["status"] == "done"
    assert [e for e in events if e["type"] == "revise"][0]["issues"] == ["Handle None"]
    coder_retry = fake.chat_requests[3]["messages"][1]["content"]
    assert "Handle None" in coder_retry and "Your previous code" in coder_retry
    step = client.get(f"/tasks/{events[0]['task_id']}").json()["steps"][0]
    assert step["verdict"] == "approve" and step["revisions"] == 1


def test_revisions_capped(client, fake, settings):
    fake.scripted = [plan("add")] + [GOOD, revise()] * (settings.max_revisions + 1)
    events = run_task(client)
    assert events[-1]["status"] == "done"
    step = client.get(f"/tasks/{events[0]['task_id']}").json()["steps"][0]
    assert step["revisions"] == settings.max_revisions
    assert step["verdict"] == "revise" and step["issues"] == ["Handle negative numbers"]
    assert agents_called(fake).count("coder") == settings.max_revisions + 1


def test_syntax_error_skips_reviewer(client, fake):
    fake.scripted = [plan("add"), BAD, GOOD, APPROVE]
    events = run_task(client)
    assert agents_called(fake) == ["planner", "coder", "coder", "reviewer"]
    assert "SyntaxError" in fake.chat_requests[2]["messages"][1]["content"]
    step = client.get(f"/tasks/{events[0]['task_id']}").json()["steps"][0]
    assert step["verdict"] == "approve" and step["revisions"] == 1


def test_earlier_code_passed_to_next_step(client, fake):
    fake.scripted = [plan("add", "use add"), GOOD, APPROVE, GOOD, APPROVE]
    run_task(client)
    second_coder = fake.chat_requests[3]["messages"][1]["content"]
    assert "Code already written for earlier steps" in second_coder and "def add" in second_coder


def test_plan_truncated_to_max_steps(client, fake, settings):
    n = settings.max_plan_steps
    fake.scripted = [plan(*[f"s{i}" for i in range(n + 3)])] + [GOOD, APPROVE] * n
    events = run_task(client)
    assert next(e for e in events if e["type"] == "plan")["truncated"] is True
    assert len(client.get(f"/tasks/{events[0]['task_id']}").json()["steps"]) == n


def test_planner_bad_json_retried_then_fails(client, fake):
    fake.scripted = ["not json", json.dumps({"steps": []})]
    events = run_task(client)
    assert events[-1]["status"] == "failed"
    assert "planner gave an unusable answer twice" in events[-1]["error"]
    task = client.get(f"/tasks/{events[0]['task_id']}").json()
    assert task["status"] == "failed" and len(task["agent_runs"]) == 2


def test_planner_bad_json_once_recovers(client, fake):
    fake.scripted = ["{oops", plan("add"), GOOD, APPROVE]
    events = run_task(client)
    assert events[-1]["status"] == "done"
    assert "could not be used" in fake.chat_requests[1]["messages"][1]["content"]


def test_ollama_down_refuses_task(client, fake):
    fake.running = False
    events = run_task(client)
    assert events[0]["http_status"] == 503 and events[0]["error"] == "ollama_unavailable"
    assert client.app.state.model_lock.busy is False


def test_ollama_drops_mid_task_fails_cleanly(client, fake):
    fake.scripted = [plan("add")]
    fake.fail_after = 0  # every later call breaks off
    events = run_task(client)
    assert events[-1]["status"] == "failed" and "Ollama" in events[-1]["error"]
    assert client.app.state.model_lock.busy is False


# ---------------------------------------------------------------- one model job at a time

def test_chat_busy_while_task_runs_and_task_busy_while_chat(client):
    lock = client.app.state.model_lock
    assert lock.try_acquire("task")
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 429 and r.json()["error"] == "busy"
    lock.release("task")
    assert lock.try_acquire("chat")
    events = run_task(client)
    assert events[0]["http_status"] == 429
    lock.release("chat")


def test_lock_released_after_task(client, fake):
    fake.scripted = [plan("add"), GOOD, APPROVE]
    run_task(client)
    assert client.app.state.model_lock.busy is False
    status = client.post("/chat", json={"message": "hi"}).status_code
    assert status == 200


# ---------------------------------------------------------------- cancel

def test_cancel_over_http(client, fake):
    fake.scripted = [plan("add"), GOOD, APPROVE]
    fake.chunk_delay_s = 0.5  # each model call takes a while
    collected: list[dict] = []
    worker = threading.Thread(target=lambda: collected.extend(run_task(client)))
    worker.start()
    for _ in range(100):
        if client.app.state.running_task is not None:
            break
        time.sleep(0.02)
    task_id = client.app.state.running_task["id"]
    started = time.perf_counter()
    r = client.post(f"/tasks/{task_id}/cancel")
    elapsed = time.perf_counter() - started
    worker.join(timeout=10)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert elapsed < 10
    assert collected[-1] == {"type": "task_end", "task_id": task_id, "status": "cancelled"}
    task = client.get(f"/tasks/{task_id}").json()
    assert task["status"] == "cancelled"
    assert task["agent_runs"][-1]["status"] == "cancelled"
    assert client.app.state.model_lock.busy is False


def test_cancel_unknown_or_finished(client, fake):
    assert client.post("/tasks/999/cancel").status_code == 404
    fake.scripted = [plan("add"), GOOD, APPROVE]
    events = run_task(client)
    r = client.post(f"/tasks/{events[0]['task_id']}/cancel")
    assert r.status_code == 409 and r.json()["error"] == "not_running"


# ---------------------------------------------------------------- persistence and validation

def test_task_survives_restart(make_client, settings, fake):
    first = make_client()
    fake.scripted = [plan("add"), GOOD, APPROVE]
    task_id = run_task(first)[0]["task_id"]
    before = first.get(f"/tasks/{task_id}").json()
    second = make_client(settings)
    after = second.get(f"/tasks/{task_id}").json()
    assert after["steps"] == before["steps"] and len(after["agent_runs"]) == len(before["agent_runs"])
    assert task_id in [t["id"] for t in second.get("/tasks").json()["tasks"]]


def test_interrupted_task_marked_failed_on_start(make_client, settings):
    db = Database(settings.db_file)
    task_id = db.create_task("left running", "llama3.2:3b")
    client = make_client(settings)
    task = client.get(f"/tasks/{task_id}").json()
    assert task["status"] == "failed" and "Interrupted" in task["error"]


def test_task_request_validation(client, settings):
    assert client.post("/tasks", json={"request": ""}).status_code == 422
    r = client.post("/tasks", json={"request": "x" * (settings.max_task_chars + 1)})
    assert r.status_code == 413
    assert client.get("/tasks/12345").status_code == 404


def test_orchestrator_cancel_directly(settings, tmp_path):
    """Cancel while a model call is in flight: the run is recorded as cancelled."""
    fake = FakeOllama()
    fake.scripted = [plan("add")]
    fake.chunk_delay_s = 1.0

    async def scenario():
        db = Database(settings.db_file)
        ollama = OllamaClient(settings.ollama_url, 30, fake.transport())
        task_id = db.create_task("x", "llama3.2:3b")
        events: list[dict] = []
        orch = Orchestrator(db, ollama, settings, "llama3.2:3b", events.append)
        handle = asyncio.create_task(orch.run(task_id, "x"))
        await asyncio.sleep(0.2)
        handle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await handle
        await ollama.aclose()
        return db.get_task(task_id), events

    task, events = asyncio.run(scenario())
    assert task["status"] == "cancelled"
    assert task["agent_runs"][0]["status"] == "cancelled"
    assert events[-1]["status"] == "cancelled"


def test_new_settings_validated(tmp_path):
    from core.config import SettingsError, load_settings
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"agent_temperature": 0}), encoding="utf-8")
    assert load_settings(p).agent_temperature == 0.0
    p.write_text(json.dumps({"max_revisions": -1}), encoding="utf-8")
    with pytest.raises(SettingsError):
        load_settings(p)
    assert Settings().max_plan_steps == 5
