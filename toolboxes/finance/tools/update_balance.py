"""Update daily account balances."""

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


def update_balance(
    date: str,
    chase_usd: float | int | str = "",
    westpac_aud: float | int | str = "",
    tp_bank_vnd: float | int | str = "",
) -> dict[str, Any]:
    """Update the running balances for Chase, Westpac, and TP Bank on a date.

    Args:
        date: Date in YYYY-MM-DD format.
        chase_usd: Chase balance in USD (optional).
        westpac_aud: Westpac balance in AUD (optional).
        tp_bank_vnd: TP Bank balance in VND (optional).

    Returns:
        Confirmation of the updated balance row.
    """
    try:
        return _store_module.update_balance(
            date=date,
            chase_usd=chase_usd,
            westpac_aud=westpac_aud,
            tp_bank_vnd=tp_bank_vnd,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error updating balance")
        return {"error": f"Failed to update balance: {type(exc).__name__}: {exc}"}
