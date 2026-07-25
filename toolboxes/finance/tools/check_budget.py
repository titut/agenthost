"""Check spending against budget limits, optionally in USD."""

from __future__ import annotations

import importlib.util
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

_tools_dir = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_store", str(_tools_dir / "_store.py"))
_store_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_store_module)

logger = logging.getLogger(__name__)


def check_budget(
    category: str = "",
    currency: str = "USD",
    period: str = "",
) -> dict[str, Any]:
    """Check spending against budget limits.

    Args:
        category: Filter to a category, or empty for all budgets.
        currency: Budget currency. Use "USD" to compare all spending in USD,
            or a native currency to compare only that currency.
        period: "monthly" or "yearly", or empty for all.

    Returns:
        A status report for each matching budget.
    """
    try:
        target_currency = (currency or "USD").strip().upper()
        if target_currency not in _store_module.SUPPORTED_CURRENCIES:
            raise ValueError(f"Unsupported currency: {currency}")

        budgets = _store_module.get_budgets()
        if budgets.empty:
            return {"budgets": [], "message": "No budgets defined."}

        if category and category.strip():
            budgets = budgets[budgets["category"].str.lower() == category.strip().lower()]
        if target_currency != "USD":
            budgets = budgets[budgets["currency"] == target_currency]
        if period and period.strip():
            p = period.strip().lower()
            budgets = budgets[budgets["period"].str.lower() == p]

        if budgets.empty:
            return {"budgets": [], "message": "No budgets match the requested filters."}

        df = _store_module.list_all_entries()
        df = df[df["type"] == "expense"].copy()
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
        df["month"] = df["date"].str[:7]
        df["year"] = df["date"].str[:4]

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

        today = datetime.now()
        results: list[dict[str, Any]] = []

        for _, row in budgets.iterrows():
            cat = str(row["category"]).strip().lower()
            limit = float(row["limit"])
            budget_cur = str(row["currency"]).upper()
            per = str(row["period"]).lower()

            mask = df["category"].str.lower() == cat
            if per == "monthly":
                mask = mask & (df["month"] == f"{today.year:04d}-{today.month:02d}")
            elif per == "yearly":
                mask = mask & (df["year"] == f"{today.year:04d}")

            spent = float(df.loc[mask, "converted_amount"].sum())
            pct = (spent / limit * 100) if limit else 0.0
            status = "ok"
            if pct >= 100:
                status = "exceeded"
            elif pct >= 80:
                status = "warning"

            results.append({
                "category": row["category"],
                "currency": budget_cur,
                "report_currency": target_currency,
                "period": per,
                "limit": limit,
                "spent": round(spent, 2),
                "remaining": round(limit - spent, 2),
                "percent_used": round(pct, 2),
                "status": status,
            })

        return {"budgets": results}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error checking budget")
        return {"error": f"Failed to check budget: {type(exc).__name__}: {exc}"}
