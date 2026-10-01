"""GearUp Ops MCP server.

Exposes a (fictional) online store's operations data to any MCP client
(Claude Desktop, Claude Code, Cursor, ...): read-only analytics tools, one
guarded write tool, a schema resource and a briefing prompt.

Run:  python -m gearup_mcp.server          (stdio transport)
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

from .seed import build_database

DB_PATH = Path(os.environ.get("GEARUP_DB", Path.cwd() / "gearup.db"))
MAX_DISCOUNT_WITHOUT_APPROVAL = 20  # matches the staff FAQ: >20% needs owner approval
CODE_RE = re.compile(r"^[A-Z0-9]{3,20}$")

mcp = FastMCP("gearup-ops")


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        build_database(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _rows(query: str, params: tuple = ()) -> list[dict]:
    with closing(_connect()) as con:
        return [dict(r) for r in con.execute(query, params).fetchall()]


@mcp.tool()
def list_low_stock() -> list[dict]:
    """Products at or below their reorder level, most urgent first.

    Returns sku, name, stock, reorder_level and how many units below the level.
    """
    return _rows(
        """SELECT sku, name, CAST(stock AS INTEGER) AS stock,
                  CAST(reorder_level AS INTEGER) AS reorder_level,
                  CAST(reorder_level AS INTEGER) - CAST(stock AS INTEGER) AS units_below_level
           FROM Products
           WHERE CAST(stock AS INTEGER) <= CAST(reorder_level AS INTEGER)
           ORDER BY units_below_level DESC, sku"""
    )


@mcp.tool()
def sales_summary(days: int = 7) -> dict:
    """Revenue, order count and average order value for the last N days (1-365).

    Excludes cancelled orders. Dates are compared on the order timestamp.
    """
    if not 1 <= days <= 365:
        raise ValueError("days must be between 1 and 365")
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    row = _rows(
        """SELECT COUNT(*) AS orders, COALESCE(SUM(CAST(total AS REAL)), 0) AS revenue
           FROM Orders WHERE created_at >= ? AND status != 'cancelled'""",
        (since,),
    )[0]
    orders, revenue = row["orders"], round(row["revenue"], 2)
    return {
        "period_days": days,
        "since": since,
        "orders": orders,
        "revenue": revenue,
        "average_order_value": round(revenue / orders, 2) if orders else 0.0,
    }


@mcp.tool()
def top_products(days: int = 30, limit: int = 5) -> list[dict]:
    """Best-selling products by revenue over the last N days."""
    if not 1 <= days <= 365 or not 1 <= limit <= 50:
        raise ValueError("days must be 1-365 and limit 1-50")
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    return _rows(
        """SELECT sku, name, SUM(CAST(qty AS INTEGER)) AS units,
                  ROUND(SUM(CAST(qty AS INTEGER) * CAST(price AS REAL)), 2) AS revenue
           FROM OrderLines WHERE created_at >= ?
           GROUP BY sku, name ORDER BY revenue DESC LIMIT ?""",
        (since, limit),
    )


@mcp.tool()
def get_order(order_id: str) -> dict:
    """Look up one order (e.g. 'GU-1100') with its line items.

    Customer email is masked; use it only to verify identity in the support flow.
    """
    orders = _rows(
        "SELECT order_id, created_at, customer_name, customer_email, total, status, tracking FROM Orders WHERE order_id = ?",
        (order_id.strip().upper(),),
    )
    if not orders:
        return {"found": False, "order_id": order_id}
    order = orders[0]
    user, _, domain = order["customer_email"].partition("@")
    order["customer_email"] = f"{user[:1]}***@{domain}"
    lines = _rows(
        "SELECT sku, name, CAST(qty AS INTEGER) AS qty, CAST(price AS REAL) AS price FROM OrderLines WHERE order_id = ?",
        (order["order_id"],),
    )
    return {"found": True, **order, "lines": lines}


@mcp.tool()
def list_leads(tier: Literal["hot", "warm", "cold", "all"] = "all") -> list[dict]:
    """B2B wholesale leads with AI score, tier and recommended next step."""
    query = "SELECT lead_id, company, contact_name, CAST(score AS INTEGER) AS score, tier, budget_usd, next_step, status FROM Leads"
    params: tuple = ()
    if tier != "all":
        query += " WHERE tier = ?"
        params = (tier,)
    return _rows(query + " ORDER BY score DESC", params)


@mcp.tool()
def search_faq(query: str) -> list[dict]:
    """Keyword search over the customer FAQ (topic, question, answer)."""
    terms = [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]
    if not terms:
        return []
    clause = " OR ".join("lower(question || ' ' || answer || ' ' || topic) LIKE ?" for _ in terms)
    rows = _rows(f"SELECT topic, question, answer FROM FAQ WHERE {clause}", tuple(f"%{t}%" for t in terms))
    return rows[:5]


@mcp.tool()
def create_promo_code(
    code: str,
    discount_pct: int,
    valid_until: str,
    note: str = "",
    owner_approved: bool = False,
) -> dict:
    """Create a promo code. WRITE operation: only call when explicitly asked.

    Rules: code is 3-20 uppercase letters/digits and must be new; discount 1-90%;
    expiry in the future (YYYY-MM-DD). Discounts above 20% require owner_approved=true,
    which the assistant must only set after the owner has confirmed in the conversation.
    """
    code = code.strip().upper()
    if not CODE_RE.match(code):
        raise ValueError("code must be 3-20 characters: uppercase letters and digits only")
    if not 1 <= discount_pct <= 90:
        raise ValueError("discount_pct must be between 1 and 90")
    try:
        expiry = date.fromisoformat(valid_until)
    except ValueError as exc:
        raise ValueError("valid_until must be a date in YYYY-MM-DD format") from exc
    if expiry <= date.today():
        raise ValueError("valid_until must be in the future")
    if discount_pct > MAX_DISCOUNT_WITHOUT_APPROVAL and not owner_approved:
        raise ValueError(
            f"discounts above {MAX_DISCOUNT_WITHOUT_APPROVAL}% need owner approval: ask the owner, then retry with owner_approved=true"
        )
    with closing(_connect()) as con:
        if con.execute("SELECT 1 FROM Promo WHERE code = ?", (code,)).fetchone():
            raise ValueError(f"promo code {code} already exists")
        con.execute(
            "INSERT INTO Promo VALUES (?, ?, ?, ?, ?, ?)",
            (code, str(discount_pct), valid_until, note, datetime.now().strftime("%Y-%m-%d %H:%M"), "mcp-agent"),
        )
        con.commit()
    return {"created": True, "code": code, "discount_pct": discount_pct, "valid_until": valid_until}


@mcp.resource("gearup://schema")
def schema() -> str:
    """Tables and columns of the GearUp operations database."""
    with closing(_connect()) as con:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        parts = []
        for table in tables:
            cols = ", ".join(r[1] for r in con.execute(f'PRAGMA table_info("{table}")'))
            parts.append(f"{table}({cols})")
    return "\n".join(parts)


@mcp.prompt()
def daily_ops_briefing() -> str:
    """Ask the assistant for the owner's morning briefing using the tools above."""
    return (
        "You are the operations analyst for GearUp. Prepare the owner's morning briefing: "
        "1) call sales_summary for 1 day and for 7 days and compare the daily average, "
        "2) call top_products for the last 7 days, 3) call list_low_stock, 4) call list_leads for tier 'hot'. "
        "Reply with the key number first, then at most 4 bullets and 2 concrete actions for today. "
        "Never invent numbers: use only tool results."
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
