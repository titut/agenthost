"""Shared storage utilities and helpers for Finance tools.

New active data layout (one workbook per month):
    entries/YYYY-MM.xlsx
        Transactions: id, date, description, amount, currency, bank, category, type, note
        Balances:     date, chase_usd, westpac_aud, tp_bank_vnd
        Assets:       id, date, asset, type, qty, worth, currency
        Rates:        currency, rate_to_usd, date, note

Archive:
    entries/archive/YYYY-MM.xlsx      legacy wide-format files
    entries/archive/historical.db     normalized legacy transactions + assets

Budgets:
    entries/budgets.xlsx
        category, limit, currency, period, created_at, updated_at
"""

from __future__ import annotations

import logging
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from agenthost.home import get_agenthost_home

logger = logging.getLogger(__name__)

SUPPORTED_CURRENCIES = {"USD", "VND", "AUD"}
SUPPORTED_BANKS = {"Chase", "Westpac", "TP Bank"}
BANK_CURRENCY = {"Chase": "USD", "Westpac": "AUD", "TP Bank": "VND"}
CANONICAL_CATEGORIES = {
    "food": "Food",
    "travel": "Travel",
    "necessity": "Necessity",
    "neccessity": "Necessity",
    "neccesity": "Necessity",
    "entertainment": "Entertainment",
    "subscription": "Subscription",
    "gifts": "Gifts",
    "other": "Other",
    "tech": "Tech",
    "technology": "Tech",
    "clothes": "Clothes",
}
VALID_PERIODS = {"monthly", "yearly"}

# Default exchange rates (USD per 1 unit of foreign currency).
DEFAULT_RATES = {
    "VND": 0.000039,
    "AUD": 0.67,
    "USD": 1.0,
}

# Sheet names in active monthly workbooks.
SHEET_TRANSACTIONS = "Transactions"
SHEET_BALANCES = "Balances"
SHEET_ASSETS = "Assets"
SHEET_RATES = "Rates"

TRANSACTION_COLUMNS = [
    "id",
    "date",
    "description",
    "amount",
    "currency",
    "bank",
    "category",
    "type",
    "note",
]
BALANCE_COLUMNS = [
    "date",
    "chase_usd",
    "westpac_aud",
    "tp_bank_vnd",
]
ASSET_COLUMNS = [
    "id",
    "date",
    "asset",
    "type",
    "qty",
    "worth",
    "currency",
    "note",
]
RATE_COLUMNS = [
    "currency",
    "rate_to_usd",
    "date",
    "note",
]
BUDGET_COLUMNS = [
    "category",
    "limit",
    "currency",
    "period",
    "created_at",
    "updated_at",
]


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def entries_dir() -> Path:
    return get_agenthost_home() / "entries"


def archive_dir() -> Path:
    return entries_dir() / "archive"


def archive_db_path() -> Path:
    return archive_dir() / "historical.db"


def output_dir() -> Path:
    return get_agenthost_home() / "output"


def month_path(date_str: str) -> Path:
    month = normalize_date(date_str)[:7]
    return entries_dir() / f"{month}.xlsx"


def budget_path() -> Path:
    return entries_dir() / "budgets.xlsx"


# ---------------------------------------------------------------------------
# Validation / normalization
# ---------------------------------------------------------------------------


def normalize_date(date_str: str) -> str:
    if not date_str:
        return datetime.now().strftime("%Y-%m-%d")
    date_str = str(date_str).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        # Try to parse and reformat.
        try:
            dt = pd.to_datetime(date_str)
            return dt.strftime("%Y-%m-%d")
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Date must be YYYY-MM-DD, got: {date_str}") from exc
    return date_str


def normalize_currency(currency: str) -> str:
    currency = (currency or "USD").strip().upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError(
            f"Unsupported currency '{currency}'. Use one of: {sorted(SUPPORTED_CURRENCIES)}"
        )
    return currency


def normalize_bank(bank: str) -> str:
    bank = (bank or "Chase").strip()
    # Allow common aliases.
    aliases = {
        "chase": "Chase",
        "westpac": "Westpac",
        "tp bank": "TP Bank",
        "tpbank": "TP Bank",
    }
    bank = aliases.get(bank.lower(), bank)
    if bank not in SUPPORTED_BANKS:
        raise ValueError(f"Unsupported bank '{bank}'. Use one of: {sorted(SUPPORTED_BANKS)}")
    return bank


