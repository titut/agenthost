"""Update an existing asset or investment entry."""

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


def update_asset(
    asset_id: str,
    date: str = "",
    asset: str = "",
    asset_type: str = "",
    qty: float | int | str = "",
    worth: float | int | str = "",
    currency: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Update an asset by id.

    Args:
        asset_id: The asset id.
        date: New date (optional).
        asset: New asset name (optional).
        asset_type: New asset type (optional).
        qty: New quantity (optional).
        worth: New worth (optional).
        currency: New currency (optional).
        note: New note (optional).

    Returns:
        Confirmation or error.
    """
    try:
        return _store_module.update_asset(
            asset_id=asset_id,
            date=date,
            asset=asset,
            asset_type=asset_type,
            qty=qty,
            worth=worth,
            currency=currency,
            note=note,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error updating asset")
        return {"error": f"Failed to update asset: {type(exc).__name__}: {exc}"}
