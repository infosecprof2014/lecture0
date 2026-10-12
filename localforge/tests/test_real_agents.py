"""Slow test: a real agent task with the real Ollama and llama3.2:3b. Run with: python -m pytest -m slow"""

from __future__ import annotations

import ast
import json
import os
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from core.app import create_app
from core.config import Settings

pytestmark = pytest.mark.slow
MODEL = os.environ.get("LOCALFORGE_TEST_MODEL", "llama3.2:3b")


def test_real_agent_task(tmp_path):
    try:
        httpx.get("http://127.0.0.1:11434/api/version", timeout=3).raise_for_status()
    except httpx.HTTPError:
        msg = "Ollama is not running on 127.0.0.1:11434"
        if os.environ.get("LOCALFORGE_REQUIRE_REAL") == "1":
            pytest.fail(msg)
        pytest.skip(msg)

    # Keep it short on a slow CPU: at most 2 steps and 1 revision.
    settings = replace(Settings(), model=MODEL, max_plan_steps=2, max_revisions=1, max_fix_rounds=1,
                       db_path=str(tmp_path / "real.db"), log_path=str(tmp_path / "core.log"),
                       projects_path=str(tmp_path / "projects"))
    with TestClient(create_app(settings, extra_hosts=("testserver",))) as client:
        with client.stream("POST", "/tasks", json={
                "request": "Write a Python function is_even(n) that returns True if n is even."}) as r:
            assert r.status_code == 200
            events = [json.loads(line) for line in r.iter_lines() if line.strip()]
        assert events[-1]["type"] == "task_end"
        assert events[-1]["status"] == "done", events[-1]
        task = client.get(f"/tasks/{events[0]['task_id']}").json()

    assert 1 <= len(task["steps"]) <= 2
    for step in task["steps"]:
        assert step["verdict"] in ("approve", "revise")
        assert step["revisions"] <= 1
        ast.parse(step["code"])  # valid Python; nothing is executed
    # Phase 4: tests were written and run in the sandbox (the outcome depends on the model)
    assert task["test_status"] in ("passed", "failed", "no_tests"), task["test_status"]
