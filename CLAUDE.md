# CLAUDE.md

> Raley doesn't shop for you. Raley shops *with* you.

Named after a person who makes grocery day feel less like a chore. Not the chain.

---

## What This Is

A grocery shopping assistant that wraps a real store API. Searches products, compares unit prices, tracks price history in SQLite, manages coupons, builds carts from freeform grocery lists, and exposes it all as an MCP server so Claude Desktop can drive the whole thing conversationally.

The HTTP client shells out to **curl via subprocess** — not requests, not httpx, not aiohttp. The store runs F5 BIG-IP TLS fingerprinting that blocks every Python HTTP library. Curl's TLS handshake passes. This is load-bearing weirdness. Do not "improve" it.

## Setup

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest                           # 95 tests, <2s
raley login                      # browser auth
raley-mcp                        # MCP stdio server
```

## Architecture (Read This)

Full details: **[ARCHITECTURE.md](ARCHITECTURE.md)** — dependency graph, data flow diagrams, DB schema, scoring table.

Audit trail: **[AUDIT.md](AUDIT.md)** — what was broken, what was removed, what was fixed.

### Module Map

```
mcp_server.py (690L)  ← 9 MCP tools, the main interface
├── api.py (612L)     ← curl subprocess HTTP, all store endpoints
├── db.py (366L)      ← SQLite WAL, price history, deal detection
├── reasoning.py (280L)  ← heuristic scoring (NOT ml), purchase frequency
├── cart_builder.py (193L)  ← grocery list parsing, value-sorted search
├── unit_pricing.py (260L)  ← $/oz $/lb $/ml $/unit normalization
├── preferences.py (151L)   ← loads ~/.config/raley-assistant/preferences.json
├── auth.py (161L)    ← Helium/Selenium browser login
└── cookies.py (128L) ← session persistence, validation

