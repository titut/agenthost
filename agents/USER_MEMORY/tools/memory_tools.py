"""USER_MEMORY agent tools.

Public tool functions:
    process_recent_messages()
    search_daily_logs(query, top_k)
    get_master_memory()
    list_monitored_agents()
"""

from __future__ import annotations

import importlib.util
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agenthost.agents_config import AgentsConfig
from agenthost.config import AgentConfig
from agenthost.memory import AgentMemory
from agenthost.tools import current_agent_config

from openai import AsyncOpenAI

# Load sibling helper modules by absolute path to avoid sys.path pollution.
_tools_dir = Path(__file__).resolve().parent
_reader_spec = importlib.util.spec_from_file_location(
    "_memory_reader", str(_tools_dir / "_memory_reader.py")
)
_reader_module = importlib.util.module_from_spec(_reader_spec)
_reader_spec.loader.exec_module(_reader_module)
iter_monitored_agents = _reader_module.iter_monitored_agents
read_messages_since = _reader_module.read_messages_since

_vector_spec = importlib.util.spec_from_file_location(
    "_vector_store", str(_tools_dir / "_vector_store.py")
)
_vector_module = importlib.util.module_from_spec(_vector_spec)
_vector_spec.loader.exec_module(_vector_module)
DailyLogVectorStore = _vector_module.DailyLogVectorStore


DAILY_LOG_DIR = "daily_log"
MASTER_MEMORY_FILE = "master_memory.md"
LAST_SUMMARIZED_KEY = "last_summarized_at"
DEFAULT_EMBEDDING_MODEL = "text-embedding-004"


def _get_own_memory() -> AgentMemory:
    """Return an AgentMemory instance for the USER_MEMORY agent itself."""
    config = current_agent_config.get()
    if config is None:
        raise RuntimeError("No agent config available in context.")
    return AgentMemory(config)


def _get_memory_paths() -> tuple[Path, Path, Path]:
    """Return (daily_log_dir, master_memory_path, memory_db_path)."""
    config = current_agent_config.get()
    if config is None:
        raise RuntimeError("No agent config available in context.")
    base = config.memory_dir
    return base / DAILY_LOG_DIR, base / MASTER_MEMORY_FILE, base / "memory.db"


def _get_monitored_agents(config: AgentConfig | None = None) -> list[str]:
    """Return the list of monitored agent aliases from agent.yaml."""
    if config is None:
        config = current_agent_config.get()
    if config is None:
        return []
    monitored = config.extra.get("monitored_agents")
    if isinstance(monitored, list):
        return [str(a).strip() for a in monitored if str(a).strip()]
    return []


def _get_embedding_model(config: AgentConfig) -> str:
    """Return the configured embedding model name."""
    return str(config.extra.get("embedding_model") or DEFAULT_EMBEDDING_MODEL)


def _get_llm_client(config: AgentConfig) -> AsyncOpenAI:
    """Build an AsyncOpenAI client from the agent config."""
    kwargs: dict[str, Any] = {}
    if config.base_url:
        kwargs["base_url"] = config.base_url
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        kwargs["api_key"] = api_key
    return AsyncOpenAI(**kwargs)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_from_iso(iso: str) -> str:
    """Return YYYY-MM-DD portion of an ISO timestamp."""
    return iso[:10]


def _load_master_memory(path: Path) -> str:
    if not path.exists():
        return "# Master Memory\n\n"
    return path.read_text(encoding="utf-8")


def _parse_daily_log_chunks(content: str) -> list[str]:
    """Split a daily log file into chunks, one per run section."""
    if not content.strip():
        return []
    # Split on '## Run at ' headings, keeping the heading with each chunk.
    parts = re.split(r"(?=\n## Run at )", content)
    chunks: list[str] = []
    for part in parts:
        part = part.strip()
        if part.startswith("#"):
            # Skip the file title if it stands alone.
            lines = part.splitlines()
            if len(lines) == 1 and lines[0].startswith("# "):
                continue
        if part:
            chunks.append(part)
    return chunks


