"""Speech to text on this PC with Whisper (faster-whisper). Nothing is sent anywhere."""

from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path

from .config import Settings

SUFFIX = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav",
          "audio/mpeg": ".mp3", "audio/mp4": ".m4a"}


class VoiceUnavailable(Exception):
    pass


class Transcriber:
    """Loads the Whisper model on first use and keeps it (about 200-300 MB of memory)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise VoiceUnavailable("Voice input is not installed. Run scripts\\install-phase7.ps1") from exc
            folder = self.settings.resolve(self.settings.whisper_model_dir)
            if not (folder / "model.bin").is_file():
                raise VoiceUnavailable(f"The Whisper model is missing in {folder}. Run scripts\\install-phase7.ps1")
            self._model = WhisperModel(str(folder), device="cpu", compute_type="int8",
                                       cpu_threads=max(1, os.cpu_count() or 2))
        return self._model

    def transcribe(self, audio: bytes, content_type: str) -> dict:
        suffix = SUFFIX.get(content_type.split(";")[0].strip().lower(), ".webm")
        with self._lock:  # one transcription at a time
            model = self._load()
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / f"speech{suffix}"
                path.write_bytes(audio)
                started = time.perf_counter()
                segments, info = model.transcribe(str(path), beam_size=1, vad_filter=True)
                text = " ".join(seg.text.strip() for seg in segments).strip()
        return {"text": text, "language": info.language, "audio_seconds": round(info.duration, 1),
                "seconds": round(time.perf_counter() - started, 1)}
