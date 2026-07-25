"""Migrate one legacy archive month into the new active format."""

from __future__ import annotations

import importlib.util
import logging
import re
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

_tools_dir = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_store", str(_tools_dir / "_store.py"))
_store_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_store_module)

logger = logging.getLogger(__name__)

ARCHIVE_DIR = _store_module.archive_dir()


def _parse_year_month(filename: str) -> tuple[int, int] | None:
    m = re.match(r"(\d{4})-(\d{2})\.xlsx$", filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def migrate_legacy_month(
    month: str,
    target_currency: str = "USD",
) -> dict[str, Any]:
    """Convert one legacy archive month into the new 4-sheet format.

    Args:
        month: YYYY-MM of the legacy file to migrate (e.g. "2025-06").
        target_currency: Currently unused; kept for future use.

    Returns:
        Summary of migrated transactions, balances, assets, and rates.
    """
    source = ARCHIVE_DIR / f"{month}.xlsx"
    if not source.exists():
        return {"error": f"Legacy file not found: {source}"}

    try:
        df = pd.read_excel(source)
        df.columns = [str(c).strip() for c in df.columns]
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to read legacy file: {exc}"}

    ym_result = _parse_year_month(source.name)
    if ym_result is None:
        return {"error": f"Cannot parse year-month from {source.name}"}
    year, month_num = ym_result

    has_date = any(c in df.columns for c in ("Date", "Day"))
    has_bank = "Bank" in df.columns
    has_notes = "Notes" in df.columns
    has_reason = "Reason" in df.columns

    transactions: list[dict[str, Any]] = []
    balances: list[dict[str, Any]] = []
    last_date = f"{year:04d}-{month_num:02d}-01"

    for idx, row in df.iterrows():
        purchase = row.get("Purchases")
        cost = row.get("Cost")

        if pd.isna(purchase) or str(purchase).strip() == "":
            continue
        if pd.isna(cost):
            continue
        try:
            amount = float(cost)
        except (ValueError, TypeError):
            continue
        if amount == 0:
            continue

        description = str(purchase).strip()

        day_value = row.get("Date") if "Date" in df.columns else row.get("Day") if "Day" in df.columns else None
        if pd.isna(day_value):
            date = last_date
        elif isinstance(day_value, (int, float)):
            day = int(day_value)
            date = f"{year:04d}-{month_num:02d}-{day:02d}" if 1 <= day <= 31 else last_date
        else:
            s = str(day_value).strip()
            try:
                dt = pd.to_datetime(s)
                date = dt.strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001
                date = last_date
        if not date.startswith(f"{year:04d}-{month_num:02d}"):
            date = f"{year:04d}-{month_num:02d}-01"
        last_date = date

        if has_bank and pd.notna(row.get("Bank")):
            bank = str(row.get("Bank")).strip()
        else:
            bank = _store_module.infer_bank_from_balance_columns(df, idx)

        currency = _store_module.bank_default_currency(bank)
        category = _store_module.normalize_category(row.get("Category"))

        note_parts: list[str] = []
        if has_notes and pd.notna(row.get("Notes")):
            note_parts.append(str(row.get("Notes")).strip())
        if has_reason and pd.notna(row.get("Reason")):
            note_parts.append(str(row.get("Reason")).strip())
        note = "; ".join(note_parts)

        transactions.append({
            "id": str(uuid.uuid4()),
            "date": date,
            "description": description,
            "amount": amount,
            "currency": currency,
            "bank": bank,
            "category": category,
            "type": "expense",
            "note": note,
        })

    # Build a single balance row from the last non-empty values in the legacy file.
    balance_row: dict[str, Any] = {"date": f"{year:04d}-{month_num:02d}-01", "chase_usd": None, "westpac_aud": None, "tp_bank_vnd": None}
    for col in df.columns:
        if col == "Chase":
            vals = pd.to_numeric(df[col], errors="coerce").dropna()
            if not vals.empty:
                balance_row["chase_usd"] = float(vals.iloc[-1])
        if col == "Westpac":
            vals = pd.to_numeric(df[col], errors="coerce").dropna()
            if not vals.empty:
                balance_row["westpac_aud"] = float(vals.iloc[-1])
        if col == "TP Bank":
            vals = pd.to_numeric(df[col], errors="coerce").dropna()
            if not vals.empty:
                balance_row["tp_bank_vnd"] = float(vals.iloc[-1])
    if any(v is not None for v in [balance_row["chase_usd"], balance_row["westpac_aud"], balance_row["tp_bank_vnd"]]):
        balances.append(balance_row)

    target = _store_module.entries_dir() / f"{year:04d}-{month_num:02d}.xlsx"
    _store_module.save_workbook(
        target,
        transactions=pd.DataFrame(transactions),
        balances=pd.DataFrame(balances),
    )

    return {
        "success": True,
        "source": str(source),
        "target": str(target),
        "transactions": len(transactions),
        "balances": len(balances),
        "assets": 0,
    }
