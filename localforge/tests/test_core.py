"""Fast tests: no real model needed (Ollama is faked)."""

from __future__ import annotations

import json
import logging

import pytest

from core.config import Settings, SettingsError, load_settings

from .conftest import GB, read_stream


# ---------------------------------------------------------------- health / models

def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["ollama"]["reachable"] is True
    assert body["ollama"]["version"] == "0.35.0"
    assert body["free_ram_mb"] == 8 * 1024


def test_health_when_ollama_down(client, fake):
    fake.running = False
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["ollama"]["reachable"] is False
    assert "not running" in body["ollama"]["message"]


def test_models_lists_installed(client):
    names = [m["name"] for m in client.get("/models").json()["models"]]
    assert names == ["llama3.2:3b", "qwen3:4b"]


def test_models_when_ollama_down(client, fake):
    fake.running = False
    r = client.get("/models")
    assert r.status_code == 503
    assert r.json()["error"] == "ollama_unavailable"


# ---------------------------------------------------------------- chat

def test_chat_streams_and_saves(client):
    status, events = read_stream(client, {"message": "Say OK"})
    assert status == 200
    assert events[0]["type"] == "start"
    tokens = [e["content"] for e in events if e["type"] == "token"]
    assert tokens == ["Hel", "lo", " there", "!"]  # arrives in pieces, not one block
    done = events[-1]
    assert done["type"] == "done" and done["tokens"] == 4 and done["tokens_per_s"] == 8.0

    conv = client.get(f"/conversations/{done['conversation_id']}").json()
    assert conv["title"] == "Say OK"
    assert [(m["role"], m["content"], m["status"]) for m in conv["messages"]] == [
        ("user", "Say OK", "complete"),
        ("assistant", "Hello there!", "complete"),
    ]


def test_chat_continues_conversation_with_history(client, fake):
    _, first = read_stream(client, {"message": "Hi"})
    conv_id = first[-1]["conversation_id"]
    _, second = read_stream(client, {"message": "And again", "conversation_id": conv_id})
    assert second[-1]["conversation_id"] == conv_id
    sent = fake.chat_requests[-1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[-1]["content"] == "And again"


def test_history_is_limited(make_client, settings, fake):
    client = make_client(Settings(**{**settings.__dict__, "max_history_messages": 3}))
    _, ev = read_stream(client, {"message": "one"})
    cid = ev[-1]["conversation_id"]
    read_stream(client, {"message": "two", "conversation_id": cid})
    read_stream(client, {"message": "three", "conversation_id": cid})
    sent = fake.chat_requests[-1]["messages"]
    assert len(sent) == 3 and sent[-1]["content"] == "three"


def test_chat_ollama_down_gives_clear_error_and_service_survives(client, fake):
    fake.running = False
    status, events = read_stream(client, {"message": "Say OK"})
    assert status == 503
    assert events[0]["error"] == "ollama_unavailable"
    assert "Ollama is not running" in events[0]["message"]
    assert client.get("/health").json()["status"] == "ok"
    fake.running = True
    status, _ = read_stream(client, {"message": "Say OK"})
    assert status == 200  # not stuck "busy" after the failure


def test_chat_drop_mid_answer_saves_partial(client, fake):
    fake.fail_after = 2
    status, events = read_stream(client, {"message": "Tell me"})
    assert status == 200
    assert events[-1]["type"] == "error"
    conv = client.get(f"/conversations/{events[0]['conversation_id']}").json()
    reply = conv["messages"][-1]
    assert reply["status"] == "error" and reply["content"] == "Hello"


def test_chat_refuses_when_low_memory(client, ram):
    ram["bytes"] = 1 * GB  # model needs ~1.9 GB + 0.5 GB reserve
    status, events = read_stream(client, {"message": "Say OK"})
    assert status == 503
    assert events[0]["error"] == "low_memory"
    assert events[0]["needed_mb"] > events[0]["free_mb"]


def test_low_memory_ok_when_model_already_loaded(client, fake, ram):
    fake.loaded = ["llama3.2:3b"]
    ram["bytes"] = 600 * 1024 * 1024  # only the 500 MB reserve is needed
    status, _ = read_stream(client, {"message": "Say OK"})
    assert status == 200


def test_only_one_model_kept_loaded(client, fake):
    fake.loaded = ["qwen3:4b"]
    status, _ = read_stream(client, {"message": "Say OK"})
    assert status == 200
    assert fake.unloaded == ["qwen3:4b"]
    assert fake.loaded == ["llama3.2:3b"]


def test_one_chat_at_a_time(client):
    assert client.app.state.model_lock.try_acquire("other")
    status, events = read_stream(client, {"message": "Say OK"})
    assert status == 429 and events[0]["error"] == "busy"


def test_stale_busy_flag_expires(client, settings):
    import time
    lock = client.app.state.model_lock
    assert lock.try_acquire("other")
    # Last activity longer ago than request_timeout_s. (Not 0.0: the monotonic clock
    # counts from boot, so 0.0 is "recent" on a machine that started a few minutes ago.)
    lock._last_activity = time.monotonic() - settings.request_timeout_s - 1
    status, _ = read_stream(client, {"message": "Say OK"})
    assert status == 200


def test_unknown_model_and_conversation(client):
    status, events = read_stream(client, {"message": "x", "model": "nope:1b"})
    assert status == 404 and events[0]["error"] == "model_not_installed"
    status, events = read_stream(client, {"message": "x", "conversation_id": 999})
    assert status == 404 and events[0]["error"] == "not_found"
    assert client.get("/conversations/999").status_code == 404


def test_bad_requests(client, settings):
    assert client.post("/chat", json={"message": ""}).status_code == 422
    r = client.post("/chat", json={"message": "x" * (settings.max_message_chars + 1)})
    assert r.status_code == 413


# ---------------------------------------------------------------- persistence / logs

def test_history_survives_restart(make_client, settings):
    first = make_client()
    _, events = read_stream(first, {"message": "Remember me"})
    conv_id = events[-1]["conversation_id"]
    second = make_client(settings)  # new app instance, same database file
    ids = [c["id"] for c in second.get("/conversations").json()["conversations"]]
    assert conv_id in ids


def test_log_file_written(tmp_path, settings):
    from core.logging_setup import setup_logging
    setup_logging(settings)
    logging.getLogger("localforge.test").info("hello log")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "hello log" in settings.log_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------- settings

def write(tmp_path, data) -> str:
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def test_settings_load(tmp_path):
    s = load_settings(write(tmp_path, {"model": "qwen3:4b", "port": 8001}))
    assert s.model == "qwen3:4b" and s.port == 8001 and s.host == "127.0.0.1"


@pytest.mark.parametrize("data, text", [
    ({"host": "0.0.0.0"}, "loopback"),
    ({"colour": "red"}, "Unknown setting"),
    ({"port": "8000"}, "must be int"),
    ({"port": 0}, "greater than 0"),
    ({"ram_reserve_mb": True}, "must be int"),
])
def test_settings_rejected(tmp_path, data, text):
    with pytest.raises(SettingsError, match=text):
        load_settings(write(tmp_path, data))


def test_shipped_settings_file_is_valid():
    from pathlib import Path
    shipped = Path(__file__).resolve().parent.parent / "config" / "settings.json"
    assert load_settings(shipped).host == "127.0.0.1"
