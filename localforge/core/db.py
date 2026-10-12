"""SQLite storage for conversations and messages."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    model       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content          TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('complete', 'incomplete', 'error')),
    tokens           INTEGER,
    duration_ms      INTEGER,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    request      TEXT NOT NULL,
    model        TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('running', 'done', 'failed', 'cancelled')),
    plan         TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE TABLE IF NOT EXISTS task_steps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    number     INTEGER NOT NULL,
    title      TEXT NOT NULL,
    description TEXT NOT NULL,
    code       TEXT,
    verdict    TEXT,
    revisions  INTEGER NOT NULL DEFAULT 0,
    issues     TEXT
);
CREATE TABLE IF NOT EXISTS agent_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_id      INTEGER REFERENCES task_steps(id) ON DELETE CASCADE,
    agent        TEXT NOT NULL,
    input        TEXT NOT NULL,
    output       TEXT,
    status       TEXT NOT NULL CHECK (status IN ('running', 'ok', 'invalid', 'error', 'cancelled')),
    tokens       INTEGER,
    duration_ms  INTEGER,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    round        INTEGER NOT NULL,
    status       TEXT NOT NULL,
    passed       INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    output       TEXT,
    duration_ms  INTEGER,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL CHECK (kind IN ('file', 'image', 'video')),
    prompt       TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('running', 'done', 'failed', 'cancelled')),
    path         TEXT,
    error        TEXT,
    meta         TEXT,
    duration_ms  INTEGER,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steps_task ON task_steps(task_id, number);
CREATE INDEX IF NOT EXISTS idx_test_runs_task ON test_runs(task_id, id);
CREATE INDEX IF NOT EXISTS idx_runs_task ON agent_runs(task_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(SCHEMA)
            # Columns added after Phase 3 (databases created earlier lack them).
            have = {r["name"] for r in con.execute("PRAGMA table_info(tasks)")}
            for column in ("test_status TEXT", "project_dir TEXT"):
                if column.split()[0] not in have:
                    con.execute(f"ALTER TABLE tasks ADD COLUMN {column}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # One short-lived connection per operation keeps this safe across threads.
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            with con:
                yield con
        finally:
            con.close()

    def create_conversation(self, title: str, model: str) -> int:
        now = _now()
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO conversations (title, model, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (title[:60], model, now, now),
            )
            return int(cur.lastrowid)

    def conversation_exists(self, conversation_id: int) -> bool:
        with self._connect() as con:
            return con.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone() is not None

    def add_message(self, conversation_id: int, role: str, content: str, status: str = "complete") -> int:
        now = _now()
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO messages (conversation_id, role, content, status, created_at) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, role, content, status, now),
            )
            con.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
            return int(cur.lastrowid)

    def finish_message(self, message_id: int, content: str, status: str,
                       tokens: int | None = None, duration_ms: int | None = None) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE messages SET content = ?, status = ?, tokens = ?, duration_ms = ? WHERE id = ?",
                (content, status, tokens, duration_ms, message_id),
            )

    def history(self, conversation_id: int, limit: int) -> list[dict]:
        """Last `limit` complete messages, oldest first, in Ollama chat format."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? AND status = 'complete' "
                "ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def list_conversations(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT c.id, c.title, c.model, c.created_at, c.updated_at, COUNT(m.id) AS message_count "
                "FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id "
                "GROUP BY c.id ORDER BY c.updated_at DESC, c.id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self, conversation_id: int) -> dict | None:
        with self._connect() as con:
            conv = con.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if conv is None:
                return None
            msgs = con.execute(
                "SELECT id, role, content, status, tokens, duration_ms, created_at "
                "FROM messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return {**dict(conv), "messages": [dict(m) for m in msgs]}

    # ------------------------------------------------------------ agent tasks (Phase 3)

    def fail_interrupted_tasks(self) -> int:
        """Tasks still 'running' at startup were cut off by a restart."""
        with self._connect() as con:
            cur = con.execute(
                "UPDATE tasks SET status = 'failed', error = 'Interrupted: the service stopped while it ran', "
                "finished_at = ? WHERE status = 'running'", (_now(),))
            con.execute("UPDATE agent_runs SET status = 'cancelled' WHERE status = 'running'")
            return cur.rowcount

    def create_task(self, request: str, model: str) -> int:
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO tasks (request, model, status, created_at) VALUES (?, ?, 'running', ?)",
                (request, model, _now()))
            return int(cur.lastrowid)

    def set_task_plan(self, task_id: int, plan: list[dict]) -> None:
        with self._connect() as con:
            con.execute("UPDATE tasks SET plan = ? WHERE id = ?", (json.dumps(plan), task_id))

    def finish_task(self, task_id: int, status: str, error: str | None = None) -> None:
        with self._connect() as con:
            con.execute("UPDATE tasks SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                        (status, error, _now(), task_id))

    def add_step(self, task_id: int, number: int, title: str, description: str) -> int:
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO task_steps (task_id, number, title, description) VALUES (?, ?, ?, ?)",
                (task_id, number, title, description))
            return int(cur.lastrowid)

    def update_step(self, step_id: int, *, code: str | None, verdict: str | None,
                    revisions: int, issues: list[str]) -> None:
        with self._connect() as con:
            con.execute("UPDATE task_steps SET code = ?, verdict = ?, revisions = ?, issues = ? WHERE id = ?",
                        (code, verdict, revisions, json.dumps(issues), step_id))

    def start_agent_run(self, task_id: int, step_id: int | None, agent: str, input_text: str) -> int:
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO agent_runs (task_id, step_id, agent, input, status, created_at) "
                "VALUES (?, ?, ?, ?, 'running', ?)", (task_id, step_id, agent, input_text, _now()))
            return int(cur.lastrowid)

    def finish_agent_run(self, run_id: int, status: str, output: str | None,
                         tokens: int | None, duration_ms: int | None) -> None:
        with self._connect() as con:
            con.execute("UPDATE agent_runs SET status = ?, output = ?, tokens = ?, duration_ms = ? WHERE id = ?",
                        (status, output, tokens, duration_ms, run_id))

    def list_tasks(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT t.id, t.request, t.model, t.status, t.error, t.created_at, t.finished_at, "
                "(SELECT COUNT(*) FROM task_steps s WHERE s.task_id = t.id) AS step_count "
                "FROM tasks t ORDER BY t.id DESC").fetchall()
        return [dict(r) for r in rows]

    def get_task(self, task_id: int) -> dict | None:
        with self._connect() as con:
            task = con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                return None
            steps = con.execute("SELECT * FROM task_steps WHERE task_id = ? ORDER BY number", (task_id,)).fetchall()
            runs = con.execute(
                "SELECT id, step_id, agent, status, tokens, duration_ms, input, output, created_at "
                "FROM agent_runs WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
        result = dict(task)
        result["plan"] = json.loads(result["plan"]) if result["plan"] else None
        result["steps"] = [{**dict(s), "issues": json.loads(s["issues"]) if s["issues"] else []} for s in steps]
        result["agent_runs"] = [dict(r) for r in runs]
        result["test_runs"] = self.test_runs(task_id)
        return result

    # ------------------------------------------------------------ tests and projects (Phase 4)

    def set_task_project(self, task_id: int, project_dir: str) -> None:
        with self._connect() as con:
            con.execute("UPDATE tasks SET project_dir = ? WHERE id = ?", (project_dir, task_id))

    def set_test_status(self, task_id: int, test_status: str) -> None:
        with self._connect() as con:
            con.execute("UPDATE tasks SET test_status = ? WHERE id = ?", (test_status, task_id))

    def add_test_run(self, task_id: int, round_no: int, status: str, passed: int, failed: int,
                     output: str, duration_ms: int) -> int:
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO test_runs (task_id, round, status, passed, failed, output, duration_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, round_no, status, passed, failed, output, duration_ms, _now()))
            return int(cur.lastrowid)

    def test_runs(self, task_id: int) -> list[dict]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM test_runs WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ created files, images, videos (Phase 7)

    def fail_interrupted_artifacts(self) -> int:
        with self._connect() as con:
            return con.execute("UPDATE artifacts SET status = 'failed', error = 'Interrupted: the service stopped' "
                               "WHERE status = 'running'").rowcount

    def create_artifact(self, kind: str, prompt: str) -> int:
        with self._connect() as con:
            cur = con.execute("INSERT INTO artifacts (kind, prompt, status, created_at) VALUES (?, ?, 'running', ?)",
                              (kind, prompt, _now()))
            return int(cur.lastrowid)

    def finish_artifact(self, artifact_id: int, status: str, *, path: str | None = None, error: str | None = None,
                        meta: dict | None = None, duration_ms: int | None = None) -> None:
        with self._connect() as con:
            con.execute("UPDATE artifacts SET status = ?, path = ?, error = ?, meta = ?, duration_ms = ? WHERE id = ?",
                        (status, path, error, json.dumps(meta or {}), duration_ms, artifact_id))

    def get_artifact(self, artifact_id: int) -> dict | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["meta"] = json.loads(d["meta"]) if d["meta"] else {}
        return d

    def list_artifacts(self, limit: int = 100) -> list[dict]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM artifacts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["meta"] = json.loads(d["meta"]) if d["meta"] else {}
            out.append(d)
        return out
