"""Set or update a budget limit for a category."""

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


def set_budget(
    category: str,
    limit: float,
    currency: str = "USD",
    period: str = "monthly",
) -> dict[str, Any]:
    """Set or update a budget limit for a category.

    Args:
        category: The spending category.
        limit: Positive numeric limit.
        currency: One of USD, VND, AUD (default USD).
        period: "monthly" or "yearly" (default monthly).

    Returns:
        The saved budget, or an error description.
    """
    try:
        return _store_module.set_budget_in_store(category, limit, currency, period)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error setting budget")
        return {"error": f"Failed to set budget: {type(exc).__name__}: {exc}"}
