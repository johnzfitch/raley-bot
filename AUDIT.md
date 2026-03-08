# Raley Assistant — Code Audit & PR Notes

## Unused Functions & Constants

### `api.py` — Removed (were dead code from API exploration phase)

| Function/Constant | Status | Notes |
|---|---|---|
| `get_recommendations()` | **REMOVED** | Called nowhere. Used hardcoded widgetId `79opgv6j` |
| `autosuggest()` | **REMOVED** | Called nowhere |
| `validate_cart()` | **REMOVED** | Called nowhere |
| `get_collections()` | **REMOVED** | Called nowhere |
| `get_popular_searches()` | **REMOVED** | Called nowhere |
| `get_dietary_filters()` | **REMOVED** | Called nowhere |
| `get_sale_items()` | **REMOVED** | Called nowhere (search with `on_sale=True` does the same thing) |
| `browse_category()` | **REMOVED** | Called nowhere |
| `get_categories()` | **REMOVED** | Called nowhere |
| `get_time_slots()` | **REMOVED** | Called nowhere. Scheduling not wired into MCP or CLI |
| `set_time_slot()` | **REMOVED** | Called nowhere |
| `evaluate_product_offers()` | **REMOVED** | Called nowhere |
| `get_product_promotions()` | **REMOVED** | Called nowhere |
| `Promotion` dataclass | **REMOVED** | Only used by `get_product_promotions()` |
| `TimeSlot` dataclass | **REMOVED** | Only used by `get/set_time_slots()` |
| `Category` dataclass | **REMOVED** | Only used by `get_categories()` |
| `_parse_products()` (duplicate) | **MERGED** | Was duplicated logic from `search_products()`. Extracted shared `_parse_product_from_item()` |

### `cookies.py` — Cleaned

| Item | Status | Notes |
|---|---|---|
| `from rnet.cookie import Jar, Cookie` | **REMOVED** | `rnet` was never in pyproject.toml deps. Phantom import |
| `from rnet.header import HeaderMap` | **REMOVED** | Same phantom dependency |
| `cookies_to_jar()` | **REMOVED** | Built rnet.cookie.Jar objects that nothing used |
| `get_session_headers()` | **REMOVED** | Called nowhere |
| `get_csrf_token()` | **REMOVED** | Only called by `get_session_headers()` |
| `RALEYS_URL` constant | **REMOVED** | Only used by `cookies_to_jar()` |
| `import_and_save()` return type | **CHANGED** | Was `(Jar, list[str])`, now `(list[dict], list[str])` |

### `cart_builder.py` — Fixed

| Item | Status | Notes |
|---|---|---|
| `from .api import get_offers` | **REMOVED** | Imported but never used |
| `DEFAULT_COOKIES` path | **FIXED** | Was `~/raleys-cookie.json` (wrong), now `~/.config/raley-assistant/cookies.json` |

### `__init__.py` — Noted

| Item | Status | Notes |
|---|---|---|
| `quick_add`, `build_cart_from_list`, `get_client` | **KEPT** | Exported but not used by MCP server. These are the public API for external agent consumers. Intentional |

### `test_api.py` — Gitignored

| Item | Status | Notes |
|---|---|---|
| Entire file | **GITIGNORED** | Uses phantom `rnet` library, hardcoded cookie path `~/raleys-cookie.json`, debug code |

## Disjointed Systems

### 1. The `rnet` Ghost Architecture

`cookies.py` imported `rnet.cookie.Jar` and built cookie jar objects. `test_api.py` used `rnet.blocking.Client` with `Emulation.Firefox135`. But the actual production HTTP client (`api.py`) uses `subprocess.run(["curl", ...])`. The rnet library was never in `pyproject.toml` dependencies. This was clearly a prior architecture that was abandoned when curl proved more reliable for F5 evasion, but the old imports and helper functions were never cleaned up.

**Resolution**: Removed all rnet references. The curl approach is the real architecture.

### 2. Cookie Path Split Brain

Three different cookie paths existed across the codebase:

- `cart_builder.py`: `~/raleys-cookie.json` (legacy, wrong)
- `auth.py` + `mcp_server.py`: `~/.config/raley-assistant/cookies.json` (canonical)
- `test_api.py`: `~/raleys-cookie.json` (legacy, wrong)

This means `cart_builder.py`'s `quick_add()` and `build_cart_from_list()` would silently fail or use stale cookies if the user had only done `raley login` (which writes to the canonical path).

**Resolution**: Unified all paths to `~/.config/raley-assistant/cookies.json`.

### 3. Silent Coupon Clipping in Search (Security Issue)

`mcp_server.py`'s `handle_search()` had a block that silently searched for unclipped coupons matching the search query and clipped up to 3 of them. This is a WRITE operation hidden inside what appears to be a READ operation. The user is never informed.

**Resolution**: Removed the silent clipping block. Coupon clipping is now only available through the explicit `offers` tool with `action: clip_all`.

### 4. Missing Modules: `db.py` and `reasoning.py`

Both `db.py` and `reasoning.py` are imported by `mcp_server.py` but were not provided in the repository snapshot. These modules provide:

- `db.py`: SQLite price history tracking (`get_connection`, `sync_products_from_search`, `sync_coupons_from_api`, `get_product_with_history`, `is_good_deal`, `search_products_local`, `get_price_stats`)
- `reasoning.py`: Purchase frequency analysis (`evaluate_options`, `get_purchase_frequency`, `should_buy_this_trip`, `PurchaseFrequency`)

**Status**: These appear to be real, functional modules that just weren't included in the document set. The MCP server will fail at runtime without them.

**Note**: `should_buy_this_trip` is imported in the original `mcp_server.py` but never called — it's dead code within the import.

## Security Hardening Applied

| Issue | Severity | Fix |
|---|---|---|
| URL params not encoded | CRITICAL | Now uses `urllib.parse.urlencode()` |
| Cookie values not sanitized | CRITICAL | Strip `\n`, `\r`, `\x00` before passing to curl |
| Saved files use default umask | MEDIUM | Now `os.open(..., 0o600)` for saved results |
| Cookie files use default umask | MEDIUM | Now `Path.touch(mode=0o600)` |
| Silent write in read operation | HIGH | Removed coupon clipping from search |
| No curl timeout handling | MEDIUM | Added try/except around subprocess.run |
| Cookie value length unbounded | LOW | Added MAX_COOKIE_VALUE_LEN check |
