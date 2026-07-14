"""ORCHESTRATOR tool: maintain one simple plan per thread."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from agenthost.tools import current_thread_id

# Load the sibling helper module by absolute path so imports work regardless of
# the current working directory.
_TOOLS_DIR = Path(__file__).resolve().parent
_helpers_spec = importlib.util.spec_from_file_location(
    "_helpers", str(_TOOLS_DIR / "_helpers.py")
)
_helpers = importlib.util.module_from_spec(_helpers_spec)
_helpers_spec.loader.exec_module(_helpers)

get_memory = _helpers.get_memory


def plan(steps: list[str] | None = None, complete_step: int | None = None) -> str:
    """Manage the ORCHESTRATOR's plan for the current thread.

    Each thread has exactly one plan: a list of step prompts. Call with no
    arguments to read the current plan. Pass `steps` to replace it, or
    `complete_step` (0-based index) to mark a step done.

    Args:
        steps: Ordered list of step prompts. Replaces any existing plan.
        complete_step: Index of the step to mark as completed.

    Returns:
        The current plan and its completed indices.
    """
    thread_id = current_thread_id.get()
    if thread_id is None:
        return json.dumps({"error": "No active thread."})

    key = f"plan:{thread_id}"
    memory, _config = get_memory()

    current = memory.get(key) or {"steps": [], "completed": []}
    if not isinstance(current, dict):
        current = {"steps": [], "completed": []}

    if steps is not None:
        if not isinstance(steps, list):
            return json.dumps({"error": "steps must be a list of strings."})
        current = {"steps": [str(s) for s in steps], "completed": []}

    if complete_step is not None:
        try:
            idx = int(complete_step)
        except (TypeError, ValueError):
            return json.dumps({"error": "complete_step must be an integer."})
        if idx < 0 or idx >= len(current["steps"]):
            return json.dumps(
                {"error": f"Step index {idx} is out of range (0-{len(current['steps']) - 1})."}
            )
        if idx not in current["completed"]:
            current["completed"].append(idx)
            current["completed"].sort()

    memory.set(key, current)
    return json.dumps({"success": True, "plan": current}, indent=2)
