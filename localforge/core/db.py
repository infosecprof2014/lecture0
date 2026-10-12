"""SQLite storage for conversations and messages."""

from __future__ import annotations

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
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(SCHEMA)

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
