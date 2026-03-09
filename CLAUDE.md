# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> Raley doesn't shop for you. Raley shops *with* you.

Named after a person who makes grocery day feel less like a chore. Not the chain.

## Commands

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest                           # 95 tests, <2s
pytest -v                        # verbose
pytest tests/test_reasoning.py   # single module
pytest -k "brand"                # by keyword
raley-bot login                  # browser auth
raley-mcp                        # MCP stdio server
```

All tests run in <2s. If a test takes >5s, something is hitting the network and that's a bug.

## Architecture

Grocery shopping assistant wrapping a real store API. Searches products, compares unit prices, tracks price history in SQLite, manages coupons, builds carts from freeform grocery lists. Exposed as both a CLI (`raley`) and an MCP server (`raley-mcp`) for Claude Desktop.

Full details: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** -- dependency graph, data flow, DB schema, scoring table.
Audit trail: **[docs/AUDIT.md](docs/AUDIT.md)** -- what was broken, removed, and fixed.

### Module Map

```
mcp_server.py   -- 12 MCP tools, the main interface
  api.py        -- curl subprocess HTTP, all store endpoints
  db.py         -- SQLite WAL, price history, deal detection
  reasoning.py  -- heuristic scoring (NOT ml), purchase frequency
  cart_builder.py -- grocery list parsing, value-sorted search
  unit_pricing.py -- $/oz $/lb $/ml $/unit normalization
  preferences.py  -- loads ~/.config/raley-assistant/preferences.json
  t1d.py        -- T1D nutrition scoring, GI database
  memory.py     -- persistent shopping memory (T1D config, notes)
  knowledge.py  -- T1D reference book search
  auth.py       -- Helium/Selenium browser login
  cookies.py    -- session persistence, validation

cli.py          -- Click + Rich, standalone from MCP
```

### Key Data Flow

**Search**: query -> `api.search_products()` (curl) -> `db.sync_products_from_search()` -> `unit_pricing.calculate_unit_prices()` -> `reasoning.evaluate_options()` -> JSON response

**Plan** (grocery list): freeform text -> `cart_builder.parse_grocery_list()` -> per-item search + scoring + `should_buy_this_trip()` check -> JSON with matches, totals, `recently_bought` flags -> user confirms -> `handle_add()` per item

### Why Curl

The store runs F5 BIG-IP TLS fingerprinting that blocks every Python HTTP library. `subprocess.run(["curl", ...])` with list args passes because curl's TLS handshake matches browsers. This is load-bearing. Do not replace it with requests/httpx/aiohttp.

### Scoring Engine (reasoning.py)

Heuristic, not ML. Products scored 0-100: base 50, best $/oz +25, value interpolation 0-20, on sale +15, brand match +10, preferred brand +12, name relevance 0-12, organic preference +8, price tiebreaker 0-3. Purchase frequency uses keyword matching (weekly/monthly/quarterly categories).

## The Seven Invariants

Break any of these and the system fails silently or leaks credentials:

1. **Curl-only HTTP** -- `subprocess.run(["curl", ...])` with list args. No `shell=True`. No Python HTTP libs.
2. **Single cookie path** -- `~/.config/raley-assistant/cookies.json`. Nowhere else.
3. **No secrets in output** -- cookie values never in logs, MCP responses, commits, or errors.
4. **Read tools don't write** -- search/cart/orders are read-only. DB price sync (append-only) is the sole exception.
5. **0o600 on secrets** -- `os.open(..., O_WRONLY|O_CREAT|O_TRUNC, 0o600)` for anything with tokens.
6. **URL encoding** -- `urllib.parse.urlencode()`. Never f-string query params.
7. **Cookie sanitization** -- strip `\n\r\x00` before curl `-H Cookie:` headers.

## Style

- `X | None` not `Optional[X]`
- `datetime.now(timezone.utc)` not `utcnow()`
- Dataclasses for structured returns, not dicts
- Tests are pure functions, no network, no disk (except `:memory:` SQLite)
- Rate limit bulk ops: 200ms between, 500ms every 10th
- Truncate names to 40 chars in MCP responses
- Cents internally, dollars in display strings
- `asyncio_mode = "auto"` in pytest config (no manual `@pytest.mark.asyncio` needed)
