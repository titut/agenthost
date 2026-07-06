"""Simple SQLite-backed vector store for daily log chunks.

Not a tool module (prefixed with _) — imported by memory_tools.py.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np


DAILY_LOG_CHUNKS_TABLE = """
CREATE TABLE IF NOT EXISTS daily_log_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_hash TEXT UNIQUE NOT NULL,
    date TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_daily_log_chunks_date ON daily_log_chunks(date);
"""


def _chunk_hash(date: str, content: str) -> str:
    """Stable hash for a daily-log chunk so re-indexing is idempotent."""
    return hashlib.sha256(f"{date}:{content}".encode()).hexdigest()[:32]


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Return cosine similarity between two vectors."""
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0:
        return 0.0
    return float(np.dot(a, b) / norm)


class DailyLogVectorStore:
    """Stores text embeddings for daily log entries and performs brute-force
    cosine-similarity search.

    This is intentionally simple: it loads all vectors into memory for each
    query. For a personal daily-log corpus that will stay small, this avoids
    adding a dedicated vector-database dependency.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create the chunks table and index if they do not exist."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.executescript(DAILY_LOG_CHUNKS_TABLE)
            conn.commit()
        finally:
            conn.close()

    def index_chunks(
        self, date: str, chunks: list[str], embeddings: list[list[float]]
    ) -> int:
        """Index chunks with precomputed embeddings. Returns new chunk count."""
        inserted = 0
        conn = sqlite3.connect(str(self.db_path))
        try:
            for content, embedding in zip(chunks, embeddings):
                h = _chunk_hash(date, content)
                try:
                    conn.execute(
                        "INSERT INTO daily_log_chunks (chunk_hash, date, content, embedding) "
                        "VALUES (?, ?, ?, ?)",
                        (h, date, content, json.dumps(embedding)),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass
            conn.commit()
        finally:
            conn.close()
        return inserted

    def search(
        self, query_embedding: list[float], top_k: int = 5
    ) -> list[dict[str, Any]]:
        """Return the top-k chunks most similar to the query embedding."""
        query_vec = np.array(query_embedding, dtype=np.float32)
        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute(
                "SELECT date, content, embedding FROM daily_log_chunks"
            ).fetchall()
        finally:
            conn.close()

        scored: list[tuple[float, str, str]] = []
        for date, content, emb_json in rows:
            emb = np.array(json.loads(emb_json), dtype=np.float32)
            score = _cosine_similarity(query_vec, emb)
            scored.append((score, date, content))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"score": round(score, 4), "date": date, "content": content}
            for score, date, content in scored[:top_k]
        ]
