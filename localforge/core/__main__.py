"""Start the core service: python -m core (from D:\\LocalForgeAI)."""

from __future__ import annotations

import socket
import sys

import uvicorn

from .app import create_app
from .config import SettingsError, load_settings
from .logging_setup import setup_logging


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def main() -> int:
    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    log = setup_logging(settings)
    if not port_free(settings.host, settings.port):
        log.error("Port %s on %s is already in use. Is the core already running?", settings.port, settings.host)
        return 3
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,  # use the handlers from setup_logging
        access_log=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
