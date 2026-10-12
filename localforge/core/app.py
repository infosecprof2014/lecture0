"""LocalForge AI core service: the HTTP layer between the user and Ollama."""

from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings
from .db import Database
from .memory_guard import free_ram_bytes
from .model_lock import ModelLock
from .ollama_client import OllamaClient, OllamaUnavailable

log = logging.getLogger("localforge.core")
MB = 1024 * 1024
UI_DIR = Path(__file__).resolve().parent.parent / "ui"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: int | None = None
    model: str | None = None


def full_name(model: str) -> str:
    """Ollama treats 'llama3' as 'llama3:latest'."""
    return model if ":" in model else f"{model}:latest"


def error(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message, **extra})


def ndjson(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def create_app(
    settings: Settings,
    *,
    ollama_transport: httpx.AsyncBaseTransport | None = None,
    free_ram: Callable[[], int] = free_ram_bytes,
    extra_hosts: tuple[str, ...] = (),
) -> FastAPI:
    db = Database(settings.db_file)
    allowed_hosts = LOCAL_HOSTS | set(extra_hosts)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ollama = OllamaClient(settings.ollama_url, settings.request_timeout_s, ollama_transport)
        log.info("LocalForge core %s started on %s:%s (model %s, db %s)",
                 __version__, settings.host, settings.port, settings.model, settings.db_file)
        yield
        await app.state.ollama.aclose()
        log.info("LocalForge core stopped")

    app = FastAPI(title="LocalForge AI core", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.free_ram = free_ram
    # One model job at a time, shared by /chat and agent tasks.
    lock = ModelLock(settings.request_timeout_s)
    app.state.model_lock = lock

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        """Browser protections now that a web page talks to this API.

        - Host check: a web page on another site cannot reach this service through DNS rebinding.
        - Origin check: a page on another site cannot make your browser start chats or tasks here.
        """
        host = urlsplit("//" + request.headers.get("host", "")).hostname or ""
        if host not in allowed_hosts:
            log.warning("Refused request with Host %r", request.headers.get("host"))
            return error(400, "bad_host", "This service only answers on 127.0.0.1 or localhost.")
        origin = request.headers.get("origin")
        if origin and request.method not in SAFE_METHODS:
            o = urlsplit(origin)
            if o.hostname not in allowed_hosts or (o.port or 80) != settings.port:
                log.warning("Refused %s %s from origin %r", request.method, request.url.path, origin)
                return error(403, "bad_origin", "Requests from other websites are not allowed.")
        return await call_next(request)

    if UI_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

        @app.get("/", include_in_schema=False)
        async def index():
            return FileResponse(UI_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("Unexpected error on %s %s", request.method, request.url.path)
        return error(500, "internal_error", "Unexpected error; details are in logs/core.log")

    @app.get("/health")
    async def health():
        ollama: OllamaClient = app.state.ollama
        info = {
            "status": "ok",
            "version": __version__,
            "configured_model": settings.model,
            "free_ram_mb": round(free_ram() / MB),
            "busy": lock.busy,
            "ollama": {"url": settings.ollama_url, "reachable": False},
        }
        try:
            info["ollama"]["version"] = await ollama.version()
            info["ollama"]["loaded_models"] = [m.get("name") for m in await ollama.loaded_models()]
            info["ollama"]["reachable"] = True
        except OllamaUnavailable as exc:
            info["ollama"]["message"] = str(exc)
        return info

    @app.get("/models")
    async def models():
        try:
            installed = await app.state.ollama.installed_models()
        except OllamaUnavailable as exc:
            return error(503, "ollama_unavailable", str(exc))
        return {"models": [
            {"name": m.get("name"), "size_mb": round(m.get("size", 0) / MB),
             "parameter_size": m.get("details", {}).get("parameter_size"),
             "quantization": m.get("details", {}).get("quantization_level")}
            for m in installed
        ]}

    @app.get("/conversations")
    async def conversations():
        return {"conversations": db.list_conversations()}

    @app.get("/conversations/{conversation_id}")
    async def conversation(conversation_id: int):
        conv = db.get_conversation(conversation_id)
        if conv is None:
            return error(404, "not_found", f"Conversation {conversation_id} does not exist")
        return conv

    @app.post("/chat")
    async def chat(req: ChatRequest):
        if len(req.message) > settings.max_message_chars:
            return error(413, "message_too_long",
                         f"Message is {len(req.message)} characters; the limit is {settings.max_message_chars}")
        if not lock.try_acquire("chat"):
            return error(429, "busy", "The model is busy with another chat or task. Try again when it finishes.")
        handed_off = False
        try:
            response = await _prepare_chat(req)
            handed_off = isinstance(response, StreamingResponse)
            return response
        finally:
            # The stream releases the flag itself when it ends.
            if not handed_off:
                lock.release("chat")

    async def preflight(model: str, purpose: str) -> JSONResponse | None:
        """Checks before using the model: Ollama up, model installed, one model loaded, enough RAM."""
        ollama: OllamaClient = app.state.ollama
        try:
            installed = {m.get("name"): m for m in await ollama.installed_models()}
            loaded = [m.get("name") for m in await ollama.loaded_models()]
        except OllamaUnavailable as exc:
            log.warning("%s refused: %s", purpose, exc)
            return error(503, "ollama_unavailable", ollama.not_running_message())

        if model not in installed:
            return error(404, "model_not_installed",
                         f"Model {model} is not installed. Run: ollama pull {model}",
                         installed=sorted(installed))

        # Keep only one model in memory.
        for other in loaded:
            if other != model:
                log.info("Unloading %s to keep one model in memory", other)
                try:
                    await ollama.unload(other)
                except OllamaUnavailable as exc:
                    log.warning("%s", exc)

        reserve = settings.ram_reserve_mb * MB
        needed = reserve if model in loaded else reserve + int(installed[model].get("size", 0))
        free = free_ram()
        if free < needed:
            log.warning("%s refused: %d MB free, %d MB needed", purpose, free // MB, needed // MB)
            return error(503, "low_memory",
                         f"Not enough free memory to run {model} safely: {free // MB} MB free, "
                         f"{needed // MB} MB needed. Close some programs and try again.",
                         free_mb=free // MB, needed_mb=needed // MB)
        return None

    app.state.preflight = preflight

    async def _prepare_chat(req: ChatRequest):
        model = full_name(req.model or settings.model)
        if req.conversation_id is not None and not db.conversation_exists(req.conversation_id):
            return error(404, "not_found", f"Conversation {req.conversation_id} does not exist")
        refused = await preflight(model, "Chat")
        if refused is not None:
            return refused

        conv_id = req.conversation_id or db.create_conversation(req.message.strip(), model)
        db.add_message(conv_id, "user", req.message)
        history = db.history(conv_id, settings.max_history_messages)
        reply_id = db.add_message(conv_id, "assistant", "", status="incomplete")
        log.info("Chat start: conversation %d, model %s, %d history message(s)", conv_id, model, len(history))
        return StreamingResponse(_stream(conv_id, reply_id, model, history), media_type="application/x-ndjson")

    async def _stream(conv_id: int, reply_id: int, model: str, history: list[dict]):
        ollama: OllamaClient = app.state.ollama
        parts: list[str] = []
        started = time.perf_counter()
        status = "incomplete"
        try:
            yield ndjson({"type": "start", "conversation_id": conv_id, "message_id": reply_id, "model": model})
            async for chunk in ollama.chat_stream(model, history):
                text = chunk.get("message", {}).get("content", "")
                if text:
                    parts.append(text)
                    yield ndjson({"type": "token", "content": text})
                if chunk.get("done"):
                    tokens = chunk.get("eval_count")
                    eval_ns = chunk.get("eval_duration") or 0
                    duration_ms = round((time.perf_counter() - started) * 1000)
                    status = "complete"
                    db.finish_message(reply_id, "".join(parts), status, tokens, duration_ms)
                    log.info("Chat done: conversation %d, %s tokens in %d ms", conv_id, tokens, duration_ms)
                    yield ndjson({
                        "type": "done", "conversation_id": conv_id, "message_id": reply_id,
                        "tokens": tokens, "duration_ms": duration_ms,
                        "tokens_per_s": round(tokens / (eval_ns / 1e9), 1) if tokens and eval_ns else None,
                    })
        except OllamaUnavailable as exc:
            status = "error"
            log.warning("Chat failed mid-answer: %s", exc)
            yield ndjson({"type": "error", "error": "ollama_unavailable", "message": str(exc)})
        finally:
            # Also reached when the client disconnects: keep whatever arrived.
            if status != "complete":
                db.finish_message(reply_id, "".join(parts), status,
                                  None, round((time.perf_counter() - started) * 1000))
            lock.release("chat")

    from .tasks_api import register_task_routes
    register_task_routes(app, settings, db, lock)
    from .media_api import register_media_routes
    register_media_routes(app, settings, db, lock)
    return app