cli.py (500L)         ← Click + Rich, standalone from MCP
```

### The Seven Invariants

Break any of these and the system fails silently or leaks credentials:

1. **Curl-only HTTP** — `subprocess.run(["curl", ...])` with list args. No shell=True. No Python HTTP libs.
2. **Single cookie path** — `~/.config/raley-assistant/cookies.json`. Nowhere else. Ever.
3. **No secrets in output** — cookie values never in logs, MCP responses, commits, or error messages.
4. **Read tools don't write** — search/cart/orders are read-only. DB price sync (append-only) is the sole exception.
5. **0o600 on secrets** — `os.open(..., O_WRONLY|O_CREAT|O_TRUNC, 0o600)` for anything with tokens.
6. **URL encoding** — `urllib.parse.urlencode()`. Never f-string query params.
7. **Cookie sanitization** — strip `\n\r\x00` before curl `-H Cookie:` headers.

### What's Wired, What's Not

| Connection | Status |
|-----------|--------|
| preferences → `prefer_organic` in MCP | **Wired** (V2c) |
| preferences → `preferred_brand()` in scoring | **Wired** — +12 scoring bonus in `evaluate_options()` |
| `should_buy_this_trip()` in reasoning.py | **Wired** — via `order_items` table in `handle_build_list()` |
| CLI `clip_all` vs MCP `clip_all_offers` | **Consolidated** — CLI delegates to `api.clip_all_offers()` |
| db price sync in `handle_search` | **Wired** |
| db order sync in `handle_orders` | **Wired** — syncs order items for purchase frequency |
| unit_pricing in MCP search response | **Wired** |

---

## Phase Plan

Each phase is a coherent unit of work. **Do not batch phases.** Complete one, verify it, then move to the next. Each phase has a verification step that requires reading actual code output — not just "it compiled."

### Phase 0: Orient
Read `ARCHITECTURE.md` and `AUDIT.md` in full. Then read `mcp_server.py` top to bottom. Do not write code yet. State three things you found surprising or that contradict your assumptions.

### Phase 1: Wire the Remaining Preferences
`preferences.py` exposes `preferred_brand(category)` but `evaluate_options()` in `reasoning.py` never uses it. The scoring engine already has brand matching against the *query string* — but user-configured brand preferences from `preferences.json` should boost scoring too.

**Tasks:**
1. Add `preferred_brands: dict[str, str] | None` parameter to `evaluate_options()`
2. If a product's brand matches the user's preferred brand for that category, add a scoring bonus (separate from query-string brand match)
3. Wire `_prefs.product_prefs` through from `mcp_server.py` into both `handle_search` and `handle_build_list`
4. Add tests that verify: (a) preferred brand wins over slightly-better-value competitor, (b) preferred brand does NOT win when value gap is >30%

**Verify:** Run `pytest -v` — all existing tests still pass, new tests pass, no test touches the network.

### Phase 2: Consolidate `clip_all`
CLI's `clip_all` and MCP's `clip_all_offers` are separate implementations with different rate limiting (CLI: 200ms/500ms, MCP: calls `api.clip_all_offers` which has its own timing). One should call the other.

**Tasks:**
1. Identify which implementation is more robust (check error handling, rate limiting, progress reporting)
2. Make the weaker one call the stronger one
3. Ensure CLI still shows Rich progress bar, MCP still returns JSON summary
4. Add a test that verifies the shared function's rate limiting logic (mock `time.sleep` to assert call pattern)

**Verify:** `grep -rn "clip_all\|clip_offer" raley_assistant/` should show exactly ONE implementation of the bulk-clip loop, called from two places.

### Phase 3: Order History → `should_buy_this_trip()`
`should_buy_this_trip()` exists in `reasoning.py` but is never called because there's no order history integration. `get_orders()` in `api.py` returns past orders. Close the loop.

**Tasks:**
1. Add `db.sync_orders_from_api()` — extract (sku, date) pairs from order history and store them
2. Add `db.get_last_purchase_date(sku)` → `str | None`
3. Wire `should_buy_this_trip()` into `handle_build_list()` — if an item was recently purchased (per its frequency class), add a `"recently_bought": "3d ago"` flag to the plan response instead of blocking it
4. Add tests using `:memory:` SQLite

**Verify:** The plan tool's response JSON for "milk" should include a `recently_bought` field when milk was ordered 2 days ago, and should NOT include it when milk was ordered 10 days ago.

### Phase 4: Subprocess Mocking for api.py Tests
The test suite has zero coverage on `api.py` because it calls `subprocess.run`. Fix that.

**Tasks:**
1. Create `tests/test_api.py`
2. Mock `subprocess.run` to return realistic curl output (HTTP status on last line, JSON body above)
3. Test: successful search parse, 403 response handling, timeout handling, malformed JSON
4. Test: cookie sanitization actually strips `\r\n\x00` (construct a CurlClient with dirty cookies, assert the curl command args are clean)
5. Test: URL encoding (search for `"chicken & waffles"` → verify `%26` in the curl command)

**Verify:** `pytest tests/test_api.py -v` passes. `grep -c "subprocess" tests/test_api.py` shows mock usage, not real calls.

### Phase 5: MCP Handler Tests
Test the async handlers in `mcp_server.py` without network access.

**Tasks:**
1. Create `tests/test_mcp_handlers.py`
2. Mock `get_api_client()` to return a fake client
3. Mock `search_products()` to return canned Product objects
4. Test `handle_search` returns valid JSON with expected fields
5. Test `handle_plan` with a multi-item grocery list
6. Test error handling: what happens when cookies are missing?

**Verify:** `pytest tests/test_mcp_handlers.py -v` passes with no warnings about unclosed connections or event loops.

### Phase 6: Documentation Sync
After phases 1-5, the codebase has changed. Documentation must match.

**Tasks:**
1. Update `ARCHITECTURE.md` known gaps (remove completed items, add any new ones)
2. Update this file's "What's Wired" table
3. Update `README.md` if any new CLI commands or MCP tools were added
4. Run `pytest` one final time
5. `git diff --stat` to confirm every changed file is intentional

**Verify:** Read this file as if you've never seen this project. Does it tell you everything you need to start working? If not, fix it.

---

## Style Notes

- `X | None` not `Optional[X]` in new code
- `datetime.now(timezone.utc)` not `utcnow()`
- Dataclasses for structured returns, not dicts
- Tests are pure functions, no network, no disk (except `:memory:` SQLite)
- Rate limit bulk ops: 200ms between, 500ms every 10th
- Truncate names to 40 chars in MCP responses
- Cents internally, dollars in display strings

## Test Execution

```bash
pytest                        # all 95 tests
pytest -v                     # verbose
pytest tests/test_reasoning.py  # single module
pytest tests/test_api.py      # api subprocess mocking
pytest tests/test_mcp_server.py  # MCP handler tests
pytest -k "brand"             # by keyword
```

All tests run in <2s. If a test takes >5s, something is hitting the network and that's a bug.
