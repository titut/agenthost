"""SQLite-backed memory for an agent."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from agenthost.config import AgentConfig


class AgentMemory:
    """Simple SQLite-backed memory: conversation history + key/value store."""

    def __init__(self, config: AgentConfig):
        self.config = config
        config.memory_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = config.memory_dir / "memory.db"
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    tool_call_id TEXT,
                    name TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);

                CREATE TABLE IF NOT EXISTS kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.commit()

    def get_messages(self, thread_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, tool_calls, tool_call_id, name FROM messages "
                "WHERE thread_id = ? ORDER BY id",
                (thread_id,),
            ).fetchall()

        messages: list[dict[str, Any]] = []
        for row in rows:
            msg: dict[str, Any] = {"role": row["role"]}
            if row["tool_calls"]:
                msg["tool_calls"] = json.loads(row["tool_calls"])
            if row["tool_call_id"]:
                msg["tool_call_id"] = row["tool_call_id"]
            if row["name"]:
                msg["name"] = row["name"]
            if row["content"]:
                msg["content"] = row["content"]
            messages.append(msg)
        return messages

    def repair_thread(self, thread_id: str) -> int:
        """Remove assistant 'tool_calls' messages with no matching tool responses.

        This can happen if a tool hung or crashed after the assistant message was
        persisted. OpenAI rejects conversations with unanswered tool_calls.
        Returns the number of dangling assistant messages removed.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, role, tool_calls, tool_call_id FROM messages "
                "WHERE thread_id = ? ORDER BY id",
                (thread_id,),
            ).fetchall()

        ids_to_delete: list[int] = []
        i = 0
        while i < len(rows):
            row = rows[i]
            if row["role"] != "assistant" or not row["tool_calls"]:
                i += 1
                continue

            tool_calls = json.loads(row["tool_calls"])
            expected_ids = {tc.get("id") for tc in tool_calls if tc.get("id")}
            if not expected_ids:
                i += 1
                continue

            j = i + 1
            found_ids: set[str] = set()
            while j < len(rows) and rows[j]["role"] == "tool":
                found_ids.add(rows[j]["tool_call_id"])
                j += 1

            if not expected_ids <= found_ids:
                ids_to_delete.append(row["id"])
            i = j

        if ids_to_delete:
            with self._connect() as conn:
                placeholders = ",".join("?" * len(ids_to_delete))
                conn.execute(
                    f"DELETE FROM messages WHERE id IN ({placeholders})",
                    tuple(ids_to_delete),
                )
                conn.commit()
        return len(ids_to_delete)

    def append_message(self, thread_id: str, message: dict[str, Any]) -> None:
        tool_calls = json.dumps(message.get("tool_calls")) if message.get("tool_calls") else None
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages (thread_id, role, content, tool_calls, tool_call_id, name) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    message["role"],
                    message.get("content"),
                    tool_calls,
                    message.get("tool_call_id"),
                    message.get("name"),
                ),
            )
            conn.commit()
        self._trim_messages(thread_id)

    def _trim_messages(self, thread_id: str) -> None:
        """Keep only the most recent max_memory_turns."""
        max_turns = self.config.max_memory_turns
        if max_turns <= 0:
            return
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM messages WHERE id IN ("
                "SELECT id FROM messages WHERE thread_id = ? "
                "ORDER BY id DESC LIMIT -1 OFFSET ?"
                ")",
                (thread_id, max_turns),
            )
            conn.commit()

    def get(self, key: str, default: Any = None) -> Any:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return row["value"]

    def set(self, key: str, value: Any) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
                (key, json.dumps(value, default=str)),
            )
            conn.commit()

    def clear_messages(self) -> None:
        """Remove all conversation history. Called on serve to start fresh."""
        with self._connect() as conn:
            conn.execute("DELETE FROM messages")
            conn.commit()

    def clear_thread(self, thread_id: str) -> None:
        """Remove all messages for a specific thread."""
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
            conn.commit()
