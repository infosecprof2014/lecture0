"""Local media tools: safe file creation, images (stable-diffusion.cpp), narration (Windows voices),
and slideshow videos (FFmpeg). Every external program runs with a time limit and can be cancelled."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT, Settings

ALLOWED_EXTENSIONS = {".md", ".txt", ".csv", ".json", ".html", ".py", ".xml", ".yaml", ".yml", ".ini", ".sql"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,79}$")
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class MediaError(Exception):
    pass


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- finding tools

def _find(configured: str, folder: Path, names: tuple[str, ...]) -> Path | None:
    if configured:
        p = Path(configured)
        p = p if p.is_absolute() else ROOT / p
        return p if p.is_file() else None
    if folder.is_dir():
        for name in names:
            hits = sorted(folder.rglob(name))
            if hits:
                return hits[0]
    for name in names:  # also allow tools on PATH (e.g. ffmpeg)
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def sd_binary(s: Settings) -> Path | None:
    names = ("sd-cli.exe", "sd.exe") if sys.platform == "win32" else ("sd-cli", "sd")
    return _find(s.sd_binary, ROOT / "tools" / "sd", names)


def sd_model(s: Settings) -> Path | None:
    if s.sd_model:
        p = s.resolve(s.sd_model)
        return p if p.is_file() else None
    folder = ROOT / "models" / "sd"
    for pattern in ("*q8_0*.gguf", "*.gguf", "*.safetensors"):
        hits = sorted(folder.glob(pattern)) if folder.is_dir() else []
        if hits:
            return hits[0]
    return None


def ffmpeg_binary(s: Settings) -> Path | None:
    return _find(s.ffmpeg_binary, ROOT / "tools" / "ffmpeg", ("ffmpeg.exe",) if sys.platform == "win32" else ("ffmpeg",))


def whisper_ready(s: Settings) -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return (s.resolve(s.whisper_model_dir) / "model.bin").is_file()


def tts_available() -> bool:
    return sys.platform == "win32" or bool(shutil.which("espeak-ng") or shutil.which("espeak"))


def capabilities(s: Settings) -> dict:
    return {
        "voice_input": whisper_ready(s),
        "voice_output": True,  # the browser speaks with the system's voices
        "files": True,
        "images": bool(sd_binary(s) and sd_model(s)),
        "video": bool(ffmpeg_binary(s)),
        "video_images": bool(sd_binary(s) and sd_model(s)),
        "narration": tts_available(),
    }


# ---------------------------------------------------------------- running programs

def run_program(cmd: list[str], *, timeout_s: float, cancel: threading.Event | None = None,
                cwd: Path | None = None, env: dict | None = None) -> str:
    """Run a program; stop it on timeout or when `cancel` is set. Returns its output."""
    proc = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    chunks: list[bytes] = []
    reader = threading.Thread(target=lambda: chunks.extend(iter(lambda: proc.stdout.read(4096), b"")), daemon=True)
    reader.start()
    started = time.monotonic()
    while proc.poll() is None:
        if cancel is not None and cancel.is_set():
            proc.kill()
            proc.wait()
            raise Cancelled("cancelled")
        if time.monotonic() - started > timeout_s:
            proc.kill()
            proc.wait()
            raise MediaError(f"{Path(cmd[0]).name} took longer than {timeout_s:.0f} s and was stopped")
        time.sleep(0.2)
    reader.join(timeout=5)
    out = b"".join(chunks).decode("utf-8", "replace")
    if proc.returncode != 0:
        raise MediaError(f"{Path(cmd[0]).name} failed (exit {proc.returncode}): {out[-800:]}")
    return out


# ---------------------------------------------------------------- files

def check_new_file(filename: str, content: str, max_kb: int) -> str | None:
    """Problems with a file the model wants to create, or None if it is acceptable."""
    name = filename.strip()
    if not SAFE_NAME.match(name) or ".." in name:
        return "file name must be a plain name like report.md (letters, numbers, spaces, - _ .)"
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return f"file type '{ext or 'none'}' is not allowed; use one of {', '.join(sorted(ALLOWED_EXTENSIONS))}"
    if not content.strip():
        return "the file is empty"
    if len(content.encode("utf-8")) > max_kb * 1024:
        return f"the file is larger than {max_kb} KB"
    if ext == ".json":
        try:
            json.loads(content)
        except ValueError as exc:
            return f"not valid JSON: {exc}"
    if ext == ".csv":
        rows = list(csv.reader(io.StringIO(content)))
        if len(rows) < 2 or len({len(r) for r in rows if r}) != 1:
            return "CSV needs a header and rows that all have the same number of columns"
    return None


def save_new_file(folder: Path, filename: str, content: str) -> Path:
    """Write into `folder` only, never overwriting: report.md, report-2.md, ..."""
    folder.mkdir(parents=True, exist_ok=True)
    name = Path(filename.strip()).name
    stem, ext = Path(name).stem, Path(name).suffix
    target = folder / name
    n = 2
    while target.exists():
        target = folder / f"{stem}-{n}{ext}"
        n += 1
    if target.resolve().parent != folder.resolve():
        raise MediaError("refusing to write outside the files folder")
    target.write_text(content, encoding="utf-8", newline="")
    return target


# ---------------------------------------------------------------- images

def generate_image(s: Settings, prompt: str, out: Path, seed: int, cancel: threading.Event | None = None) -> dict:
    binary, model = sd_binary(s), sd_model(s)
    if not binary or not model:
        raise MediaError("Image generation is not installed. Run scripts\\install-phase7.ps1")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [binary, "-m", model, "-p", prompt, "-o", out, "--steps", s.image_steps, "--cfg-scale", "1.0",
           "-W", s.image_size, "-H", s.image_size, "-s", seed, "-t", max(1, (os.cpu_count() or 2))]
    started = time.perf_counter()
    log = run_program(cmd, timeout_s=s.image_timeout_s, cancel=cancel, cwd=binary.parent)
    if not out.is_file():
        raise MediaError(f"the image program finished but wrote no image: {log[-500:]}")
    return {"seconds": round(time.perf_counter() - started, 1), "seed": seed, "size": s.image_size,
            "steps": s.image_steps, "model": model.name}


def plain_background(s: Settings, out: Path, seed: int, cancel: threading.Event | None = None) -> Path:
    """A colour-gradient picture, used for videos when no image model is installed."""
    ffmpeg = ffmpeg_binary(s)
    if not ffmpeg:
        raise MediaError("FFmpeg is not installed. Run scripts\\install-phase7.ps1")
    out.parent.mkdir(parents=True, exist_ok=True)
    run_program([ffmpeg, "-hide_banner", "-y", "-f", "lavfi", "-i", f"gradients=s=512x512:seed={seed}:speed=0",
                 "-frames:v", "1", out], timeout_s=60, cancel=cancel)
    return out


# ---------------------------------------------------------------- narration (text to speech)

def narrate(text: str, out_wav: Path, cancel: threading.Event | None = None) -> Path | None:
    """Speak `text` into a WAV file with the computer's own voices. None if no voice is available."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        txt = out_wav.with_suffix(".txt")
        txt.write_text(text, encoding="utf-8")
        script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  "$s.Rate = -1; $s.SetOutputToWaveFile($env:LF_OUT); "
                  "$s.Speak([IO.File]::ReadAllText($env:LF_TXT, [Text.Encoding]::UTF8)); $s.Dispose()")
        env = {**os.environ, "LF_OUT": str(out_wav), "LF_TXT": str(txt)}
        run_program(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout_s=120,
                    cancel=cancel, env=env)
        txt.unlink(missing_ok=True)
    else:
        tool = shutil.which("espeak-ng") or shutil.which("espeak")
        if not tool:
            return None
        run_program([tool, "-w", out_wav, text], timeout_s=120, cancel=cancel)
    return out_wav if out_wav.is_file() and out_wav.stat().st_size > 100 else None


