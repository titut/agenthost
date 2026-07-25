"""Add a new income or expense transaction."""

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


def add_entry(
    date: str,
    description: str,
    amount: float,
    bank: str = "Chase",
    currency: str = "",
    entry_type: str = "expense",
    category: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Add a new income or expense transaction to the appropriate monthly file.

    Args:
        date: Transaction date in YYYY-MM-DD format (defaults to today if empty).
        description: What the transaction was for.
        amount: Non-negative numeric amount.
        bank: Chase, Westpac, or TP Bank (default Chase).
        currency: USD, VND, or AUD. If empty, inferred from bank.
        entry_type: "income" or "expense" (default expense).
        category: One of Food, Travel, Necessity, Entertainment, Subscription,
            Gifts, Other, Tech, Clothes. Ask the user if uncertain.
        note: Optional extra detail.

    Returns:
        The created entry, or an error description.
    """
    try:
        return _store_module.add_entry_to_store(
            date=date,
            description=description,
            amount=amount,
            bank=bank,
            currency=currency,
            entry_type=entry_type,
            category=category,
            note=note,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error adding entry")
        return {"error": f"Failed to add entry: {type(exc).__name__}: {exc}"}
