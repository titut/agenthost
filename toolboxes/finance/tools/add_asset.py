"""Add an asset or investment entry."""

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


def add_asset(
    date: str,
    asset: str,
    asset_type: str = "",
    qty: float | int | str = 0,
    worth: float | int | str = 0,
    currency: str = "USD",
    note: str = "",
) -> dict[str, Any]:
    """Add an asset/investment to the monthly file.

    Args:
        date: Date in YYYY-MM-DD format.
        asset: Asset name (e.g. VTI, BTC, house).
        asset_type: Asset type (e.g. stock, crypto, real estate).
        qty: Quantity owned.
        worth: Total worth in the given currency.
        currency: USD, VND, or AUD.
        note: Optional note.

    Returns:
        The created asset, or an error description.
    """
    try:
        return _store_module.add_asset(
            date=date,
            asset=asset,
            asset_type=asset_type,
            qty=qty,
            worth=worth,
            currency=currency,
            note=note,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error adding asset")
        return {"error": f"Failed to add asset: {type(exc).__name__}: {exc}"}