def _build_daily_log_prompt(date_str: str, messages: list[dict[str, Any]]) -> str:
    lines = [f"Summarize the following conversation activity for {date_str}.", ""]
    for msg in messages:
        ts = msg.get("created_at", "")
        role = msg.get("role", "")
        agent = msg.get("agent_alias", "")
        thread = msg.get("thread_id", "")
        content = msg.get("content") or ""
        lines.append(f"[{ts}] {agent}/{thread} {role}: {content[:500]}")
    lines.append("")
    lines.append(
        "Write a concise daily log entry in markdown. Focus on what the user did, "
        "asked, or decided. Mention the agent names. Do not add commentary."
    )
    return "\n".join(lines)


def _build_master_memory_prompt(existing: str, messages: list[dict[str, Any]]) -> str:
    lines = [
        "You maintain a living master memory document about the user.",
        "Update the document below using the new conversation messages.",
        "Merge new facts with existing ones, remove contradictions, and keep the "
        "document organized by topic. Be concise.",
        "",
        "# Existing Master Memory",
        existing,
        "",
        "# New Messages",
    ]
    for msg in messages:
        ts = msg.get("created_at", "")
        role = msg.get("role", "")
        agent = msg.get("agent_alias", "")
        content = msg.get("content") or ""
        lines.append(f"[{ts}] {agent} {role}: {content[:500]}")
    lines.append("")
    lines.append("Return the updated master memory document in markdown.")
    return "\n".join(lines)


async def _summarize_with_llm(config: AgentConfig, prompt: str) -> str:
    """Send a prompt to the configured model and return the content."""
    client = _get_llm_client(config)
    try:
        response = await client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": "You are a precise summarization assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=2048,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001
        return f"[Summarization failed: {type(exc).__name__}: {exc}]"


async def _get_embedding(config: AgentConfig, text: str) -> list[float]:
    """Generate an embedding vector for the given text."""
    client = _get_llm_client(config)
    model = _get_embedding_model(config)
    try:
        response = await client.embeddings.create(
            model=model,
            input=text[:8000],
        )
        return response.data[0].embedding
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Embedding failed ({type(exc).__name__}): {exc}") from exc


def _get_vector_store() -> DailyLogVectorStore:
    """Return a vector store backed by the USER_MEMORY memory database."""
    _, _, db_path = _get_memory_paths()
    return DailyLogVectorStore(db_path)


async def _index_daily_logs(config: AgentConfig) -> dict[str, Any]:
    """Read all daily log files, chunk them, and index missing chunks."""
    daily_log_dir, _, _ = _get_memory_paths()
    if not daily_log_dir.exists():
        return {"indexed": 0, "note": "No daily log directory yet."}

    store = _get_vector_store()
    total_indexed = 0

    for log_path in sorted(daily_log_dir.glob("*.md")):
        date_str = log_path.stem
        content = log_path.read_text(encoding="utf-8")
        chunks = _parse_daily_log_chunks(content)
        if not chunks:
            continue
        embeddings = [await _get_embedding(config, chunk) for chunk in chunks]
        total_indexed += store.index_chunks(date_str, chunks, embeddings)

    return {"indexed": total_indexed}