# ---------------------------------------------------------------- video

@dataclass
class Scene:
    image: Path
    caption: str
    audio: Path | None


def media_seconds(ffmpeg: Path, path: Path) -> float:
    """Duration of an audio/video file, read from FFmpeg's own report."""
    proc = subprocess.run([str(ffmpeg), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                          creationflags=NO_WINDOW)
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    if not m:
        raise MediaError(f"could not read the length of {path.name}")
    h, mi, se = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(se)


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def build_video(s: Settings, scenes: list[Scene], out: Path, cancel: threading.Event | None = None,
                width: int = 1024, height: int = 576, fps: int = 25) -> dict:
    """Slideshow MP4: slow zoom on each image, fades, narration, captions as a subtitle track."""
    ffmpeg = ffmpeg_binary(s)
    if not ffmpeg:
        raise MediaError("FFmpeg is not installed. Run scripts\\install-phase7.ps1")
    work = out.parent / "parts"
    work.mkdir(parents=True, exist_ok=True)
    clips, srt, clock = [], [], 0.0
    for i, sc in enumerate(scenes, 1):
        duration = (media_seconds(ffmpeg, sc.audio) + 0.8) if sc.audio else 4.0
        duration = max(3.0, min(duration, 30.0))
        frames = int(duration * fps)
        clip = work / f"scene{i:02d}.mp4"
        zoom = (f"scale={width * 2}:-2,crop={width * 2}:{height * 2},"
                f"zoompan=z='min(zoom+0.0007,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d={frames}:s={width}x{height}:fps={fps},"
                f"fade=t=in:st=0:d=0.6,fade=t=out:st={duration - 0.6:.2f}:d=0.6,format=yuv420p")
        audio_in = ["-i", sc.audio] if sc.audio else ["-f", "lavfi", "-t", f"{duration:.2f}", "-i",
                                                      "anullsrc=channel_layout=mono:sample_rate=22050"]
        cmd = [ffmpeg, "-hide_banner", "-y", "-loop", "1", "-i", sc.image, *audio_in,
               "-filter_complex", f"[0:v]{zoom}[v];[1:a]apad,aresample=44100[a]",
               "-map", "[v]", "-map", "[a]", "-t", f"{duration:.2f}",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k", "-ac", "2",
               "-r", fps, clip]
        run_program(cmd, timeout_s=600, cancel=cancel)
        clips.append(clip)
        if sc.caption.strip():
            srt.append(f"{i}\n{_srt_time(clock)} --> {_srt_time(clock + duration)}\n{sc.caption.strip()}\n")
        clock += duration

    listing = work / "clips.txt"
    listing.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    joined = work / "joined.mp4"
    run_program([ffmpeg, "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy",
                 joined.name], timeout_s=300, cancel=cancel, cwd=work)
    subs = out.with_suffix(".srt")
    subs.write_text("\n".join(srt), encoding="utf-8")
    if srt:
        run_program([ffmpeg, "-hide_banner", "-y", "-i", joined, "-i", subs, "-map", "0", "-map", "1",
                     "-c", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng", out],
                    timeout_s=300, cancel=cancel)
    else:
        shutil.copyfile(joined, out)
    shutil.rmtree(work, ignore_errors=True)
    return {"seconds_long": round(clock, 1), "scenes": len(scenes), "width": width, "height": height,
            "narrated": any(sc.audio for sc in scenes), "subtitles": subs.name if srt else None}
