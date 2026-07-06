"""Shared database connection and schema for the TODO agent.

Not a tool module (prefixed with _) — imported by other tool modules.
Files starting with _ are skipped by discover_tools() and won't appear
as callable tools.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

AGENT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = AGENT_DIR / "todos.db"

_connection: Optional[sqlite3.Connection] = None


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS todos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    labels      TEXT    NOT NULL DEFAULT '',
    due_date    TEXT,
    due_time    TEXT,
    status      TEXT    NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'completed')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status);
CREATE INDEX IF NOT EXISTS idx_todos_due_date ON todos(due_date);
"""


def get_db() -> sqlite3.Connection:
    """Get or create a shared SQLite connection for the TODO database."""
    global _connection
    if _connection is None:
        _connection = sqlite3.connect(str(DB_PATH))
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA journal_mode=WAL")
        _connection.execute("PRAGMA foreign_keys=ON")
    return _connection


def init_schema() -> None:
    """Create tables and indexes if they don't exist."""
    db = get_db()
    db.executescript(SCHEMA_SQL)
    db.commit()


def row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a todo database row into a plain dict."""
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "labels": [label.strip() for label in row["labels"].split(",") if label.strip()],
        "due_date": row["due_date"],
        "due_time": row["due_time"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
