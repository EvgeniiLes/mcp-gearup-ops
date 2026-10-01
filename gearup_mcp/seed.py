"""Build the SQLite demo database from GearUp_Database.xlsx.

The workbook stores dates as spreadsheet formulas relative to TODAY(), e.g.
``=TEXT(TODAY()-29+TIME(8,47,0),"yyyy-mm-dd hh:mm")``. This module evaluates
those formulas so the demo data always looks "fresh" (yesterday has orders).
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl

DEFAULT_XLSX = Path(__file__).resolve().parent.parent / "data" / "GearUp_Database.xlsx"

_FORMULA = re.compile(
    r"=TEXT\(TODAY\(\)(?P<days>[+-]\d+)?\+TIME\((?P<h>\d+),(?P<m>\d+),(?P<s>\d+)\),"
)

TABLES = {
    "Products": ["sku", "name", "category", "price", "stock", "reorder_level", "updated_at"],
    "Orders": ["order_id", "created_at", "customer_name", "customer_email", "customer_tg", "total", "status", "tracking"],
    "OrderLines": ["order_id", "created_at", "sku", "name", "qty", "price"],
    "Leads": ["lead_id", "created_at", "company", "contact_name", "email", "phone", "website", "size",
              "budget_usd", "message", "score", "tier", "reason", "next_step", "status"],
    "FAQ": ["topic", "question", "answer"],
    "Promo": ["code", "discount_pct", "valid_until", "note", "created_at", "created_by"],
}


def eval_cell(value, today: date):
    """Evaluate the TODAY()-relative date formulas used in the workbook."""
    if isinstance(value, str) and value.startswith("="):
        match = _FORMULA.match(value)
        if match:
            days = int(match.group("days") or 0)
            moment = datetime.combine(today, datetime.min.time()) + timedelta(
                days=days, hours=int(match.group("h")), minutes=int(match.group("m")), seconds=int(match.group("s"))
            )
            return moment.strftime("%Y-%m-%d %H:%M")
    return value


def build_database(db_path: str | Path, xlsx_path: str | Path = DEFAULT_XLSX, today: date | None = None) -> Path:
    """Create (or overwrite) the SQLite database at ``db_path``."""
    today = today or date.today()
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    workbook = openpyxl.load_workbook(xlsx_path, data_only=False)
    con = sqlite3.connect(db_path)
    for table, columns in TABLES.items():
        con.execute(f'CREATE TABLE "{table}" ({", ".join(f"{c} TEXT" for c in columns)})')
        sheet = workbook[table]
        rows = []
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if all(cell is None for cell in row[: len(columns)]):
                continue
            rows.append([eval_cell(cell, today) for cell in row[: len(columns)]])
        con.executemany(
            f'INSERT INTO "{table}" VALUES ({", ".join("?" * len(columns))})', rows
        )
    con.commit()
    con.close()
    return db_path


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "gearup.db"
    print(f"Built {build_database(target)}")
