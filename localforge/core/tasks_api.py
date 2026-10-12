"""HTTP endpoints for agent tasks (Phase 3)."""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agents.orchestrator import Orchestrator

from .config import Settings
from .db import Database
from .model_lock import ModelLock

log = logging.getLogger("localforge.tasks")
CANCEL_WAIT_S = 10


class TaskRequest(BaseModel):
    request: str = Field(min_length=1)
    model: str | None = None


def agent_summary(runs: list[dict]) -> dict:
    summary: dict[str, dict] = {}
    for r in runs:
        s = summary.setdefault(r["agent"], {"calls": 0, "tokens": 0, "duration_ms": 0})
        s["calls"] += 1
        s["tokens"] += r["tokens"] or 0
        s["duration_ms"] += r["duration_ms"] or 0
    return summary


def register_task_routes(app: FastAPI, settings: Settings, db: Database, lock: ModelLock) -> None:
    from .app import error, full_name, ndjson  # shared helpers

    interrupted = db.fail_interrupted_tasks()
    if interrupted:
        log.warning("Marked %d task(s) as failed: interrupted by a restart", interrupted)
    # The one running task: {"id", "handle", "queue"}. One model job at a time, so one task.
    app.state.running_task = None

    @app.post("/tasks")
    async def start_task(req: TaskRequest):
        text = req.request.strip()
        if len(text) > settings.max_task_chars:
            return error(413, "request_too_long",
                         f"Request is {len(text)} characters; the limit is {settings.max_task_chars}")
        if not lock.try_acquire("task"):
            return error(429, "busy", "The model is busy with another chat or task. Try again when it finishes.")
        model = full_name(req.model or settings.model)
        try:
            refused = await app.state.preflight(model, "Task")
        except BaseException:
            lock.release("task")
            raise
        if refused is not None:
            lock.release("task")
            return refused

        task_id = db.create_task(text, model)
        entry: dict = {"id": task_id, "queue": asyncio.Queue()}

        def emit(event: dict) -> None:
            queue = entry["queue"]
            if queue is not None:  # nobody listening any more: the task carries on regardless
                queue.put_nowait(event)

        orchestrator = Orchestrator(db, app.state.ollama, settings, model, emit, touch=lock.touch)

        async def runner():
            try:
                await orchestrator.run(task_id, text)
            except asyncio.CancelledError:
                pass
            finally:
                lock.release("task")
                app.state.running_task = None
                emit(None)  # end of stream

        entry["handle"] = asyncio.create_task(runner())
        app.state.running_task = entry
        log.info("Task %d started: %s", task_id, text[:80])

        async def stream():
            try:
                while True:
                    event = await entry["queue"].get()
                    if event is None:
                        return
                    yield ndjson(event)
            finally:
                entry["queue"] = None

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.get("/tasks")
    async def list_tasks():
        return {"tasks": db.list_tasks()}

    @app.get("/tasks/{task_id}")
    async def get_task(task_id: int):
        task = db.get_task(task_id)
        if task is None:
            return error(404, "not_found", f"Task {task_id} does not exist")
        task["agent_summary"] = agent_summary(task["agent_runs"])
        return task

    @app.get("/tasks/{task_id}/files")
    async def task_files(task_id: int):
        task = db.get_task(task_id)
        if task is None:
            return error(404, "not_found", f"Task {task_id} does not exist")
        files = {}
        if task.get("project_dir"):
            from pathlib import Path
            folder = Path(task["project_dir"])
            for name in ("solution.py", "test_solution.py"):
                f = folder / name
                if f.is_file():
                    files[name] = f.read_text(encoding="utf-8")
        return {"task_id": task_id, "project_dir": task.get("project_dir"), "files": files}

    @app.post("/tasks/{task_id}/cancel")
    async def cancel_task(task_id: int):
        running = app.state.running_task
        if running is None or running["id"] != task_id:
            if db.get_task(task_id) is None:
                return error(404, "not_found", f"Task {task_id} does not exist")
            return error(409, "not_running", f"Task {task_id} is not running")
        started = time.perf_counter()
        running["handle"].cancel()
        await asyncio.wait({running["handle"]}, timeout=CANCEL_WAIT_S)
        task = db.get_task(task_id)
        return {"task_id": task_id, "status": task["status"],
                "stopped_in_ms": round((time.perf_counter() - started) * 1000)}
