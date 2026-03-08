# PR: Raley v0.3.0 — Security Hardening, Dead Code Exorcism, README Rewrite

## Summary

This PR takes the raley-assistant codebase from "it works but there's a ghost architecture haunting cookies.py" to "it works and we actually know why." Named the project after Raley — a person, not a grocery chain — because someone deserves credit for making grocery day bearable.

## Changes

### Security Hardening
- **URL parameter encoding** — `api.py` was building query strings by naive string concatenation. Now uses `urllib.parse.urlencode()`. The kind of fix that makes you wonder how it worked before (answer: the params were always simple strings, but one malformed search query would've been exciting).
- **Cookie value sanitization** — Stripping `\n`, `\r`, `\x00` from cookie values before they hit the curl `-H Cookie:` header. Header injection via cookie values is a real attack vector.
- **File permissions** — All saved files (cookies, results JSONs) now write with `0o600` instead of inheriting umask. Your grocery session tokens shouldn't be world-readable.
- **Silent write in read operation** — `handle_search()` was secretly clipping coupons during product searches. Removed. Writes are now explicit.

### Dead Code Removal
- **14 unused functions** removed from `api.py` (recommendations, autosuggest, cart validation, collections, popular searches, dietary filters, scheduling, category browsing, promotions evaluation)
- **Phantom `rnet` dependency** fully exorcised from `cookies.py` — was imported but never in pyproject.toml
- **Unused import** (`get_offers`) removed from `cart_builder.py`
- **Cookie path inconsistency** fixed — `cart_builder.py` pointed to `~/raleys-cookie.json` while everything else used `~/.config/raley-assistant/cookies.json`
- **Duplicate product parsing** consolidated into `_parse_product_from_item()`

### README
Complete rewrite. Explains the curl-based TLS evasion approach, documents the actual architecture, covers security posture, and acknowledges this project is named after a person.

### Files
- `test_api.py` → added to `.gitignore` (uses phantom deps and hardcoded paths)
- `AUDIT.md` → full breakdown of every unused function, disjointed system, and security fix

## Missing Files Note

`db.py` and `reasoning.py` are imported by the MCP server but weren't in the provided source. They need to exist for the MCP server to function. Also `should_buy_this_trip` is imported from `reasoning.py` but never called.

---

hey @codex — need your eyes on this. Here's what I want you to tear apart:

**Security review**: The curl subprocess approach in `api.py` is the load-bearing wall of this project. Verify the URL encoding fix in `_run_curl()` actually covers all injection vectors. Check if the cookie sanitization in `_sanitize_cookie_value()` is sufficient or if we need to handle `%0d%0a` encoded variants. Look at `save_result_to_file()` — is `os.open()` with `O_CREAT | O_TRUNC` and mode `0o600` actually atomic on Linux or can there be a TOCTOU race? Also verify the `subprocess.TimeoutExpired` handler can't leave zombie curl processes.

**Unused function review**: I removed 14 functions from `api.py`. Confirm none of them are referenced by `db.py` or `reasoning.py` (which I don't have source for). Specifically check whether `get_product_promotions()` or `evaluate_product_offers()` feed into the reasoning engine. Also verify `should_buy_this_trip` is truly dead — it's imported in the original `mcp_server.py` but I can't find a call site.

**Unused constants review**: `CT_ALIASES` was defined in `unit_pricing.py` but never referenced — I removed it. Check if `OZ_PER_GAL = 128` is actually used anywhere or if it's leftover from a liquid pricing path that was never wired in. Same for `ML_PER_GAL`.

**Unused blocks of code**: The `_parse_products()` function in the original `api.py` was nearly identical to the product parsing loop inside `search_products()`. I consolidated them into `_parse_product_from_item()`. Verify the consolidation didn't lose any edge-case handling that was in one version but not the other.

**Code missing alignment elsewhere**: The `cli.py` `clip_all` command uses per-offer `clip_offer()` in a loop, but `mcp_server.py` calls the batch `clip_all_offers()` from `api.py`. These are two different implementations of the same operation with different rate limiting behavior. Should the CLI use the batch version too?

**Code conflicting with goals**: The `preferences.json` system (`prefer_local`, `organic_preference`, `budget_target`) is documented in the README but never loaded or referenced by any code path. Is this aspirational or is there a missing loader? The `evaluate_options()` function in `reasoning.py` takes `prefer_organic` but it's always called with `prefer_organic=False`. Someone you can always depend on to help you go shopping. The great thing about healthy eating and drinking habits starting with our grocery stores. The grocery store is always a hassle~~free~~ experience with your pal raley.
