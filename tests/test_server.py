"""End-to-end tests: a real MCP client talks to the server over stdio."""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


def _params(db: Path) -> StdioServerParameters:
    env = {**os.environ, "GEARUP_DB": str(db)}
    return StdioServerParameters(command=sys.executable, args=["-m", "gearup_mcp"], cwd=str(ROOT), env=env)


async def _call(db: Path, name: str, args: dict | None = None):
    async with stdio_client(_params(db)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(name, args or {})


def call(db, name, args=None):
    return anyio.run(_call, db, name, args)


def payload(result):
    """Structured content if present, otherwise parse the text block."""
    if result.structuredContent is not None:
        data = result.structuredContent
        return data.get("result", data) if isinstance(data, dict) and set(data) == {"result"} else data
    return json.loads(result.content[0].text)


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "test.db"


def test_server_lists_expected_tools_resources_and_prompt(db):
    async def run():
        async with stdio_client(_params(db)) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                tools = {t.name for t in (await s.list_tools()).tools}
                resources = [str(r.uri) for r in (await s.list_resources()).resources]
                prompts = {p.name for p in (await s.list_prompts()).prompts}
                schema = await s.read_resource("gearup://schema")
                return tools, resources, prompts, schema.contents[0].text

    tools, resources, prompts, schema = anyio.run(run)
    assert tools == {"list_low_stock", "sales_summary", "top_products", "get_order", "list_leads", "search_faq", "create_promo_code"}
    assert "gearup://schema" in resources
    assert "daily_ops_briefing" in prompts
    assert "Products(sku, name" in schema


def test_low_stock_only_returns_items_at_or_below_reorder_level(db):
    items = payload(call(db, "list_low_stock"))
    assert items, "demo data contains low-stock items"
    assert all(i["stock"] <= i["reorder_level"] for i in items)
    assert items == sorted(items, key=lambda i: (-i["units_below_level"], i["sku"]))


def test_sales_summary_is_consistent(db):
    week = payload(call(db, "sales_summary", {"days": 7}))
    month = payload(call(db, "sales_summary", {"days": 30}))
    assert week["orders"] > 0
    assert month["orders"] >= week["orders"]
    assert week["average_order_value"] == round(week["revenue"] / week["orders"], 2)


def test_invalid_period_is_rejected(db):
    result = call(db, "sales_summary", {"days": 0})
    assert result.isError


def test_get_order_masks_email_and_returns_lines(db):
    order = payload(call(db, "get_order", {"order_id": "gu-1100"}))
    assert order["found"] is True
    assert order["order_id"] == "GU-1100"
    assert "***@" in order["customer_email"]
    assert order["lines"]
    assert payload(call(db, "get_order", {"order_id": "NOPE-1"}))["found"] is False


def test_top_products_sorted_by_revenue(db):
    rows = payload(call(db, "top_products", {"days": 30, "limit": 3}))
    assert len(rows) == 3
    assert [r["revenue"] for r in rows] == sorted((r["revenue"] for r in rows), reverse=True)


def test_leads_filter(db):
    hot = payload(call(db, "list_leads", {"tier": "hot"}))
    assert hot and all(l["tier"] == "hot" for l in hot)


def test_faq_search(db):
    rows = payload(call(db, "search_faq", {"query": "how long does shipping take"}))
    assert rows and any("shipping" in r["topic"] for r in rows)


def test_promo_creation_and_guards(db):
    future = (date.today() + timedelta(days=30)).isoformat()
    ok = payload(call(db, "create_promo_code", {"code": "autumn15", "discount_pct": 15, "valid_until": future}))
    assert ok == {"created": True, "code": "AUTUMN15", "discount_pct": 15, "valid_until": future}
    # duplicate
    assert call(db, "create_promo_code", {"code": "AUTUMN15", "discount_pct": 10, "valid_until": future}).isError
    # bad code, bad percent, past date
    assert call(db, "create_promo_code", {"code": "bad code!", "discount_pct": 10, "valid_until": future}).isError
    assert call(db, "create_promo_code", {"code": "BIG95", "discount_pct": 95, "valid_until": future, "owner_approved": True}).isError
    assert call(db, "create_promo_code", {"code": "OLD10", "discount_pct": 10, "valid_until": "2020-01-01"}).isError
    # >20% needs owner approval
    blocked = call(db, "create_promo_code", {"code": "HALF30", "discount_pct": 30, "valid_until": future})
    assert blocked.isError and "owner approval" in blocked.content[0].text
    approved = payload(call(db, "create_promo_code", {"code": "HALF30", "discount_pct": 30, "valid_until": future, "owner_approved": True}))
    assert approved["created"] is True