def bank_default_currency(bank: str) -> str:
    return BANK_CURRENCY.get(normalize_bank(bank), "USD")


def infer_bank_from_balance_columns(df: pd.DataFrame, row_index: int) -> str:
    """Look at balance columns like Chase/Westpac/TP Bank to guess the bank."""
    for col in df.columns:
        col_str = str(col).strip()
        if col_str in BANK_CURRENCY:
            value = df.iloc[row_index][col]
            if pd.notna(value):
                if row_index > 0:
                    prev = df.iloc[row_index - 1][col]
                    if pd.notna(prev) and value != prev:
                        return col_str
    return "Chase"  # Default fallback


def normalize_amount(amount: float | int | str) -> float:
    try:
        value = float(amount)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Amount must be numeric, got: {amount}") from exc
    if value < 0:
        raise ValueError(f"Amount must be non-negative, got: {value}")
    return value


def normalize_type(entry_type: str) -> str:
    entry_type = (entry_type or "expense").strip().lower()
    if entry_type not in {"income", "expense"}:
        raise ValueError(f"Type must be 'income' or 'expense', got: {entry_type}")
    return entry_type


def normalize_category(category: str) -> str:
    category = (category or "Other").strip().lower()
    return CANONICAL_CATEGORIES.get(category, "Other")


# ---------------------------------------------------------------------------
# Active workbook helpers
# ---------------------------------------------------------------------------


