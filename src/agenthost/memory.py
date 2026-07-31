"""SQLite-backed memory for an agent with RAG retrieval."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from agenthost.config import _estimate_tokens

if TYPE_CHECKING:
    from agenthost.config import AgentConfig
    from agenthost.embeddings import EmbeddingClient


class AgentMemory:
    """SQLite-backed memory: conversation history + embeddings + key/value store.

    Recent messages are kept verbatim. Older messages are chunked, embedded, and
    stored for semantic retrieval, so the agent can recall relevant context
    beyond the recent-message window.
    """

    def __init__(
        self,
        config: AgentConfig,
        embedding_client: EmbeddingClient | None = None,
    ):
        self.config = config
        self.embedding_client = embedding_client
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
            conn.executescript("""
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
                CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);

                CREATE TABLE IF NOT EXISTS embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    thread_id TEXT NOT NULL,
                    message_index INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    role TEXT,
                    chunk_text TEXT,
                    embedding TEXT,
                    created_at DATETIME,
                    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_embeddings_thread ON embeddings(thread_id);
                CREATE INDEX IF NOT EXISTS idx_embeddings_message ON embeddings(message_id);

                CREATE TABLE IF NOT EXISTS kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS task_plans (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_task_plans_thread
                    ON task_plans(thread_id);
                CREATE INDEX IF NOT EXISTS idx_task_plans_status
                    ON task_plans(status);

                CREATE TABLE IF NOT EXISTS task_steps (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL REFERENCES task_plans(id) ON DELETE CASCADE,
                    step_number INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    assigned_toolbox TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    result_summary TEXT,
                    result_artifact_id TEXT,
                    result_is_truncated INTEGER DEFAULT 0,
                    timeout_seconds INTEGER DEFAULT 600,
                    max_retries INTEGER DEFAULT 3,
                    retry_count INTEGER DEFAULT 0,
                    retry_strategy TEXT DEFAULT 'retry',
                    fallback_step_id TEXT REFERENCES task_steps(id),
                    error_message TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    started_at TEXT,
                    completed_at TEXT,
                    UNIQUE(plan_id, step_number)
                );
                CREATE INDEX IF NOT EXISTS idx_task_steps_plan
                    ON task_steps(plan_id);

                CREATE TABLE IF NOT EXISTS task_step_dependencies (
                    step_id TEXT NOT NULL REFERENCES task_steps(id) ON DELETE CASCADE,
                    depends_on_step_id TEXT NOT NULL REFERENCES task_steps(id) ON DELETE CASCADE,
                    PRIMARY KEY (step_id, depends_on_step_id)
                );
                CREATE INDEX IF NOT EXISTS idx_task_step_deps_depends
                    ON task_step_dependencies(depends_on_step_id);
                """)
            conn.commit()

    def get_messages(self, thread_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, role, content, tool_calls, tool_call_id, name, created_at FROM messages "
                "WHERE thread_id = ? ORDER BY id",
                (thread_id,),
            ).fetchall()

        messages: list[dict[str, Any]] = []
        for row in rows:
            msg: dict[str, Any] = {
                "id": row["id"],
                "role": row["role"],
                "created_at": row["created_at"],
            }
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
        """Repair a conversation thread so it can be sent to the LLM.

        Instead of deleting assistant tool_calls that lack matching tool
        responses (e.g. after a /stop), this keeps the assistant message and
        lets ``_normalize_messages`` append synthetic tool results. That
        preserves the assistant's intent and any partial results.

        Removes:
        - Following tool rows for corrupted assistant tool_calls that have no
          valid tool_call IDs.
        - Tool messages without a preceding assistant message that declared the
          matching tool_call.

        Returns the number of messages removed.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, role, tool_calls, tool_call_id FROM messages "
                "WHERE thread_id = ? ORDER BY id",
                (thread_id,),
            ).fetchall()

        ids_to_delete: set[int] = set()
        ids_to_strip_tool_calls: set[int] = set()

        i = 0
        while i < len(rows):
            row = rows[i]
            if row["role"] != "assistant" or not row["tool_calls"]:
                i += 1
                continue

            tool_calls = json.loads(row["tool_calls"])
            expected_ids = {tc.get("id") for tc in tool_calls if tc.get("id")}

            j = i + 1
            while j < len(rows) and rows[j]["role"] == "tool":
                j += 1
            following_tool_rows = rows[i + 1 : j]

            if not expected_ids:
                ids_to_strip_tool_calls.add(row["id"])
                for tool_row in following_tool_rows:
                    ids_to_delete.add(tool_row["id"])

            i = j

        declared_tool_call_ids: set[str] = set()
        for row in rows:
            if row["id"] in ids_to_delete:
                continue
            if row["role"] == "assistant" and row["tool_calls"]:
                if row["id"] in ids_to_strip_tool_calls:
                    continue
                tool_calls = json.loads(row["tool_calls"])
                for tc in tool_calls:
                    if tc.get("id"):
                        declared_tool_call_ids.add(tc["id"])
            elif row["role"] == "tool":
                if row["tool_call_id"] not in declared_tool_call_ids:
                    ids_to_delete.add(row["id"])

        modified_count = 0
        with self._connect() as conn:
            if ids_to_strip_tool_calls:
                for msg_id in ids_to_strip_tool_calls:
                    conn.execute(
                        "UPDATE messages SET tool_calls = NULL WHERE id = ?",
                        (msg_id,),
                    )
                modified_count += len(ids_to_strip_tool_calls)

            if ids_to_delete:
                placeholders = ",".join("?" * len(ids_to_delete))
                conn.execute(
                    f"DELETE FROM messages WHERE id IN ({placeholders})",
                    tuple(ids_to_delete),
                )
                modified_count += len(ids_to_delete)

            conn.commit()
        return modified_count

    async def append_message(self, thread_id: str, message: dict[str, Any]) -> None:
        """Store a message and its embedding chunks.

        Embeddings are generated asynchronously via the configured embedding
        client. If the client is unavailable, the message is still stored so the
        conversation can continue, but it will not be retrievable via RAG.
        """
        tool_calls = (
            json.dumps(message.get("tool_calls")) if message.get("tool_calls") else None
        )
        created_at = message.get("created_at") or datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO messages (thread_id, role, content, tool_calls, tool_call_id, name, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    message["role"],
                    message.get("content"),
                    tool_calls,
                    message.get("tool_call_id"),
                    message.get("name"),
                    created_at,
                ),
            )
            message_id = cursor.lastrowid
            conn.commit()

        await self._embed_and_store_message(thread_id, message_id, message, created_at)

    async def _embed_and_store_message(
        self,
        thread_id: str,
        message_id: int,
        message: dict[str, Any],
        created_at: str,
    ) -> None:
        """Chunk a message, embed the chunks, and store them."""
        if self.embedding_client is None:
            return

        text = _message_to_text(message)
        if not text.strip():
            return

        chunk_size = self.config.memory.chunk_size
        overlap = chunk_size // 4
        chunks = _chunk_text(text, chunk_size, overlap)
        if not chunks:
            return

        result = await self.embedding_client.embed(chunks)
        if "error" in result:
            return

        embeddings = result.get("embeddings", [])
        if len(embeddings) != len(chunks):
            return

        message_index = self._message_index(thread_id, message_id)

        with self._connect() as conn:
            for chunk_index, (chunk_text, embedding) in enumerate(
                zip(chunks, embeddings)
            ):
                conn.execute(
                    "INSERT INTO embeddings "
                    "(message_id, thread_id, message_index, chunk_index, role, chunk_text, embedding, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        message_id,
                        thread_id,
                        message_index,
                        chunk_index,
                        message.get("role"),
                        chunk_text,
                        json.dumps(embedding, default=float),
                        created_at,
                    ),
                )
            conn.commit()

    def _message_index(self, thread_id: str, message_id: int) -> int:
        """Return the 0-based index of a message in its thread."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE thread_id = ? AND id < ?",
                (thread_id, message_id),
            ).fetchone()
        return row[0] if row else 0

    async def rewrite_thread(
        self, thread_id: str, messages: list[dict[str, Any]]
    ) -> None:
        """Replace all messages for a thread and regenerate embeddings."""
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
            conn.commit()

        for msg in messages:
            await self.append_message(thread_id, msg)

    async def retrieve_relevant_chunks(
        self,
        thread_id: str,
        query_text: str,
        exclude_message_ids: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Return RAG chunks relevant to the query, ranked and diversified.

        The ranking combines semantic similarity, recency, and a small role
        weight. Maximal Marginal Relevance (MMR) is then applied to balance
        relevance with diversity.

        Results are limited by both ``memory.max_chunks`` and
        ``memory.budget_tokens``.
        """
        if self.embedding_client is None:
            return []

        query_result = await self.embedding_client.embed_query(query_text)
        if "error" in query_result:
            return []
        query_embedding = query_result.get("embedding")
        if query_embedding is None:
            return []

        candidates = self._load_candidates(thread_id, exclude_message_ids)
        if not candidates:
            return []

        total_messages = self._message_count(thread_id)
        scored = self._score_candidates(candidates, query_embedding, total_messages)
        selected = self._mmr_select(
            scored, self.config.memory.max_chunks, self.config.memory.mmr_lambda
        )

        # Trim by token budget while preserving MMR order.
        selected = self._trim_by_token_budget(selected)
        return selected

    def _load_candidates(
        self,
        thread_id: str,
        exclude_message_ids: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Load the most recent embeddable chunks for a thread, capped by config.

        Only the ``memory.max_pool_chunks`` most recent embeddings are loaded
        so retrieval latency stays bounded for very long threads.
        """
        exclude = exclude_message_ids or set()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, message_id, message_index, chunk_index, role, chunk_text, embedding, created_at "
                "FROM embeddings WHERE thread_id = ? ORDER BY created_at DESC LIMIT ?",
                (thread_id, self.config.memory.max_pool_chunks),
            ).fetchall()

        candidates: list[dict[str, Any]] = []
        for row in rows:
            if row["message_id"] in exclude:
                continue
            try:
                embedding = json.loads(row["embedding"])
            except (json.JSONDecodeError, TypeError):
                continue
            candidates.append(
                {
                    "id": row["id"],
                    "message_id": row["message_id"],
                    "message_index": row["message_index"],
                    "chunk_index": row["chunk_index"],
                    "role": row["role"],
                    "chunk_text": row["chunk_text"],
                    "embedding": embedding,
                    "created_at": row["created_at"],
                }
            )
        return candidates

    def _message_count(self, thread_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        return row[0] if row else 0

    def _score_candidates(
        self,
        candidates: list[dict[str, Any]],
        query_embedding: list[float],
        total_messages: int,
    ) -> list[dict[str, Any]]:
        """Score chunks by similarity, recency, and role."""
        cfg = self.config.memory
        total_messages = max(total_messages, 1)

        # Recency is derived from created_at timestamps, which is robust against
        # deleted or repaired messages. Normalize to [0, 1].
        timestamps: list[float] = []
        for cand in candidates:
            try:
                timestamps.append(
                    datetime.fromisoformat(cand["created_at"]).timestamp()
                )
            except (ValueError, TypeError):
                timestamps.append(0.0)

        min_ts = min(timestamps) if timestamps else 0.0
        max_ts = max(timestamps) if timestamps else 0.0
        ts_range = max_ts - min_ts if max_ts > min_ts else 1.0

        scored: list[dict[str, Any]] = []
        for cand, ts in zip(candidates, timestamps):
            similarity = _cosine_similarity(query_embedding, cand["embedding"])
            recency = (ts - min_ts) / ts_range
            role_weight = _role_weight(cand["role"])

            score = (
                cfg.similarity_weight * similarity
                + cfg.recency_weight * recency
                + cfg.role_weight * role_weight
            )
            scored.append(
                {
                    **cand,
                    "similarity": similarity,
                    "recency": recency,
                    "role_weight": role_weight,
                    "score": score,
                }
            )
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored

    def _mmr_select(
        self,
        scored: list[dict[str, Any]],
        max_results: int,
        lambda_param: float,
    ) -> list[dict[str, Any]]:
        """Select diverse chunks using Maximal Marginal Relevance."""
        if not scored:
            return []
        if len(scored) <= max_results:
            return scored

        selected: list[dict[str, Any]] = []
        remaining = list(scored)

        while remaining and len(selected) < max_results:
            best_idx = 0
            best_mmr_score = -1.0
            for i, cand in enumerate(remaining):
                if not selected:
                    mmr_score = cand["score"]
                else:
                    max_sim = max(
                        _cosine_similarity(cand["embedding"], s["embedding"])
                        for s in selected
                    )
                    mmr_score = (
                        lambda_param * cand["score"] - (1 - lambda_param) * max_sim
                    )
                if mmr_score > best_mmr_score:
                    best_mmr_score = mmr_score
                    best_idx = i

            selected.append(remaining.pop(best_idx))

        return selected

    def _trim_by_token_budget(
        self, selected: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return the prefix of selected chunks that fits within the RAG budget."""
        budget = self.config.memory.budget_tokens
        if budget <= 0:
            return selected

        total = 0
        trimmed: list[dict[str, Any]] = []
        for chunk in selected:
            estimated = _estimate_tokens(
                [{"role": "system", "content": chunk["chunk_text"]}],
                self.config.model,
            )
            if total + estimated > budget and trimmed:
                break
            total += estimated
            trimmed.append(chunk)
        return trimmed

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
            conn.execute("DELETE FROM embeddings")
            conn.commit()

    def clear_thread(self, thread_id: str) -> None:
        """Remove all messages and embeddings for a specific thread."""
        with self._connect() as conn:
            conn.execute("DELETE FROM embeddings WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
            conn.commit()

    def list_threads(self) -> list[dict[str, Any]]:
        """Return all thread IDs with a preview of their latest message."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT thread_id, role, content, created_at
                FROM messages
                WHERE id IN (
                    SELECT MAX(id) FROM messages GROUP BY thread_id
                )
                ORDER BY created_at DESC
                """).fetchall()

        threads: list[dict[str, Any]] = []
        for row in rows:
            preview = row["content"] or f"[{row['role']}]"
            preview = preview.replace("\n", " ")[:120]
            threads.append(
                {
                    "thread_id": row["thread_id"],
                    "latest_message_role": row["role"],
                    "latest_message_preview": preview,
                    "latest_message_at": row["created_at"],
                }
            )
        return threads


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _message_to_text(message: dict[str, Any]) -> str:
    """Convert a stored message into a single embeddable text."""
    role = message.get("role", "unknown")
    content = message.get("content") or ""
    if role == "tool":
        name = message.get("name") or "tool"
        return f"[{role}] {name}: {content}"
    if role == "assistant" and message.get("tool_calls"):
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            calls = " ".join(
                f"{tc.get('function', {}).get('name', 'unknown')}({tc.get('function', {}).get('arguments', '')})"
                for tc in tool_calls
            )
            return f"[{role}] {content} {calls}".strip()
    return f"[{role}] {content}"


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into overlapping chunks by tokens (best-effort).

    Uses tiktoken when available, otherwise falls back to a character heuristic.
    """
    if not text:
        return []

    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        tokens = encoding.encode(text)
        if len(tokens) <= chunk_size:
            return [encoding.decode(tokens)]

        chunks: list[str] = []
        step = max(chunk_size - overlap, 1)
        start = 0
        while start < len(tokens):
            end = min(start + chunk_size, len(tokens))
            chunks.append(encoding.decode(tokens[start:end]))
            if end >= len(tokens):
                break
            start += step
        return chunks
    except Exception:
        # Fallback: approximate tokens with 4 characters per token.
        char_size = chunk_size * 4
        char_overlap = overlap * 4
        if len(text) <= char_size:
            return [text]

        chunks: list[str] = []
        step = max(char_size - char_overlap, 1)
        start = 0
        while start < len(text):
            end = min(start + char_size, len(text))
            chunks.append(text[start:end])
            if end >= len(text):
                break
            start += step
        return chunks


def _role_weight(role: str | None) -> float:
    """Return a small weight boost based on message role."""
    if role == "user":
        return 1.05
    if role == "assistant":
        return 1.0
    if role == "tool":
        return 0.95
    return 1.0


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Return cosine similarity between two vectors.

    Uses numpy when available for ~50× speedup on large candidate pools;
    falls back to pure Python otherwise.
    """
    try:
        import numpy as np

        va = np.asarray(a, dtype=np.float64)
        vb = np.asarray(b, dtype=np.float64)
        dot = float(np.dot(va, vb))
        norm_a = float(np.linalg.norm(va))
        norm_b = float(np.linalg.norm(vb))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)
    except ImportError:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
