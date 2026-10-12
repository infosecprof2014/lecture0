"""HTTP endpoints for voice input and for creating files, images and videos (Phase 7)."""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agents.base import AgentError
from agents.creator import FileWriter, Storyboarder

from . import media
from .config import Settings
from .db import Database
from .model_lock import ModelLock
from .ollama_client import OllamaUnavailable
from .speech import Transcriber, VoiceUnavailable

log = logging.getLogger("localforge.media")
MB = 1024 * 1024
CANCEL_WAIT_S = 10
INLINE_TYPES = {".png": "image/png", ".mp4": "video/mp4"}


class FileRequest(BaseModel):
    request: str = Field(min_length=1, max_length=2000)


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    seed: int | None = None


class VideoRequest(BaseModel):
    request: str = Field(min_length=1, max_length=2000)


def register_media_routes(app: FastAPI, settings: Settings, db: Database, lock: ModelLock) -> None:
    from .app import error, full_name, ndjson

    if db.fail_interrupted_artifacts():
        log.warning("Marked interrupted creations as failed")
    transcriber = Transcriber(settings)
    app.state.transcriber = transcriber
    app.state.running_creation = None
    voice_lock = asyncio.Lock()
    out_dir = {kind: settings.projects_dir / folder for kind, folder in
               (("file", "files"), ("image", "images"), ("video", "videos"))}

    # ------------------------------------------------------------ status and voice

    @app.get("/media/status")
    async def media_status():
        return media.capabilities(settings)

    @app.post("/speech/transcribe")
    async def transcribe(request: Request):
        audio = await request.body()
        if not audio:
            return error(400, "no_audio", "No audio was received.")
        if len(audio) > settings.voice_max_mb * MB:
            return error(413, "audio_too_long", f"Recording is over {settings.voice_max_mb} MB; keep it under a minute.")
        async with voice_lock:
            try:
                result = await asyncio.to_thread(app.state.transcriber.transcribe, audio,
                                                 request.headers.get("content-type", "audio/webm"))
            except VoiceUnavailable as exc:
                return error(503, "voice_not_installed", str(exc))
            except Exception as exc:  # noqa: BLE001 - undecodable audio and the like
                log.warning("Transcription failed: %s", exc)
                return error(400, "bad_audio", f"Could not understand the recording: {exc}")
        log.info("Transcribed %.1f s of audio in %.1f s", result["audio_seconds"], result["seconds"])
        return result

    # ------------------------------------------------------------ shared job machinery

    async def free_memory_for_images() -> JSONResponse | None:
        """Unload the chat model (the image model needs the memory), then check free RAM."""
        try:
            for m in await app.state.ollama.loaded_models():
                await app.state.ollama.unload(m.get("name"))
        except OllamaUnavailable:
            pass
        free = app.state.free_ram()
        needed = (settings.ram_reserve_mb + settings.image_ram_mb) * MB
        if free < needed:
            return error(503, "low_memory", f"Not enough free memory to make images: {free // MB} MB free, "
                         f"{needed // MB} MB needed. Close some programs and try again.",
                         free_mb=free // MB, needed_mb=needed // MB)
        return None

    def start_job(kind: str, prompt: str,
                  work: Callable[..., Awaitable[tuple[Path, dict]]]):
        """`work(artifact_id, emit, blocking, cancel)` returns (output path, details)."""
        artifact_id = db.create_artifact(kind, prompt)
        entry: dict = {"id": artifact_id, "queue": asyncio.Queue(), "cancel": threading.Event()}

        def emit(event: dict | None) -> None:
            if entry["queue"] is not None:
                entry["queue"].put_nowait(event)

        async def blocking(stage: str, fn: Callable, *args):
            """Run slow work in a thread, sending a progress event every 5 s."""
            started = time.monotonic()
            future = asyncio.ensure_future(asyncio.to_thread(fn, *args))
            try:
                while True:
                    done, _ = await asyncio.wait({future}, timeout=5)
                    if done:
                        return future.result()
                    lock.touch()
                    emit({"type": "progress", "stage": stage, "elapsed_s": round(time.monotonic() - started)})
            except asyncio.CancelledError:
                entry["cancel"].set()  # stops the external program
                try:
                    await asyncio.wait_for(asyncio.shield(future), timeout=CANCEL_WAIT_S)
                except BaseException:  # noqa: BLE001 - already cancelling
                    pass
                raise

        async def runner():
            started = time.perf_counter()
            emit({"type": "start", "artifact_id": artifact_id, "kind": kind})
            try:
                path, meta = await work(artifact_id, emit, blocking, entry["cancel"])
                ms = round((time.perf_counter() - started) * 1000)
                db.finish_artifact(artifact_id, "done", path=str(path), meta=meta, duration_ms=ms)
                emit({"type": "done", "artifact_id": artifact_id, "kind": kind, "name": path.name,
                      "url": f"/artifacts/{artifact_id}/content", "meta": meta, "duration_ms": ms})
                log.info("Created %s #%d in %d ms: %s", kind, artifact_id, ms, path.name)
            except (asyncio.CancelledError, media.Cancelled):
                db.finish_artifact(artifact_id, "cancelled", error="Cancelled by the user")
                emit({"type": "end", "artifact_id": artifact_id, "status": "cancelled"})
            except (media.MediaError, AgentError, OllamaUnavailable) as exc:
                db.finish_artifact(artifact_id, "failed", error=str(exc))
                emit({"type": "end", "artifact_id": artifact_id, "status": "failed", "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                log.exception("Creation %d crashed", artifact_id)
                db.finish_artifact(artifact_id, "failed", error=f"Unexpected error: {exc}")
                emit({"type": "end", "artifact_id": artifact_id, "status": "failed", "error": f"Unexpected error: {exc}"})
            finally:
                lock.release("create")
                app.state.running_creation = None
                emit(None)

        entry["handle"] = asyncio.create_task(runner())
        app.state.running_creation = entry

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

    def agent_kwargs(emit: Callable[[dict], None]) -> dict:
        def record(agent: str, _input: str):
            emit({"type": "agent_start", "agent": agent})

            def finish(status: str, _output: str, tokens: int | None, duration_ms: int) -> None:
                emit({"type": "agent_done", "agent": agent, "status": status, "tokens": tokens,
                      "duration_ms": duration_ms})
            return finish
        return dict(temperature=settings.agent_temperature, record=record, before_call=lock.touch)

    async def acquire(need_model: bool, need_images: bool):
        if not lock.try_acquire("create"):
            return error(429, "busy", "The model is busy with another chat, task or creation. Try again soon.")
        try:
            refused = await app.state.preflight(full_name(settings.model), "Creation") if need_model else None
            if refused is None and need_images:
                refused = await free_memory_for_images()
        except BaseException:
            lock.release("create")
            raise
        if refused is not None:
            lock.release("create")
        return refused

    # ------------------------------------------------------------ files

    @app.post("/create/file")
    async def create_file(req: FileRequest):
        refused = await acquire(need_model=True, need_images=False)
        if refused is not None:
            return refused

        async def work(artifact_id, emit, blocking, cancel):
            writer = FileWriter(app.state.ollama, full_name(settings.model), max_tokens=settings.files_max_tokens,
                                max_kb=settings.files_max_kb, **agent_kwargs(emit))
            result = await writer.write(req.request.strip())
            path = media.save_new_file(out_dir["file"], result["filename"], result["content"])
            return path, {"filename": path.name, "bytes": path.stat().st_size}

        return start_job("file", req.request.strip(), work)

    # ------------------------------------------------------------ images

    @app.post("/create/image")
    async def create_image(req: ImageRequest):
        if not media.capabilities(settings)["images"]:
            return error(503, "images_not_installed", "Image generation is not installed. Run scripts\\install-phase7.ps1")
        refused = await acquire(need_model=False, need_images=True)
        if refused is not None:
            return refused

        async def work(artifact_id, emit, blocking, cancel):
            seed = req.seed if req.seed is not None else random.randint(1, 2**31 - 1)
            path = out_dir["image"] / f"image-{artifact_id}.png"
            emit({"type": "stage", "stage": "drawing the image (about 30 s to 2 min on this PC)"})
            info = await blocking("drawing", media.generate_image, settings, req.prompt.strip(), path, seed, cancel)
            return path, info

        return start_job("image", req.prompt.strip(), work)

    # ------------------------------------------------------------ videos

    @app.post("/create/video")
    async def create_video(req: VideoRequest):
        caps = media.capabilities(settings)
        if not caps["video"]:
            return error(503, "video_not_installed", "FFmpeg is not installed. Run scripts\\install-phase7.ps1")
        refused = await acquire(need_model=True, need_images=False)
        if refused is not None:
            return refused

        async def work(artifact_id, emit, blocking, cancel):
            boarder = Storyboarder(app.state.ollama, full_name(settings.model),
                                   max_tokens=settings.storyboard_max_tokens, **agent_kwargs(emit))
            board = await boarder.plan(req.request.strip(), settings.video_max_scenes)
            emit({"type": "storyboard", "title": board["title"], "scenes": board["scenes"]})
            folder = out_dir["video"] / f"video-{artifact_id}"
            folder.mkdir(parents=True, exist_ok=True)

            use_ai_images = caps["images"]
            if use_ai_images:
                refused = await free_memory_for_images()
                if refused is not None:
                    raise media.MediaError("Not enough free memory for the pictures; close some programs and try again.")
            scenes = []
            n = len(board["scenes"])
            for i, sc in enumerate(board["scenes"], 1):
                image = folder / f"scene{i:02d}.png"
                if use_ai_images:
                    emit({"type": "stage", "stage": f"picture {i} of {n}: {sc['caption']}"})
                    await blocking(f"picture {i}/{n}", media.generate_image, settings, sc["image_prompt"], image,
                                   random.randint(1, 2**31 - 1), cancel)
                else:
                    # No image model installed: a soft colour gradient background instead of a picture.
                    await blocking(f"background {i}/{n}", media.plain_background, settings, image, i, cancel)
                emit({"type": "stage", "stage": f"narration {i} of {n}"})
                audio = await blocking(f"narration {i}/{n}", media.narrate, sc["narration"],
                                       folder / f"scene{i:02d}.wav", cancel)
                scenes.append(media.Scene(image, sc["caption"], audio))
            emit({"type": "stage", "stage": "assembling the video"})
            out = folder / "video.mp4"
            info = await blocking("assembling", media.build_video, settings, scenes, out, cancel)
            info.update({"title": board["title"], "ai_images": use_ai_images,
                         "storyboard": board["scenes"]})
            return out, info

        return start_job("video", req.request.strip(), work)

    # ------------------------------------------------------------ cancel and results

    @app.post("/create/cancel")
    async def cancel_creation():
        running = app.state.running_creation
        if running is None:
            return error(409, "not_running", "Nothing is being created right now.")
        started = time.perf_counter()
        running["cancel"].set()
        running["handle"].cancel()
        await asyncio.wait({running["handle"]}, timeout=CANCEL_WAIT_S)
        art = db.get_artifact(running["id"])
        return {"artifact_id": running["id"], "status": art["status"],
                "stopped_in_ms": round((time.perf_counter() - started) * 1000)}

    @app.get("/artifacts")
    async def list_artifacts():
        return {"artifacts": [{k: a[k] for k in ("id", "kind", "prompt", "status", "error", "meta", "duration_ms",
                                                 "created_at")} | {"name": Path(a["path"]).name if a["path"] else None}
                              for a in db.list_artifacts()]}

    @app.get("/artifacts/{artifact_id}/content")
    async def artifact_content(artifact_id: int):
        art = db.get_artifact(artifact_id)
        if art is None or art["status"] != "done" or not art["path"]:
            return error(404, "not_found", f"Nothing to download for #{artifact_id}")
        path = Path(art["path"]).resolve()
        if settings.projects_dir.resolve() not in path.parents or not path.is_file():
            return error(404, "not_found", "The file is no longer there")
        inline = INLINE_TYPES.get(path.suffix.lower())
        headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"}
        if inline:
            return FileResponse(path, media_type=inline, headers=headers)
        # Created documents (including .html) are always downloaded, never shown inside this site.
        return FileResponse(path, media_type="application/octet-stream", filename=path.name, headers=headers)
