"""Phase 7: voice input, creating files, images and videos (model, image program and Whisper faked)."""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from core import media
from core.media import Scene, check_new_file, run_program, save_new_file

from .conftest import GB

PNG_1PX = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def stream(client, path: str, body: dict) -> list[dict]:
    with client.stream("POST", path, json=body) as r:
        if r.headers.get("content-type", "").startswith("application/json"):
            r.read()
            return [{"http_status": r.status_code, **r.json()}]
        return [json.loads(line) for line in r.iter_lines() if line.strip()]


@pytest.fixture
def mclient(make_client, settings, tmp_path, monkeypatch):
    """Client whose settings point at dummy image tools, with image drawing faked."""
    (tmp_path / "sd.exe").write_bytes(b"x")
    (tmp_path / "model.gguf").write_bytes(b"x")
    s = replace(settings, sd_binary=str(tmp_path / "sd.exe"), sd_model=str(tmp_path / "model.gguf"))

    def fake_image(_s, prompt, out, seed, cancel=None):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(PNG_1PX)
        return {"seconds": 0.1, "seed": seed, "size": 512, "steps": 1, "model": "model.gguf", "prompt": prompt}
    monkeypatch.setattr(media, "generate_image", fake_image)
    return make_client(s)


# ---------------------------------------------------------------- file safety rules

@pytest.mark.parametrize("name, content, problem", [
    ("notes.md", "# Hi", None),
    ("data.csv", "a,b\n1,2\n", None),
    ("data.json", '{"a": 1}', None),
    ("../evil.md", "x", "plain name"),
    ("C:\\Windows\\evil.md", "x", "plain name"),
    ("run.exe", "x", "not allowed"),
    ("script.ps1", "x", "not allowed"),
    ("noext", "x", "not allowed"),
    ("bad.json", "{oops", "not valid JSON"),
    ("bad.csv", "a,b\n1\n", "same number of columns"),
    ("empty.txt", "   ", "empty"),
])
def test_check_new_file(name, content, problem):
    result = check_new_file(name, content, max_kb=200)
    assert (result is None) if problem is None else (problem in result)


def test_save_never_overwrites(tmp_path):
    a = save_new_file(tmp_path / "files", "report.md", "one")
    b = save_new_file(tmp_path / "files", "report.md", "two")
    assert (a.name, b.name) == ("report.md", "report-2.md") and a.read_text() == "one"


