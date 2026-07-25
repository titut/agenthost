"""List all defined budgets."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

_tools_dir = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_store", str(_tools_dir / "_store.py"))
_store_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_store_module)

logger = logging.getLogger(__name__)


def list_budgets() -> dict[str, Any]:
    """List all defined budgets.

    Returns:
        A list of budgets.
    """
    try:
        budgets = _store_module.get_budgets()
        if budgets.empty:
            return {"budgets": []}
        return {"budgets": _store_module.entries_to_records(budgets, limit=1000)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error listing budgets")
        return {"error": f"Failed to list budgets: {type(exc).__name__}: {exc}"}
