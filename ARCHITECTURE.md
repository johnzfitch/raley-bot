# Architecture

## System Overview

Raley is a grocery shopping assistant that wraps a grocery store's web API with intelligent product selection, price tracking, and AI agent integration via MCP (Model Context Protocol).

```
┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│ Claude       │────▶│ MCP Server  │────▶│ Curl Client  │────▶ Store API
│ Desktop/Code │     │ (stdio)     │     │ (F5 evasion) │     (HTTPS)
└─────────────┘     └──────┬──────┘     └──────────────┘
                           │
                    ┌──────┴──────┐
                    │             │
              ┌─────▼────┐ ┌─────▼─────┐
              │ Reasoning │ │ SQLite DB │
              │ Engine    │ │ (prices)  │
              └───────────┘ └───────────┘
```

## Module Dependency Graph

```
mcp_server.py
├── api.py            (HTTP client, all store API calls)
├── db.py             (SQLite price history + order item tracking)
├── reasoning.py      (product scoring, purchase frequency)
├── cart_builder.py   (grocery list parsing, best product finder)
│   └── api.py
├── auth.py           (session status check)
├── unit_pricing.py   ($/oz, $/lb, $/unit normalization)
└── preferences.py    (user preference loading, wired into evaluate_options)

cli.py
├── api.py
├── cookies.py        (session persistence)
└── auth.py           (browser login)

cookies.py            (standalone, no internal deps)
preferences.py        (standalone, no internal deps)
```

## HTTP Client: Why Curl

The store API sits behind F5 BIG-IP Application Security Manager with TLS fingerprinting. Python's `requests`, `httpx`, and `aiohttp` all produce TLS Client Hello signatures that differ from real browsers, causing immediate 403 responses.

**Previous approach** (abandoned): The `rnet` Rust-based HTTP library with `Emulation.Firefox135` for TLS mimicry. This was never fully wired in — it was only used in `test_api.py` and `cookies.py`, never in the actual API client. The dependency was also never added to `pyproject.toml`.

**Current approach**: `subprocess.run(["curl", ...])` shells out to system curl, which naturally produces browser-like TLS fingerprints. Arguments are passed as a list (not a shell string), so shell injection is mitigated. URL parameters go through `urllib.parse.urlencode()`. Cookie values are sanitized for header injection characters.

