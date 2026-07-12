"""Helpers for reading other agents' memory databases.

Not a tool module (prefixed with _) — imported by memory_tools.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator, NamedTuple

from agenthost.agents_config import AgentsConfig
from agenthost.config import AgentConfig


class MemoryMessage(NamedTuple):
    agent_alias: str
    thread_id: str
    role: str
    content: str | None
    created_at: str


def _resolve_agent_path(alias: str) -> Path | None:
    """Resolve an agent alias to its folder path via agents.yaml."""
    path = AgentsConfig().resolve(alias)
    if path is None:
        return None
    return path.expanduser().resolve()


def _resolve_memory_db(alias: str) -> Path | None:
    """Resolve an agent alias to its memory database path.

    Memory lives under the agenthost home directory (e.g. ~/.agenthost/memory/<name>),
    not inside the agent code folder.
    """
    agent_path = _resolve_agent_path(alias)
    if agent_path is None:
        return None
    try:
        config = AgentConfig.from_path(agent_path)
        return config.memory_dir / "memory.db"
    except Exception:  # noqa: BLE001
        return None


def iter_monitored_agents(monitored_agents: list[str]) -> Iterator[tuple[str, Path]]:
    """Yield (alias, memory_db_path) for each monitored agent that exists."""
    for alias in monitored_agents:
        if not alias or alias.upper() == "USER_MEMORY":
            continue
        db_path = _resolve_memory_db(alias)
        if db_path is not None and db_path.exists():
            yield alias, db_path


def read_messages_since(
    alias: str,
    db_path: Path,
    since: str,
) -> list[MemoryMessage]:
    """Read all messages from an agent's memory.db after the given ISO timestamp."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT thread_id, role, content, created_at
            FROM messages
            WHERE datetime(created_at) > datetime(?)
            ORDER BY datetime(created_at), id
            """,
            (since,),
        ).fetchall()
    finally:
        conn.close()

    return [
        MemoryMessage(
            agent_alias=alias,
            thread_id=row["thread_id"],
            role=row["role"],
            content=row["content"],
            created_at=row["created_at"],
        )
        for row in rows
    ]
