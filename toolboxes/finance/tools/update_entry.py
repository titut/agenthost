"""Update an existing transaction."""

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


def update_entry(
    entry_id: str,
    date: str = "",
    description: str = "",
    amount: float | int | str = "",
    bank: str = "",
    currency: str = "",
    entry_type: str = "",
    category: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Update an existing transaction by id.

    Only provided fields are changed.

    Args:
        entry_id: The transaction id.
        date: New date (optional).
        description: New description (optional).
        amount: New amount (optional).
        bank: New bank (optional).
        currency: New currency (optional).
        entry_type: New type (optional).
        category: New category (optional).
        note: New note (optional).

    Returns:
        Confirmation or error.
    """
    try:
        return _store_module.update_entry(
            entry_id=entry_id,
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
        logger.exception("Error updating entry")
        return {"error": f"Failed to update entry: {type(exc).__name__}: {exc}"}
