"""Summarize income and expenses, optionally converted to USD."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

import pandas as pd

_tools_dir = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_store", str(_tools_dir / "_store.py"))
_store_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_store_module)

logger = logging.getLogger(__name__)


def get_summary(
    time_range: str = "",
    category: str = "",
    currency: str = "USD",
    group_by: str = "category",
) -> dict[str, Any]:
    """Summarize income and expenses.

    Args:
        time_range: YYYY-MM, YYYY, last_N_months, or empty for all time.
        category: Filter to a category.
        currency: Report currency. Use "USD" for universal view, or "VND"/"AUD"
            for native amounts. Defaults to USD.
        group_by: "category" or "month".

    Returns:
        Totals grouped as requested, plus income/expense/net in the requested currency.
    """
    try:
        if group_by not in {"category", "month"}:
            raise ValueError("group_by must be 'category' or 'month'")

        target_currency = (currency or "USD").strip().upper()
        if target_currency not in _store_module.SUPPORTED_CURRENCIES:
            raise ValueError(f"Unsupported currency: {currency}")

        df = _store_module.list_all_entries()
        df = _store_module.filter_entries(df, time_range, category)

        if df.empty:
            return {"summary": {}, "income": 0.0, "expense": 0.0, "net": 0.0, "currency": target_currency}

        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)

        if target_currency == "USD":
            if df.empty:
                df["converted_amount"] = 0.0
            else:
                df["converted_amount"] = df.apply(
                    lambda row: _store_module.convert_to_usd(row["amount"], row["currency"], row["date"]),
                    axis=1,
                )
        else:
            df["converted_amount"] = df["amount"]
            df = df[df["currency"] == target_currency].copy()

        income_total = df[df["type"] == "income"]["converted_amount"].sum()
        expense_total = df[df["type"] == "expense"]["converted_amount"].sum()

        if group_by == "category":
            grouped = (
                df.groupby(["type", "category"])["converted_amount"]
                .sum()
                .unstack(level=0, fill_value=0)
            )
            grouped = grouped.rename(columns={"expense": "expenses", "income": "income"})
            for col in ("expenses", "income"):
                if col not in grouped.columns:
                    grouped[col] = 0.0
            summary = grouped[["expenses", "income"]].to_dict(orient="index")
        else:
            df["month"] = df["date"].str[:7]
            grouped = (
                df.groupby(["type", "month"])["converted_amount"]
                .sum()
                .unstack(level=0, fill_value=0)
            )
            grouped = grouped.rename(columns={"expense": "expenses", "income": "income"})
            for col in ("expenses", "income"):
                if col not in grouped.columns:
                    grouped[col] = 0.0
            summary = grouped[["expenses", "income"]].to_dict(orient="index")

        return {
            "summary": summary,
            "income": float(income_total),
            "expense": float(expense_total),
            "net": float(income_total - expense_total),
            "currency": target_currency,
            "time_range": time_range or "all time",
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error getting summary")
        return {"error": f"Failed to get summary: {type(exc).__name__}: {exc}"}
