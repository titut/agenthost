"""Compare spending in a period to the historical average, optionally in USD."""

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


def trend_analysis(
    time_range: str = "",
    category: str = "",
    currency: str = "USD",
) -> dict[str, Any]:
    """Compare spending in a period to the historical average.

    Args:
        time_range: YYYY-MM, YYYY, last_N_months, or empty for all time.
        category: Filter to a category.
        currency: Report currency. Defaults to USD for universal comparison.

    Returns:
        Period totals and comparison to historical monthly average.
    """
    try:
        target_currency = (currency or "USD").strip().upper()
        if target_currency not in _store_module.SUPPORTED_CURRENCIES:
            raise ValueError(f"Unsupported currency: {currency}")

        df = _store_module.list_all_entries()
        if df.empty:
            return {"message": "No entries available for trend analysis."}

        df = _store_module.filter_entries(df, time_range=time_range, category=category, entry_type="expense")
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

        df["month"] = df["date"].str[:7]

        if df.empty:
            return {"message": "No expense entries match the requested filters."}

        current_total = df["converted_amount"].sum()

        full = _store_module.list_all_entries()
        full = _store_module.filter_entries(full, category=category, entry_type="expense")
        full["amount"] = pd.to_numeric(full["amount"], errors="coerce").fillna(0)

        if target_currency == "USD":
            if full.empty:
                full["converted_amount"] = 0.0
            else:
                full["converted_amount"] = full.apply(
                    lambda row: _store_module.convert_to_usd(row["amount"], row["currency"], row["date"]),
                    axis=1,
                )
        else:
            full["converted_amount"] = full["amount"]
            full = full[full["currency"] == target_currency].copy()

        full["month"] = full["date"].str[:7]
        monthly_totals = full.groupby("month")["converted_amount"].sum()

        if len(monthly_totals) <= 1:
            historical_avg = float(monthly_totals.iloc[0]) if len(monthly_totals) == 1 else 0.0
        else:
            historical_avg = float(monthly_totals.mean())

        difference = float(current_total) - historical_avg
        pct = 0.0
        if historical_avg != 0:
            pct = (difference / historical_avg) * 100

        return {
            "current_period_total": float(current_total),
            "historical_monthly_average": historical_avg,
            "difference": difference,
            "percent_change": round(pct, 2),
            "currency": target_currency,
            "time_range": time_range or "all time",
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error in trend analysis")
        return {"error": f"Failed trend analysis: {type(exc).__name__}: {exc}"}
