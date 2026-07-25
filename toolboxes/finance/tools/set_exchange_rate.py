"""Set the USD exchange rate for a currency."""

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


def set_exchange_rate(
    currency: str,
    rate_to_usd: float,
    date: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Set how many USD 1 unit of the currency buys.

    Args:
        currency: VND, AUD, or USD.
        rate_to_usd: USD value of 1 unit of the currency.
            Examples: VND ≈ 0.000039, AUD ≈ 0.67, USD = 1.0.
        date: Optional specific date (YYYY-MM-DD). If empty, applies to the
            whole current month.
        note: Optional note.

    Returns:
        Confirmation of the saved rate.
    """
    try:
        return _store_module.set_exchange_rate(
            currency=currency,
            rate_to_usd=rate_to_usd,
            date=date,
            note=note,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error setting exchange rate")
        return {"error": f"Failed to set exchange rate: {type(exc).__name__}: {exc}"}
