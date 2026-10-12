"""Rotating log file for the core service (also echoed to the console)."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import Settings

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(settings: Settings) -> logging.Logger:
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Replace handlers from a previous call (tests create several apps).
    for h in list(root.handlers):
        if getattr(h, "_localforge", False):
            root.removeHandler(h)
            h.close()

    file_handler = RotatingFileHandler(
        settings.log_file,
        maxBytes=settings.log_max_mb * 1024 * 1024,
        backupCount=settings.log_backups,
        encoding="utf-8",
    )
    console = logging.StreamHandler()
    for h in (file_handler, console):
        h.setFormatter(logging.Formatter(FORMAT))
        h._localforge = True  # type: ignore[attr-defined]
        root.addHandler(h)
    return logging.getLogger("localforge")