def _load_sheet(path: Path, sheet_name: str, columns: list[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns)
    try:
        df = pd.read_excel(path, sheet_name=sheet_name, dtype=str)
    except (ValueError, KeyError):
        # Sheet missing.
        return pd.DataFrame(columns=columns)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to read {path} [{sheet_name}]: {exc}") from exc

    for col in columns:
        if col not in df.columns:
            df[col] = ""
    return df[columns].copy()


def _coerce_transactions(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
    df["date"] = df["date"].astype(str)
    df["currency"] = df["currency"].astype(str).str.upper()
    df["bank"] = df["bank"].astype(str)
    df["category"] = df["category"].astype(str)
    df["type"] = df["type"].astype(str)
    return df


def _coerce_balances(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ("chase_usd", "westpac_aud", "tp_bank_vnd"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = df["date"].astype(str)
    return df


def _coerce_assets(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    df["worth"] = pd.to_numeric(df["worth"], errors="coerce")
    df["date"] = df["date"].astype(str)
    df["currency"] = df["currency"].astype(str).str.upper()
    return df


def _coerce_rates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rate_to_usd"] = pd.to_numeric(df["rate_to_usd"], errors="coerce")
    df["currency"] = df["currency"].astype(str).str.upper()
    df["date"] = df["date"].astype(str)
    return df


def load_transactions(path: Path) -> pd.DataFrame:
    return _coerce_transactions(_load_sheet(path, SHEET_TRANSACTIONS, TRANSACTION_COLUMNS))


def load_balances(path: Path) -> pd.DataFrame:
    return _coerce_balances(_load_sheet(path, SHEET_BALANCES, BALANCE_COLUMNS))


def load_assets(path: Path) -> pd.DataFrame:
    return _coerce_assets(_load_sheet(path, SHEET_ASSETS, ASSET_COLUMNS))


def load_rates(path: Path) -> pd.DataFrame:
    df = _coerce_rates(_load_sheet(path, SHEET_RATES, RATE_COLUMNS))
    if df.empty:
        # Seed with defaults.
        df = pd.DataFrame([
            {"currency": cur, "rate_to_usd": rate, "date": "", "note": "default"}
            for cur, rate in DEFAULT_RATES.items()
        ])
    return df


def save_workbook(
    path: Path,
    transactions: pd.DataFrame | None = None,
    balances: pd.DataFrame | None = None,
    assets: pd.DataFrame | None = None,
    rates: pd.DataFrame | None = None,
) -> None:
    """Write all sheets to a monthly workbook, preserving any sheet not provided."""
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        if transactions is None:
            transactions = load_transactions(path)
        if balances is None:
            balances = load_balances(path)
        if assets is None:
            assets = load_assets(path)
        if rates is None:
            rates = load_rates(path)

    if transactions is None:
        transactions = pd.DataFrame(columns=TRANSACTION_COLUMNS)
    if balances is None:
        balances = pd.DataFrame(columns=BALANCE_COLUMNS)
    if assets is None:
        assets = pd.DataFrame(columns=ASSET_COLUMNS)
    if rates is None:
        rates = pd.DataFrame([
            {"currency": cur, "rate_to_usd": rate, "date": "", "note": "default"}
            for cur, rate in DEFAULT_RATES.items()
        ])

    with pd.ExcelWriter(path, engine="openpyxl") as writer:  # type: ignore[call-arg]
        transactions[TRANSACTION_COLUMNS].to_excel(writer, sheet_name=SHEET_TRANSACTIONS, index=False)
        balances[BALANCE_COLUMNS].to_excel(writer, sheet_name=SHEET_BALANCES, index=False)
        assets[ASSET_COLUMNS].to_excel(writer, sheet_name=SHEET_ASSETS, index=False)
        rates[RATE_COLUMNS].to_excel(writer, sheet_name=SHEET_RATES, index=False)


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


def add_entry_to_store(
    date: str,
    description: str,
    amount: float | int | str,
    currency: str = "",
    bank: str = "",
    entry_type: str = "expense",
    category: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Append a transaction to the correct monthly workbook."""
    date = normalize_date(date)
    amount = normalize_amount(amount)
    bank = normalize_bank(bank)
    currency = (currency or bank_default_currency(bank)).strip().upper()
    currency = normalize_currency(currency)
    entry_type = normalize_type(entry_type)

    if not description or not description.strip():
        raise ValueError("Description is required.")

    entry = {
        "id": str(uuid.uuid4()),
        "date": date,
        "description": description.strip(),
        "amount": amount,
        "currency": currency,
        "bank": bank,
        "category": normalize_category(category),
        "type": entry_type,
        "note": (note or "").strip(),
    }

    path = month_path(date)
    df = load_transactions(path)
    df = pd.concat([df, pd.DataFrame([entry])], ignore_index=True)
    save_workbook(path, transactions=df)
    logger.info("Added %s entry %s to %s", entry_type, entry["id"], path.name)
    return entry


def update_entry(
    entry_id: str,
    date: str = "",
    description: str = "",
    amount: float | int | str = "",
    currency: str = "",
    bank: str = "",
    entry_type: str = "",
    category: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Update an existing transaction by id."""
    # Search across all monthly files for the entry.
    for path in sorted(entries_dir().glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].xlsx")):
        df = load_transactions(path)
        mask = df["id"] == entry_id
        if not mask.any():
            continue

        if date:
            df.loc[mask, "date"] = normalize_date(date)
        if description:
            df.loc[mask, "description"] = description.strip()
        if amount != "":
            df.loc[mask, "amount"] = normalize_amount(amount)
        if bank:
            df.loc[mask, "bank"] = normalize_bank(bank)
        if currency:
            df.loc[mask, "currency"] = normalize_currency(currency)
        elif bank:
            df.loc[mask, "currency"] = bank_default_currency(bank)
        if entry_type:
            df.loc[mask, "type"] = normalize_type(entry_type)
        if category:
            df.loc[mask, "category"] = normalize_category(category)
        if note:
            df.loc[mask, "note"] = note.strip()

        save_workbook(path, transactions=df)
        return {"success": True, "entry_id": entry_id, "path": str(path)}

    return {"error": f"Entry not found: {entry_id}"}


def list_all_entries() -> pd.DataFrame:
    directory = entries_dir()
    if not directory.exists():
        return pd.DataFrame(columns=TRANSACTION_COLUMNS)

    frames: list[pd.DataFrame] = []
    for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].xlsx")):
        try:
            frames.append(load_transactions(path))
        except Exception:  # noqa: BLE001
            logger.exception("Skipping unreadable file %s", path)
    if not frames:
        return pd.DataFrame(columns=TRANSACTION_COLUMNS)
    return pd.concat(frames, ignore_index=True)


def get_existing_categories() -> list[str]:
    df = list_all_entries()
    if df.empty:
        return sorted(set(CANONICAL_CATEGORIES.values()))
    cats = df["category"].dropna().astype(str).str.strip().str.lower()
    return sorted({c for c in cats if c})


# ---------------------------------------------------------------------------
# Balances
# ---------------------------------------------------------------------------


def update_balance(
    date: str,
    chase_usd: float | int | str = "",
    westpac_aud: float | int | str = "",
    tp_bank_vnd: float | int | str = "",
) -> dict[str, Any]:
    """Update or insert a daily balance row."""
    date = normalize_date(date)
    path = month_path(date)

    def _to_float(value):
        if value == "" or pd.isna(value):
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Balance must be numeric, got: {value}") from exc

    df = load_balances(path)
    mask = df["date"] == date
    row = {
        "date": date,
        "chase_usd": _to_float(chase_usd),
        "westpac_aud": _to_float(westpac_aud),
        "tp_bank_vnd": _to_float(tp_bank_vnd),
    }

    if mask.any():
        for col in BALANCE_COLUMNS:
            if row[col] is not None:
                df.loc[mask, col] = row[col]
    else:
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    save_workbook(path, balances=df)
    return {"success": True, "date": date, **row}


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


def add_asset(
    date: str,
    asset: str,
    asset_type: str = "",
    qty: float | int | str = 0,
    worth: float | int | str = 0,
    currency: str = "USD",
    note: str = "",
) -> dict[str, Any]:
    date = normalize_date(date)
    if not asset or not asset.strip():
        raise ValueError("Asset name is required.")

    entry = {
        "id": str(uuid.uuid4()),
        "date": date,
        "asset": asset.strip(),
        "type": (asset_type or "").strip(),
        "qty": float(qty) if qty != "" else 0.0,
        "worth": float(worth) if worth != "" else 0.0,
        "currency": normalize_currency(currency),
        "note": (note or "").strip(),
    }

    path = month_path(date)
    df = load_assets(path)
    df = pd.concat([df, pd.DataFrame([entry])], ignore_index=True)
    save_workbook(path, assets=df)
    return entry


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
    for path in sorted(entries_dir().glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].xlsx")):
        df = load_assets(path)
        mask = df["id"] == asset_id
        if not mask.any():
            continue

        if date:
            df.loc[mask, "date"] = normalize_date(date)
        if asset:
            df.loc[mask, "asset"] = asset.strip()
        if asset_type:
            df.loc[mask, "type"] = asset_type.strip()
        if qty != "":
            df.loc[mask, "qty"] = float(qty)
        if worth != "":
            df.loc[mask, "worth"] = float(worth)
        if currency:
            df.loc[mask, "currency"] = normalize_currency(currency)
        if note:
            df.loc[mask, "note"] = note.strip()

        save_workbook(path, assets=df)
        return {"success": True, "asset_id": asset_id, "path": str(path)}

    return {"error": f"Asset not found: {asset_id}"}


# ---------------------------------------------------------------------------
# Exchange rates
# ---------------------------------------------------------------------------


def set_exchange_rate(
    currency: str,
    rate_to_usd: float | int | str,
    date: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Set the USD conversion rate for a currency in the relevant monthly file."""
    currency = normalize_currency(currency)
    try:
        rate = float(rate_to_usd)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"rate_to_usd must be numeric, got: {rate_to_usd}") from exc

    date = normalize_date(date) if date else ""
    # Rates are stored in the current month file if no date given, otherwise the month of the date.
    path = month_path(date) if date else month_path(datetime.now().strftime("%Y-%m-%d"))

    df = load_rates(path)
    mask = df["currency"] == currency
    if date:
        mask = mask & (df["date"] == date)

    if mask.any():
        df.loc[mask, "rate_to_usd"] = rate
        df.loc[mask, "note"] = (note or "").strip()
        if date:
            df.loc[mask, "date"] = date
    else:
        new_row = {
            "currency": currency,
            "rate_to_usd": rate,
            "date": date,
            "note": (note or "").strip(),
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    save_workbook(path, rates=df)
    return {"currency": currency, "rate_to_usd": rate, "date": date}


def get_rate_for_date(currency: str, date_str: str) -> float:
    """Return the best available USD rate for a currency on a given date."""
    currency = normalize_currency(currency)
    if currency == "USD":
        return 1.0

    date = normalize_date(date_str)
    path = month_path(date)
    df = load_rates(path)
    df = df[df["currency"] == currency]
    if df.empty:
        return DEFAULT_RATES.get(currency, 0.0)

    # Prefer exact date match, else blank/most recent in month.
    exact = df[df["date"] == date]
    if not exact.empty:
        return float(exact.iloc[0]["rate_to_usd"])

    blank = df[df["date"] == ""]
    if not blank.empty:
        return float(blank.iloc[0]["rate_to_usd"])

    return float(df.iloc[0]["rate_to_usd"])


def convert_to_usd(amount: float, currency: str, date_str: str) -> float:
    rate = get_rate_for_date(currency, date_str)
    return amount * rate


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


def load_budgets_file() -> pd.DataFrame:
    path = budget_path()
    if not path.exists():
        df = pd.DataFrame(columns=BUDGET_COLUMNS)
        df = df.astype({"limit": float})
        return df
    try:
        df = pd.read_excel(path, dtype={"category": str, "currency": str, "period": str})
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to read {path}: {exc}") from exc

    for col in BUDGET_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df["limit"] = pd.to_numeric(df["limit"], errors="coerce")
    return df[BUDGET_COLUMNS].copy()


def save_budgets_file(df: pd.DataFrame) -> None:
    path = budget_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    df[BUDGET_COLUMNS].to_excel(path, index=False, engine="openpyxl")


def get_budgets() -> pd.DataFrame:
    return load_budgets_file()


def set_budget_in_store(
    category: str,
    limit: float | int | str,
    currency: str = "USD",
    period: str = "monthly",
) -> dict[str, Any]:
    if not category or not category.strip():
        raise ValueError("Category is required.")
    limit = normalize_amount(limit)
    if limit == 0:
        raise ValueError("Budget limit must be positive.")
    currency = normalize_currency(currency)
    period = (period or "monthly").strip().lower()
    if period not in VALID_PERIODS:
        raise ValueError(f"Period must be one of {sorted(VALID_PERIODS)}, got: {period}")

    df = load_budgets_file()
    now = datetime.now().isoformat(timespec="seconds")
    category_norm = category.strip().lower()
    mask = (df["category"].str.lower() == category_norm) & (df["currency"] == currency)

    if mask.any():
        df.loc[mask, "limit"] = limit
        df.loc[mask, "period"] = period
        df.loc[mask, "updated_at"] = now
    else:
        new_row = {
            "category": category.strip(),
            "limit": limit,
            "currency": currency,
            "period": period,
            "created_at": now,
            "updated_at": now,
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    save_budgets_file(df)
    return {
        "category": category.strip(),
        "limit": limit,
        "currency": currency,
        "period": period,
    }


# ---------------------------------------------------------------------------
# Time-range parsing
# ---------------------------------------------------------------------------


def parse_time_range(time_range: str = "") -> tuple[str, str] | None:
    """Return (start_date, end_date) inclusive, or None for all time."""
    from datetime import timedelta

    time_range = (time_range or "").strip()
    if not time_range:
        return None

    today = datetime.now()

    if time_range.startswith("last_") and time_range.endswith("_months"):
        try:
            n = int(time_range[5:-7])
        except ValueError:
            raise ValueError(f"Invalid time range: {time_range}") from None
        if n < 1:
            raise ValueError("last_N_months requires N >= 1")
        start_year = today.year
        start_month = today.month - n + 1
        while start_month <= 0:
            start_year -= 1
            start_month += 12
        start = f"{start_year:04d}-{start_month:02d}-01"
        if today.month == 12:
            end_year = today.year + 1
            end_month = 1
        else:
            end_year = today.year
            end_month = today.month + 1
        end = datetime(end_year, end_month, 1) - timedelta(days=1)
        return start, end.strftime("%Y-%m-%d")

    if len(time_range) == 4 and time_range.isdigit():
        return f"{time_range}-01-01", f"{time_range}-12-31"

    if len(time_range) == 7 and time_range[4] == "-":
        year, month = time_range.split("-")
        next_month = int(month) + 1
        next_year = int(year)
        if next_month == 13:
            next_month = 1
            next_year += 1
        end = datetime(next_year, next_month, 1) - timedelta(days=1)
        return f"{time_range}-01", end.strftime("%Y-%m-%d")

    raise ValueError(
        f"Invalid time range '{time_range}'. Use YYYY-MM, YYYY, last_N_months, or leave empty."
    )


def filter_entries(
    df: pd.DataFrame,
    time_range: str = "",
    category: str = "",
    currency: str = "",
    entry_type: str = "",
    query: str = "",
) -> pd.DataFrame:
    if df.empty:
        return df

    date_range = parse_time_range(time_range)
    if date_range:
        start, end = date_range
        mask = (df["date"] >= start) & (df["date"] <= end)
        df = df[mask].copy()

    if category and category.strip():
        df = df[df["category"].str.lower() == category.strip().lower()].copy()

    if currency and currency.strip():
        cur = currency.strip().upper()
        if cur not in SUPPORTED_CURRENCIES:
            raise ValueError(f"Unsupported currency: {currency}")
        df = df[df["currency"] == cur].copy()

    if entry_type and entry_type.strip():
        et = entry_type.strip().lower()
        if et not in {"income", "expense"}:
            raise ValueError(f"Invalid entry_type: {entry_type}")
        df = df[df["type"] == et].copy()

    if query and query.strip():
        q = query.strip().lower()
        mask = (
            df["description"].astype(str).str.lower().str.contains(q, na=False)
            | df["category"].astype(str).str.lower().str.contains(q, na=False)
            | df["note"].astype(str).str.lower().str.contains(q, na=False)
        )
        df = df[mask].copy()

    return df


def entries_to_records(df: pd.DataFrame, limit: int = 100) -> list[dict[str, Any]]:
    if df.empty:
        return []
    df = df.dropna(axis=1, how="all")
    records = df.head(limit).to_dict(orient="records")
    for rec in records:
        for key, value in rec.items():
            if pd.isna(value):
                rec[key] = ""
            elif isinstance(value, (int, float)):
                rec[key] = value
            else:
                rec[key] = str(value)
    return records


# ---------------------------------------------------------------------------
# Archive search
# ---------------------------------------------------------------------------


def search_archive(
    query: str = "",
    time_range: str = "",
    category: str = "",
    bank: str = "",
    currency: str = "",
    entry_type: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """Search the legacy archive SQLite database."""
    db_path = archive_db_path()
    if not db_path.exists():
        return {"count": 0, "returned": 0, "archive": [], "message": "Archive database not found."}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    conditions: list[str] = []
    params: list[Any] = []

    if query and query.strip():
        # Match the query as a whole word in the description.
        q = query.strip()
        conditions.append(
            "(description = ? OR description LIKE ? OR description LIKE ? OR description LIKE ?)"
        )
        params.extend([q, f"{q} %", f"% {q} %", f"% {q}"])
    if category and category.strip():
        conditions.append("category = ?")
        params.append(category.strip().lower().capitalize())
    if bank and bank.strip():
        conditions.append("bank = ?")
        params.append(bank.strip())
    if currency and currency.strip():
        conditions.append("currency = ?")
        params.append(currency.strip().upper())
    if entry_type and entry_type.strip():
        conditions.append("type = ?")
        params.append(entry_type.strip().lower())

    date_range = parse_time_range(time_range)
    if date_range:
        conditions.append("date >= ? AND date <= ?")
        params.extend(date_range)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT * FROM transactions {where_clause} ORDER BY date DESC LIMIT ?"
    params.append(limit)

    rows = cursor.execute(sql, params).fetchall()
    conn.close()

    results = [dict(row) for row in rows]
    return {
        "count": len(results),
        "returned": len(results),
        "time_range": time_range or "all time",
        "archive": results,
    }
