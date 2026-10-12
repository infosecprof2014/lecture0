from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make `core` importable when pytest runs from D:\LocalForgeAI.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.app import create_app  # noqa: E402
from core.config import Settings  # noqa: E402

from .fake_ollama import FakeOllama  # noqa: E402

GB = 1024 ** 3


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(db_path=str(tmp_path / "test.db"), log_path=str(tmp_path / "core.log"))


@pytest.fixture
def fake() -> FakeOllama:
    return FakeOllama()


@pytest.fixture
def ram():
    """Free RAM the app sees; tests change ram['bytes']."""
    return {"bytes": 8 * GB}


@pytest.fixture
def make_client(settings, fake, ram):
    clients = []

    def _make(s: Settings | None = None) -> TestClient:
        app = create_app(s or settings, ollama_transport=fake.transport(), free_ram=lambda: ram["bytes"])
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def read_stream(client: TestClient, body: dict) -> tuple[int, list[dict]]:
    with client.stream("POST", "/chat", json=body) as r:
        if r.headers.get("content-type", "").startswith("application/json"):
            r.read()
            return r.status_code, [r.json()]
        return r.status_code, [json.loads(line) for line in r.iter_lines() if line.strip()]
