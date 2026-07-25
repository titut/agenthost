"""Suggest a category for a transaction description."""

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


def categorize(
    description: str,
    existing_categories: str = "",
) -> dict[str, Any]:
    """Suggest a category for a transaction description.

    Args:
        description: The transaction description.
        existing_categories: Comma-separated list of known categories, or empty to auto-detect.

    Returns:
        A suggested category and a list of known categories.
    """
    try:
        if not description or not description.strip():
            raise ValueError("Description is required.")

        if existing_categories and existing_categories.strip():
            known = sorted({c.strip().lower() for c in existing_categories.split(",") if c.strip()})
        else:
            known = _store_module.get_existing_categories()

        desc_lower = description.strip().lower()

        keyword_map = {
            "grocery": ["grocery", "supermarket", "market", "food"],
            "dining": ["restaurant", "cafe", "coffee", "lunch", "dinner", "takeout"],
            "transport": ["uber", "grab", "taxi", "bus", "train", "fuel", "gas"],
            "utilities": ["electricity", "water", "internet", "phone", "bill"],
            "rent": ["rent", "mortgage"],
            "shopping": ["amazon", "shopee", "lazada", "mall", "clothes"],
            "health": ["pharmacy", "doctor", "hospital", "medicine", "clinic"],
            "entertainment": ["netflix", "spotify", "movie", "game", "cinema"],
            "travel": ["flight", "hotel", "booking", "airbnb"],
            "salary": ["salary", "payroll", "wage"],
            "investment": ["dividend", "interest", "stock", "bond"],
        }

        suggestion = None
        for category, keywords in keyword_map.items():
            if any(kw in desc_lower for kw in keywords):
                suggestion = category
                break

        return {
            "description": description.strip(),
            "suggested_category": suggestion,
            "known_categories": known,
            "note": (
                "A category was suggested based on keywords. "
                if suggestion
                else "No strong keyword match; please choose from known categories or propose a new one. "
            )
            + "Confirm the category with the user before applying it.",
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error categorizing")
        return {"error": f"Failed to categorize: {type(exc).__name__}: {exc}"}