def test_run_program_timeout_and_cancel():
    with pytest.raises(media.MediaError, match="longer than"):
        run_program([sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=1)
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(media.Cancelled):
        run_program([sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=60, cancel=cancel)
    assert time.monotonic() - started < 5
    with pytest.raises(media.MediaError, match="exit 3"):
        run_program([sys.executable, "-c", "import sys; sys.exit(3)"], timeout_s=10)


# ---------------------------------------------------------------- creating files

def test_create_file(client, fake, settings):
    fake.scripted = [json.dumps({"filename": "groceries.csv", "content": "item,qty\nmilk,2\neggs,12\n"})]
    events = stream(client, "/create/file", {"request": "a grocery list as CSV"})
    done = events[-1]
    assert done["type"] == "done" and done["meta"]["filename"] == "groceries.csv"
    saved = settings.projects_dir / "files" / "groceries.csv"
    assert saved.read_text(encoding="utf-8") == "item,qty\nmilk,2\neggs,12\n"
    r = client.get(done["url"])
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]


def test_create_file_rejects_unsafe_name_then_recovers(client, fake, settings):
    fake.scripted = [json.dumps({"filename": "../../evil.md", "content": "x"}),
                     json.dumps({"filename": "safe.md", "content": "# ok"})]
    events = stream(client, "/create/file", {"request": "a note"})
    assert events[-1]["type"] == "done" and events[-1]["meta"]["filename"] == "safe.md"
    assert "plain name" in fake.chat_requests[1]["messages"][1]["content"]
    assert not (settings.projects_dir.parent / "evil.md").exists()


def test_html_is_downloaded_not_rendered(client, fake):
    fake.scripted = [json.dumps({"filename": "page.html", "content": "<script>alert(1)</script>"})]
    done = stream(client, "/create/file", {"request": "a web page"})[-1]
    r = client.get(done["url"])
    assert r.headers["content-type"] == "application/octet-stream"
    assert "attachment" in r.headers["content-disposition"] and r.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------------- images

def test_create_image(mclient, settings):
    events = stream(mclient, "/create/image", {"prompt": "a red fox in snow", "seed": 42})
    done = events[-1]
    assert done["type"] == "done" and done["meta"]["seed"] == 42
    r = mclient.get(done["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == PNG_1PX


def test_image_not_installed(client):
    r = client.post("/create/image", json={"prompt": "x"})
    assert r.status_code == 503 and r.json()["error"] == "images_not_installed"


def test_image_low_memory_refused_and_chat_model_unloaded(mclient, fake, ram):
    fake.loaded = ["llama3.2:3b"]
    ram["bytes"] = 1 * GB
    r = mclient.post("/create/image", json={"prompt": "x"})
    assert r.status_code == 503 and r.json()["error"] == "low_memory"
    assert fake.unloaded == ["llama3.2:3b"]  # it tried to free memory first
    assert mclient.app.state.model_lock.busy is False


def test_cancel_image(mclient, monkeypatch):
    def slow_image(_s, prompt, out, seed, cancel=None):
        for _ in range(100):
            if cancel is not None and cancel.is_set():
                raise media.Cancelled("cancelled")
            time.sleep(0.05)
        raise AssertionError("not cancelled")
    monkeypatch.setattr(media, "generate_image", slow_image)
    got: list[dict] = []
    worker = threading.Thread(target=lambda: got.extend(stream(mclient, "/create/image", {"prompt": "x"})))
    worker.start()
    for _ in range(100):
        if mclient.app.state.running_creation is not None:
            break
        time.sleep(0.02)
    r = mclient.post("/create/cancel")
    worker.join(timeout=10)
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["stopped_in_ms"] < 10000
    assert got[-1]["status"] == "cancelled"
    assert mclient.app.state.model_lock.busy is False


def test_busy_rules_shared_with_chat(mclient):
    lock = mclient.app.state.model_lock
    assert lock.try_acquire("chat")
    assert mclient.post("/create/image", json={"prompt": "x"}).status_code == 429
    lock.release("chat")


# ---------------------------------------------------------------- videos

needs_ffmpeg = pytest.mark.skipif(media.ffmpeg_binary(media.Settings()) is None, reason="FFmpeg not installed")


@needs_ffmpeg
def test_build_video_real_ffmpeg(tmp_path):
    from core.config import Settings
    s = Settings()
    ff = media.ffmpeg_binary(s)
    imgs = []
    for i, src in enumerate(("testsrc2", "smptebars")):
        p = tmp_path / f"{i}.png"
        run_program([ff, "-y", "-f", "lavfi", "-i", f"{src}=s=512x512", "-frames:v", "1", p], timeout_s=60)
        imgs.append(p)
    wav = tmp_path / "n.wav"
    run_program([ff, "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=2", wav], timeout_s=60)
    out = tmp_path / "v" / "video.mp4"
    info = media.build_video(s, [Scene(imgs[0], "Hello", wav), Scene(imgs[1], "World", None)], out)
    assert out.is_file() and info["narrated"] and info["scenes"] == 2
    assert 6.0 < media.media_seconds(ff, out) < 8.5  # 2 s narration + 0.8, then a 4 s silent scene
    assert "00:00:00,000 --> " in out.with_suffix(".srt").read_text(encoding="utf-8")


@needs_ffmpeg
def test_create_video_end_to_end(mclient, fake, monkeypatch):
    board = {"title": "Fox", "scenes": [
        {"image_prompt": "a fox at dawn, cinematic, highly detailed", "caption": "Dawn", "narration": "The fox wakes."},
        {"image_prompt": "a fox running, cinematic, highly detailed", "caption": "Run", "narration": "It runs."}]}
    fake.scripted = [json.dumps(board)]

    def fake_image(_s, prompt, out, seed, cancel=None):  # real PNG big enough for the zoom filter
        ff = media.ffmpeg_binary(_s)
        run_program([ff, "-y", "-f", "lavfi", "-i", "testsrc2=s=512x512", "-frames:v", "1", out], timeout_s=60)
        return {}
    monkeypatch.setattr(media, "generate_image", fake_image)
    monkeypatch.setattr(media, "narrate", lambda text, out, cancel=None: None)  # silent
    events = stream(mclient, "/create/video", {"request": "a fox's morning"})
    types = [e["type"] for e in events]
    assert "storyboard" in types and types[-1] == "done", events[-1]
    done = events[-1]
    assert done["meta"]["scenes"] == 2 and done["meta"]["title"] == "Fox"
    r = mclient.get(done["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and len(r.content) > 1000
    storyboard_req = fake.chat_requests[0]
    assert "format" in storyboard_req and "between 1 and 4 scenes" in storyboard_req["messages"][0]["content"]


def test_video_not_installed(client, monkeypatch):
    monkeypatch.setattr(media, "ffmpeg_binary", lambda s: None)
    r = client.post("/create/video", json={"request": "x"})
    assert r.status_code == 503 and r.json()["error"] == "video_not_installed"


# ---------------------------------------------------------------- voice

class FakeTranscriber:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio: bytes, content_type: str) -> dict:
        self.calls.append((len(audio), content_type))
        return {"text": "what is two plus two", "language": "en", "audio_seconds": 1.5, "seconds": 0.4}


def test_transcribe(client):
    fake_t = FakeTranscriber()
    client.app.state.transcriber = fake_t
    r = client.post("/speech/transcribe", content=b"RIFF....fake", headers={"content-type": "audio/webm;codecs=opus"})
    assert r.status_code == 200 and r.json()["text"] == "what is two plus two"
    assert fake_t.calls == [(12, "audio/webm;codecs=opus")]


def test_transcribe_limits_and_not_installed(client, settings):
    assert client.post("/speech/transcribe", content=b"").status_code == 400
    big = b"x" * (settings.voice_max_mb * 1024 * 1024 + 1)
    assert client.post("/speech/transcribe", content=big).status_code == 413
    r = client.post("/speech/transcribe", content=b"abc", headers={"content-type": "audio/webm"})
    assert r.status_code in (400, 503)  # 503 when Whisper is not installed on this machine


def test_media_status_and_artifact_listing(mclient, fake):
    status = mclient.get("/media/status").json()
    assert status["images"] is True and status["files"] is True
    fake.scripted = [json.dumps({"filename": "a.md", "content": "# A"})]
    stream(mclient, "/create/file", {"request": "a"})
    items = mclient.get("/artifacts").json()["artifacts"]
    assert items[0]["kind"] == "file" and items[0]["name"] == "a.md" and items[0]["status"] == "done"
    assert mclient.get("/artifacts/999/content").status_code == 404


def test_artifact_outside_projects_not_served(client, settings, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    aid = client.app.state.db.create_artifact("file", "x")
    client.app.state.db.finish_artifact(aid, "done", path=str(secret))
    assert client.get(f"/artifacts/{aid}/content").status_code == 404


def test_cross_site_creation_refused(client):
    r = client.post("/create/file", json={"request": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
