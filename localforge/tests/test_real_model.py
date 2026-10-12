"""Slow test against the real Ollama and llama3.2:3b. Run with: python -m pytest -m slow"""

from __future__ import annotations

import os

import httpx
import pytest
from fastapi.testclient import TestClient

from core.app import create_app
from core.config import Settings

from .conftest import read_stream

pytestmark = pytest.mark.slow
MODEL = os.environ.get("LOCALFORGE_TEST_MODEL", "llama3.2:3b")


def test_real_model_answers(tmp_path):
    try:
        httpx.get("http://127.0.0.1:11434/api/version", timeout=3).raise_for_status()
    except httpx.HTTPError:
        msg = "Ollama is not running on 127.0.0.1:11434"
        if os.environ.get("LOCALFORGE_REQUIRE_REAL") == "1":
            pytest.fail(msg)
        pytest.skip(msg)

    settings = Settings(model=MODEL, db_path=str(tmp_path / "real.db"), log_path=str(tmp_path / "core.log"))
    with TestClient(create_app(settings)) as client:
        status, events = read_stream(client, {"message": "Say OK"})
    assert status == 200, events
    assert events[-1]["type"] == "done", events[-1]
    text = "".join(e["content"] for e in events if e["type"] == "token")
    assert text.strip(), "model returned an empty answer"
