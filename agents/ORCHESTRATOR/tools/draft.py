"""Draft response tools for the ORCHESTRATOR.

These tools let the ORCHESTRATOR build and refine long responses in a scratchpad
before emitting the final answer. Reading the draft back gives the model explicit
access to its own output so it can catch repetition, missing sections, and other
"lost in the middle" problems.
"""
from __future__ import annotations

from agenthost.memory import AgentMemory
from agenthost.tools import current_agent_config, current_thread_id

_DRAFT_KEY_PREFIX = "orchestrator_draft_response"


def _draft_key(thread_id: str) -> str:
    return f"{_DRAFT_KEY_PREFIX}:{thread_id}"


def write_draft(text: str, append: bool = True) -> dict:
    """Write text to the thread's draft response buffer.

    Use `append=True` (the default) to build the draft section by section. Use
    `append=False` to overwrite the draft with a refined version.
    """
    config = current_agent_config.get()
    thread_id = current_thread_id.get()
    if config is None or thread_id is None:
        return {"error": "Tool must be called within an agent conversation."}

    memory = AgentMemory(config)
    key = _draft_key(thread_id)
    current = memory.get(key, "") if append else ""
    updated = current + text
    memory.set(key, updated)
    return {
        "ok": True,
        "thread_id": thread_id,
        "operation": "append" if append else "replace",
        "length": len(updated),
    }


def read_draft() -> dict:
    """Read the current draft response for this thread."""
    config = current_agent_config.get()
    thread_id = current_thread_id.get()
    if config is None or thread_id is None:
        return {"error": "Tool must be called within an agent conversation."}

    memory = AgentMemory(config)
    key = _draft_key(thread_id)
    draft = memory.get(key, "")
    return {
        "draft": draft,
        "thread_id": thread_id,
        "length": len(draft),
    }


def clear_draft() -> dict:
    """Clear the draft response buffer for this thread."""
    config = current_agent_config.get()
    thread_id = current_thread_id.get()
    if config is None or thread_id is None:
        return {"error": "Tool must be called within an agent conversation."}

    memory = AgentMemory(config)
    key = _draft_key(thread_id)
    memory.set(key, "")
    return {"ok": True, "thread_id": thread_id}