async def process_recent_messages() -> dict[str, Any]:
    """Summarize recent messages from monitored agents and update memory documents.

    Uses a high-water mark stored in the USER_MEMORY agent's own kv store to
    avoid reprocessing messages.

    Returns:
        A summary of what was processed and written.
    """
    config = current_agent_config.get()
    if config is None:
        return {"error": "No agent config available."}

    monitored = _get_monitored_agents(config)
    if not monitored:
        return {"error": "No monitored_agents configured in agent.yaml."}

    memory = _get_own_memory()
    daily_log_dir, master_path, _ = _get_memory_paths()
    daily_log_dir.mkdir(parents=True, exist_ok=True)

    last_summarized = memory.get(LAST_SUMMARIZED_KEY, "1970-01-01T00:00:00+00:00")
    if not isinstance(last_summarized, str):
        last_summarized = "1970-01-01T00:00:00+00:00"

    all_messages: list[dict[str, Any]] = []
    per_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stats: dict[str, int] = {}

    for alias, db_path in iter_monitored_agents(monitored):
        msgs = read_messages_since(alias, db_path, last_summarized)
        stats[alias] = len(msgs)
        for msg in msgs:
            serialized = {
                "agent_alias": msg.agent_alias,
                "thread_id": msg.thread_id,
                "role": msg.role,
                "content": msg.content,
                "created_at": msg.created_at,
            }
            all_messages.append(serialized)
            per_date[_date_from_iso(msg.created_at)].append(serialized)

    if not all_messages:
        return {
            "processed": 0,
            "agents_checked": list(stats.keys()),
            "message_counts": stats,
            "note": "No new messages since last run.",
        }

    # Update daily logs per date.
    updated_dates: list[str] = []
    for date_str in sorted(per_date):
        messages = per_date[date_str]
        prompt = _build_daily_log_prompt(date_str, messages)
        summary = await _summarize_with_llm(config, prompt)

        log_path = daily_log_dir / f"{date_str}.md"
        entry = f"\n## Run at {_iso_now()}\n\n{summary.strip()}\n"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(entry)
        updated_dates.append(date_str)

    # Index the newly written daily log chunks.
    index_result = await _index_daily_logs(config)

    # Update master memory.
    existing_master = _load_master_memory(master_path)
    master_prompt = _build_master_memory_prompt(existing_master, all_messages)
    updated_master = await _summarize_with_llm(config, master_prompt)
    master_path.write_text(updated_master.strip() + "\n", encoding="utf-8")

    # Advance watermark to the newest processed timestamp.
    newest = max(msg["created_at"] for msg in all_messages)
    memory.set(LAST_SUMMARIZED_KEY, newest)

    return {
        "processed": len(all_messages),
        "agents_checked": list(stats.keys()),
        "message_counts": stats,
        "updated_daily_logs": updated_dates,
        "indexed_chunks": index_result.get("indexed", 0),
        "master_memory_updated": True,
        "new_watermark": newest,
    }


async def search_daily_logs(query: str, top_k: int = 5) -> dict[str, Any]:
    """Search daily logs semantically using embeddings.

    Args:
        query: Natural language query, e.g. "what did I work on yesterday?".
        top_k: Number of relevant chunks to return.

    Returns:
        A dict with the query and the top matching daily log chunks.
    """
    config = current_agent_config.get()
    if config is None:
        return {"error": "No agent config available."}

    store = _get_vector_store()
    try:
        query_embedding = await _get_embedding(config, query)
    except RuntimeError as exc:
        return {"error": str(exc)}

    results = store.search(query_embedding, top_k=top_k)
    return {"query": query, "results": results}


def get_master_memory() -> dict[str, Any]:
    """Return the current master memory document."""
    _, master_path, _ = _get_memory_paths()
    return {
        "path": str(master_path),
        "content": _load_master_memory(master_path),
    }


def list_monitored_agents() -> dict[str, Any]:
    """List configured monitored agents and whether their memory.db exists."""
    config = current_agent_config.get()
    if config is None:
        return {"error": "No agent config available."}

    monitored = _get_monitored_agents(config)
    result: list[dict[str, Any]] = []
    for alias, db_path in iter_monitored_agents(monitored):
        result.append({"alias": alias, "memory_db": str(db_path), "exists": True})

    # Also report configured agents that could not be resolved.
    resolved = {r["alias"] for r in result}
    for alias in monitored:
        if alias not in resolved:
            db_path = _reader_module._resolve_memory_db(alias)
            result.append({
                "alias": alias,
                "memory_db": str(db_path) if db_path else None,
                "exists": False,
            })

    return {"monitored_agents": result}
