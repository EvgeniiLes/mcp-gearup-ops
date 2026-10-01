# GearUp Ops MCP Server

A **Model Context Protocol (MCP) server** in Python that lets Claude Desktop, Claude Code, Cursor or any MCP client work with an online store's operations data: sales, stock, orders, B2B leads, FAQ and promo codes.

Built on a fictional store ("GearUp") as a showcase. It is the "own MCP server" pattern many AI-engineer roles ask for: read-only analytics tools, one **guarded write tool**, a schema resource and a reusable prompt, all covered by end-to-end tests that drive the server through a real MCP client.

## What it exposes

| Kind | Name | Purpose |
|------|------|---------|
| Tool | `list_low_stock` | Products at/below reorder level, most urgent first |
| Tool | `sales_summary(days)` | Revenue, orders and average order value for the last N days |
| Tool | `top_products(days, limit)` | Best sellers by revenue |
| Tool | `get_order(order_id)` | Order with line items; customer email is masked |
| Tool | `list_leads(tier)` | B2B leads with AI score and next step |
| Tool | `search_faq(query)` | Keyword search over the customer FAQ |
| Tool | `create_promo_code(...)` | **Write.** Validates code, percent, expiry, duplicates; discounts above 20% need `owner_approved=true` |
| Resource | `gearup://schema` | Tables and columns, so the model can reason about the data |
| Prompt | `daily_ops_briefing` | Morning briefing recipe that chains the tools above |

### Design decisions

- **Guardrails live in the server, not in the prompt.** A model can be talked into anything; the server refuses a 30% discount unless the owner approval flag is set, rejects duplicate or malformed codes and past expiry dates, and returns a clear error the model can act on.
- **Least privilege by default.** Only one tool writes; everything else is read-only SQL with bound parameters (no string-built queries from user input except a validated LIKE clause built from placeholders).
- **PII minimised.** `get_order` masks the customer email (`j***@example.com`).
- **Evergreen demo data.** The source workbook uses `TODAY()`-relative date formulas; `seed.py` evaluates them so "yesterday" always has orders.

## Quick start

```bash
pip install -r requirements.txt
python -m gearup_mcp.seed gearup.db          # build the SQLite demo DB from data/GearUp_Database.xlsx
python -m gearup_mcp                         # run the server over stdio (also auto-builds the DB on first call)
```

### Claude Desktop

Add to `claude_desktop_config.json` (see `claude_desktop_config.example.json`):

```json
{
  "mcpServers": {
    "gearup-ops": {
      "command": "python",
      "args": ["-m", "gearup_mcp"],
      "cwd": "/absolute/path/to/mcp-gearup-ops",
      "env": { "GEARUP_DB": "/absolute/path/to/mcp-gearup-ops/gearup.db" }
    }
  }
}
```

### Claude Code

```bash
claude mcp add gearup-ops --env GEARUP_DB=$PWD/gearup.db -- python -m gearup_mcp
```

Then ask: *"Use the daily_ops_briefing prompt"*, *"What is running low on stock?"*, or *"Create promo AUTUMN15, 15% until 2026-12-31"*.

## Tests

```bash
pip install pytest
pytest -q
```

9 end-to-end tests start the server as a subprocess and call it through the official MCP client: tool/resource/prompt discovery, low-stock logic, sales aggregation consistency, input validation, PII masking, FAQ search, and every promo-code guard (duplicate, bad format, bad percent, past date, owner approval).

## Project layout

```
gearup_mcp/server.py   tools, resource and prompt (FastMCP)
gearup_mcp/seed.py     builds SQLite from the Excel workbook, evaluating TODAY() formulas
data/                  GearUp_Database.xlsx (fictional demo data)
tests/                 end-to-end tests through a real MCP client
```

## Extending it

Swap SQLite for Postgres, or point the tools at Shopify / WooCommerce / Wildberries APIs; add a `reorder_suggestion` tool that combines stock with sales velocity (see the n8n stock-out forecast workflow in the sibling portfolio repo); expose it over streamable HTTP for remote agents behind an auth proxy.
