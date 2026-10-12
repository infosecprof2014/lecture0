"""Settings for the LocalForge AI core, read from config/settings.json."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path

# D:\LocalForgeAI on the target machine: the folder that contains core\.
ROOT = Path(os.environ.get("LOCALFORGE_ROOT", Path(__file__).resolve().parent.parent))

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class SettingsError(Exception):
    """settings.json is missing, unreadable or has a bad value."""


@dataclass(frozen=True)
class Settings:
    model: str = "llama3.2:3b"
    host: str = "127.0.0.1"
    port: int = 8000
    ollama_url: str = "http://127.0.0.1:11434"
    ram_reserve_mb: int = 500
    max_history_messages: int = 20
    max_message_chars: int = 20000
    request_timeout_s: int = 600
    db_path: str = "database/localforge.db"
    log_path: str = "logs/core.log"
    log_max_mb: int = 5
    log_backups: int = 5

    def resolve(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else ROOT / p

    @property
    def db_file(self) -> Path:
        return self.resolve(self.db_path)

    @property
    def log_file(self) -> Path:
        return self.resolve(self.log_path)


def default_settings_path() -> Path:
    return Path(os.environ.get("LOCALFORGE_SETTINGS", ROOT / "config" / "settings.json"))


def load_settings(path: Path | str | None = None) -> Settings:
    path = Path(path) if path else default_settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise SettingsError(f"Settings file not found: {path}") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise SettingsError(f"Cannot read {path}: {exc}") from None
    if not isinstance(raw, dict):
        raise SettingsError(f"{path} must contain a JSON object")

    known = {f.name: f for f in fields(Settings)}
    unknown = sorted(set(raw) - set(known))
    if unknown:
        raise SettingsError(f"Unknown setting(s) in {path}: {', '.join(unknown)}")

    defaults = Settings()
    for name, value in raw.items():
        expected = type(getattr(defaults, name))
        # bool is a subclass of int; reject it for numeric settings.
        if not isinstance(value, expected) or isinstance(value, bool):
            raise SettingsError(f"Setting '{name}' must be {expected.__name__}, got {value!r}")

    settings = Settings(**raw)
    if settings.host not in LOOPBACK_HOSTS:
        raise SettingsError(
            f"host must be a loopback address ({', '.join(sorted(LOOPBACK_HOSTS))}) "
            f"so the service is not reachable from other devices; got {settings.host!r}"
        )
    for name in ("port", "ram_reserve_mb", "max_history_messages", "max_message_chars",
                 "request_timeout_s", "log_max_mb", "log_backups"):
        if getattr(settings, name) <= 0:
            raise SettingsError(f"Setting '{name}' must be greater than 0")
    if not settings.model.strip():
        raise SettingsError("Setting 'model' must not be empty")
    return settings