Tradeoffs:
- Pro: Zero exotic Python dependencies, works on any system with curl
- Pro: curl's TLS stack matches browser fingerprints without configuration
- Con: Process spawn overhead per request (~5-15ms)
- Con: No connection pooling
- Con: Error handling is string-based (parsing curl's stdout)

## Data Flow

### Search → Display
```
User query
  → mcp_server.handle_search()
    → api.search_products()           # curl → JSON response
    → db.sync_products_from_search()  # append price observation
    → unit_pricing.calculate_unit_prices()  # normalize $/oz etc.
    → reasoning.evaluate_options()    # score and rank
    → JSON response to MCP client
```

### Plan (Grocery List) → Cart
```
Freeform text ("5 sweet potatoes, 2 ground chicken")
  → cart_builder.parse_grocery_list()    # regex parsing → [(item, qty)]
  → for each item:
    → cart_builder.find_best_product()   # search + sort by value
    → reasoning.evaluate_options()       # pick best (with preferred brands)
    → db.get_last_purchase_date()        # check order history
    → reasoning.should_buy_this_trip()   # flag recently bought items
    → reasoning.get_purchase_frequency() # flag infrequent items
  → JSON with matches, totals, recently_bought flags, confirmation prompts
  → User confirms
  → mcp_server.handle_add() for each   # separate cart add calls
```

### Price Tracking
```
Every search syncs results to SQLite:
  products table     ← upsert current price, name, brand
  price_history table ← append-only price observations

is_good_deal() compares current price against:
  - Historical average (15%+ below = good deal)
  - Historical minimum (within 5% = great deal)
  - Flags prices 10%+ above average
```

## Reasoning Engine

The reasoning engine (`reasoning.py`) is heuristic-based, not ML. It scores products on a 0-100 scale:

| Factor | Points | Condition |
|--------|--------|-----------|
| Base | 50 | All products start here |
| Best $/oz | +25 | Lowest unit price in set |
| Value interpolation | 0-20 | Linear scale between best and worst $/oz |
| On sale | +15 | `sale_price_cents` is set |
| Brand match | +10 | Brand name appears in search query |
| Preferred brand | +12 | Brand matches user's preferences.json entry |
| Name relevance | 0-12 | Proportion of query words in product name |
| Organic (if preferred) | +8 | Product name contains "organic" |
| Price tiebreaker | 0-3 | Lower absolute price |

Flags: `SALE`, `BEST_VALUE`, `PRICE_WARNING` (>2x cheapest), `ORGANIC`, `NOT_FOUND`

Purchase frequency classification uses keyword matching against category lists (weekly: milk/bread/eggs, monthly: spices/condiments, quarterly: extracts/supplements).

## Authentication

Two login methods:
1. **Browser login** (`raley login`): Opens Chrome via Helium/Selenium, waits for `FLDR.Auth` cookie, saves session
2. **Manual import** (`raley login --file cookies.json`): Import DevTools cookie export

Required cookies: `FLDR.Auth`, `FLDR.Session`, `FLDR.CSRF`, `FLDR.User`, `FLDR.RememberMe`

Cookies stored at `~/.config/raley-assistant/cookies.json` with `0600` permissions.

## File Permissions & Security

| File | Permissions | Rationale |
|------|------------|-----------|
| `cookies.json` | 0600 | Contains session tokens |
| Saved result JSONs | 0600 | May contain order/cart data |
| `raley.db` | Default umask | Price data is not sensitive |
| `preferences.json` | Default umask | No secrets |

Security hardening applied:
- URL parameter encoding (`urlencode`)
- Cookie value sanitization (strip `\n`, `\r`, `\x00`)
- No cookie values in MCP tool responses
- No silent write operations in read tools
- Subprocess timeout handling with error recovery

## Database Schema

```sql
products (
    sku TEXT PRIMARY KEY,
    name TEXT, brand TEXT, price_cents INT, sale_price_cents INT,
    size TEXT, unit_oz REAL, price_per_oz REAL,
    last_seen TEXT, first_seen TEXT
)

price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT, price_cents INT, sale_price_cents INT, observed_at TEXT
)

coupons (
    offer_id TEXT PRIMARY KEY,
    code TEXT, headline TEXT, description TEXT, category TEXT,
    discount_amount REAL, end_date TEXT, is_clipped INT,
    offer_type TEXT, synced_at TEXT
)

order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL, product_name TEXT, purchased_at TEXT NOT NULL,
    order_id TEXT,
    UNIQUE(sku, purchased_at)
)
```

## Known Gaps & TODOs

1. ~~**Preferences not wired into reasoning**~~ — **Resolved.** `preferred_brands` from `preferences.json` now wired into `evaluate_options()` (+12 scoring bonus). `prefer_organic` wired since v2c.

2. ~~**`should_buy_this_trip()` unused**~~ — **Resolved.** Now wired into `handle_build_list()` via `order_items` table and `get_last_purchase_date()`. Items recently purchased are flagged with `recently_bought`.

3. **No search command in CLI**: The CLI has `search`, `history`, `offers`, `orders`.

4. ~~**`flask` in dependencies**~~ — **Not present** in `pyproject.toml`. Already resolved.

5. ~~**CLI `clip_all` vs MCP `clip_all`**~~ — **Resolved.** CLI now delegates to `api.clip_all_offers()` with shared rate limiting and a progress callback.

6. ~~**No tests**~~ — **Resolved.** 95 tests across 7 test files covering reasoning, unit pricing, DB, cart builder, preferences, API (subprocess mocking), and MCP handlers.
