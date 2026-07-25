"""Export filtered entries to a local report file."""

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


def export_report(
    time_range: str = "",
    format: str = "xlsx",
    category: str = "",
    currency: str = "",
) -> dict[str, Any]:
    """Export filtered entries to a local report file.

    Args:
        time_range: YYYY-MM, YYYY, last_N_months, or empty for all time.
        format: "xlsx" or "txt".
        category: Filter to a category.
        currency: Filter to a currency.

    Returns:
        Path to the saved report and a summary.
    """
    try:
        fmt = (format or "xlsx").strip().lower()
        if fmt not in {"xlsx", "txt"}:
            raise ValueError("format must be 'xlsx' or 'txt'")

        df = _store_module.list_all_entries()
        df = _store_module.filter_entries(df, time_range, category, currency)
        df = df.sort_values(["date"], ascending=[False])

        out_dir = _store_module.output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_range = time_range.replace(" ", "_") if time_range else "all"
        filename = f"finance_report_{safe_range}_{suffix}.{fmt}"
        path = out_dir / filename

        if df.empty:
            content = "No entries match the requested filters."
            if fmt == "xlsx":
                pd.DataFrame({"message": [content]}).to_excel(path, index=False, engine="openpyxl")
            else:
                path.write_text(content, encoding="utf-8")
            return {"path": str(path), "count": 0, "format": fmt}

        if fmt == "xlsx":
            df.to_excel(path, index=False, engine="openpyxl")
        else:
            lines = [f"Finance Report ({time_range or 'all time'})", "=" * 50, ""]
            for _, row in df.iterrows():
                lines.append(
                    f"{row['date']} | {row['type']:8} | {row['currency']:3} {row['amount']:>12.2f} | "
                    f"{row['bank']:10} | {row['category']:15} | {row['description']}"
                )
            path.write_text("\n".join(lines), encoding="utf-8")

        return {
            "path": str(path),
            "count": len(df),
            "format": fmt,
            "time_range": time_range or "all time",
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error exporting report")
        return {"error": f"Failed to export report: {type(exc).__name__}: {exc}"}
