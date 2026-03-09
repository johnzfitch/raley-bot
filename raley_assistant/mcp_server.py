"""MCP Server for Raley Grocery Assistant — Consolidated Tools.

Named after Raley — someone who makes grocery day feel less like a chore.
"""

import json
import os
import re
from pathlib import Path
from typing import Any
from datetime import datetime

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    Prompt,
    PromptArgument,
    GetPromptResult,
    PromptMessage,
)

from .t1d import score_t1d, annotate_product, find_coupon_matches
from .memory import (
    load_memory,
    save_memory,
    add_note,
    set_field,
    get_summary,
    current_season,
)
from .knowledge import search_knowledge, list_books

# ---------------------------------------------------------------------------
# Channel 1: MCP Server Instructions
# Injected verbatim into Claude's system prompt as "# MCP Server Instructions"
# Re-evaluated each turn — keep it stateless and factual.
# ---------------------------------------------------------------------------

MCP_INSTRUCTIONS = """
You are connected to Raley's Grocery Shopping Assistant — a live interface to
the Raley's / Bel Air / Nob Hill grocery store API with price history tracking,
coupon management, and Type 1 diabetes nutrition guidance.

## Tools

| Tool         | What it does |
|--------------|-------------|
| search       | Find products. Returns best match with SKU, price, sale status, unit price |
| search_batch | Search multiple products at once (up to 15). Returns best match per query |
| add          | Add single item to cart. Returns cart state after mutation |
| add_plan     | Bulk add items: pass 'sku:cents,sku:cents:qty,...' from plan results |
| remove       | Remove item from cart by SKU. Returns reason on failure + cart state |
| cart         | View current cart. Includes both `items_count` (unique) and `total_units` (sum of qty) |
| cart_diff    | Compare expected SKUs against actual cart. Shows missing, extra, qty mismatches |
| offers       | Coupon management: `list` unclipped, `clip_all`, or `sync` to local DB |
| plan         | Parse a freeform grocery list, find matches + totals. Does not add to cart |
| price        | Price history for a SKU, search local DB, or `clear=true` to reset cache |
| orders       | Past order history with totals |
| favorites    | Purchase history: `products` (recent), `brands` (by product count), `sync` (refresh) |
| deals        | Best-value items this week: clipped coupons + sale prices + price history |
| memory       | Read/write shopping memory. Use `section=t1d/shopping/notes` to filter |
| knowledge    | Search T1D books. Use `book`+`heading` to fetch full section after searching |
| read_saved   | Read a previously saved file (from `save_to_file=true`) |
| auth         | Check if session cookies are valid |

## Workflow

**Planning a trip**: ALWAYS start by checking `favorites products` and
`favorites brands` to learn the user's preferred brands and purchase patterns.
Then `offers sync` + `offers clip_all` for fresh coupons. Use `plan` with the
grocery list, or `search_batch` for multiple queries at once (much faster than
calling `search` in a loop). Check `deals` for this week's best values. Confirm
items with the user before calling `add`.

**Finding best value**: `search` returns unit pricing ($/oz, $/lb). Always check
if the recommended item is on sale or has a clipped coupon via `deals`. For
price history context, call `price` with the SKU. If a price looks implausibly
low (e.g. $0.18/lb for chicken), the result will include `price_suspect` — do
NOT recommend suspect prices without asking the user to verify on the website.

**Coupon workflow**: `offers list` shows unclipped coupons. `offers clip_all`
clips everything at once. After clipping, relevant discounts apply automatically
at checkout — no promo code needed.

**Memory & blacklist**: Use `memory get` at the start of a session to load user's
T1D config and preferences. Notes with key "blacklist" are consulted during
search — blacklisted items are flagged. Use `memory note` to record discoveries
(liked recipes, items to avoid, brands that were good value). Use `memory set`
to update structured fields like `gi_ceiling` or `carb_target_per_meal`.

**Cart verification**: After adding/removing items, check the `cart_total`,
`cart_items`, and `cart_units` returned in the response — they reflect the actual
cart state. If the cart drifts from expectations, use `cart_diff` with the expected
SKUs to diagnose what's missing or extra. The `cart` tool returns both `items_count`
(unique line items) and `total_units` (sum of all quantities) — the website shows
`total_units`.

**Store/location change**: If items show "No Longer Available" or search results
include a `store_warning`, run `price clear=true` to reset the local cache.
The cache tracks which store each SKU came from. Changing delivery address
invalidates cached SKUs.

**Failure handling**: If `remove` returns `"reason": "not_in_cart"`, do NOT retry.
The response includes `cart_skus` showing what's actually in the cart. If `add`
returns `"ok": false`, check the returned cart state to understand why.

**Weight/size data**: The API often lacks accurate pack weights for meat and
produce. When comparing items by unit price ($/lb):
- If weight is missing, flag it: "Weight not in API — verify pack size"
- Use $/lb from the price field as truth for "sold by weight" items
- Ask user to confirm pack size before making price comparisons

## T1D Nutrition

When GI data is available in search or plan results:
- `gi_cat: "low"` (GI < 55) — preferred, no flag needed
- `gi_cat: "medium"` (GI 55-69) — note portions, don't block
- `flag: "HIGH_GI"` or `flag: "WARN_GI"` — mention the flag and suggest the
  `gi_swap` value if present

Always report carb counts when discussing recipes or meals. Insulin timing
(pre-bolusing, extended bolus) is important context — ask about the user's
routine if meal planning.
""".strip()

from .api import (
    create_client,
    search_products,
    add_to_cart as api_add_to_cart,
    remove_from_cart,
    get_cart,
    get_offers,
    clip_all_offers,
    get_orders,
    get_products_by_sku,
    get_previously_purchased,
    get_store_id,
    CartItem,
)
from .db import (
    get_connection,
    sync_products_from_search,
    sync_coupons_from_api,
    sync_order_items,
    get_last_purchase_date,
    get_product_with_history,
    is_good_deal,
    search_products_local,
    get_price_stats,
    sync_previously_purchased,
    get_favorite_products,
    get_favorite_brands,
    get_purchase_stats,
    check_store_mismatch,
)
from .reasoning import (
    evaluate_options,
    get_purchase_frequency,
    should_buy_this_trip,
    check_price_sanity,
    PurchaseFrequency,
)
from .cart_builder import find_best_product
from .auth import check_auth_status
from .unit_pricing import calculate_unit_prices
from .preferences import load_preferences

# Canonical cookies path
COOKIES_PATH = Path.home() / ".config" / "raley-assistant" / "cookies.json"

# Response limits
MAX_PLAN_ITEMS = 25
MAX_ERROR_LENGTH = 200

server = Server("raley-assistant")

# Load user preferences once at import time
_prefs = load_preferences()

# Build preferred brands lookup for reasoning engine
_preferred_brands: dict[str, str] = {
    cat: pref.brand
    for cat, pref in _prefs.product_prefs.items()
    if pref.brand
}


def _check_blacklist(sku: str, product_name: str) -> str | None:
    """Check if a SKU or product name matches any blacklist entries in memory.

    Returns a warning string if blacklisted, or None.
    """
    mem = load_memory()
    name_lower = product_name.lower()
    sku_lower = sku.lower()

    # Check blacklist notes: does any meaningful keyword from the note appear in the product name?
    if mem.notes:
        for key, value in mem.notes.items():
            if "blacklist" not in key.lower():
                continue
            value_lower = value.lower()
            # Direct SKU match in note
            if sku_lower in value_lower:
                return f"Matches blacklist note '{key}': {value[:80]}"
            # Note keywords found in product name (5+ chars to avoid common words)
            note_keywords = [w for w in value_lower.split() if len(w) >= 5]
            if any(kw in name_lower for kw in note_keywords):
                return f"Matches blacklist note '{key}': {value[:80]}"

    # Check avoid_brands in shopping config
    if mem.shopping.avoid_brands:
        for brand in mem.shopping.avoid_brands:
            if brand.lower() in name_lower:
                return f"Brand '{brand}' is in your avoid list"

    return None


def _truncate(s: str, maxlen: int = 40) -> str:
    """Truncate string with ellipsis indicator."""
    return s[:maxlen - 3] + "..." if len(s) > maxlen else s


def get_api_client():
    """Get authenticated API client."""
    if not COOKIES_PATH.exists():
        raise RuntimeError(
            f"Cookies not found at {COOKIES_PATH}. Run 'raley-bot login' first."
        )
    return create_client(COOKIES_PATH)


def save_result_to_file(filename_prefix: str, data: dict) -> str:
    """Save result data to file with restricted permissions. Returns file path."""
    base_dir = Path.home() / ".local" / "share" / "raley-assistant"
    base_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize prefix to prevent path traversal
    safe_prefix = re.sub(r"[^\w-]", "", filename_prefix)[:30]
    from datetime import timezone
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filepath = base_dir / f"{safe_prefix}-{timestamp}.json"

    # Write with owner-only permissions
    fd = os.open(str(filepath), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(data, f, indent=2)

    return str(filepath)


# ============================================================================
# TOOL DEFINITIONS
# ============================================================================

TOOLS = [
    Tool(
        name="deals",
        description="Best-value items this week: clipped coupons + sale prices + price history. Run after 'offers sync'.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "gi_filter": {"type": "boolean", "description": "Only show low-GI items (GI < 55)"},
            },
        },
    ),
    Tool(
        name="memory",
        description="Read or write shopping memory: T1D config, notes, preferences. Persists across sessions.",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["get", "set", "note"]},
                "section": {"type": "string", "enum": ["t1d", "shopping", "notes"], "description": "For get: filter to section. For set: target section."},
                "key": {"type": "string", "description": "Field name for action=set, note key for action=note"},
                "value": {"type": "string", "description": "Value for action=set or action=note"},
                "limit": {"type": "integer", "description": "For action=get with section=notes: max notes to return"},
            },
            "required": ["action"],
        },
    ),
    Tool(
        name="search",
        description="Search products. Returns best match with SKU, price, unit pricing.",
        inputSchema={
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "sale": {"type": "boolean"},
                "diet": {"type": "string"},
            },
            "required": ["q"],
        },
    ),
    Tool(
        name="add",
        description="Add to cart. Need SKU and price_cents from search.",
        inputSchema={
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "qty": {"type": "integer"},
                "cents": {"type": "integer"},
            },
            "required": ["sku", "cents"],
        },
    ),
    Tool(
        name="remove",
        description="Remove from cart by SKU.",
        inputSchema={
            "type": "object",
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"],
        },
    ),
    Tool(
        name="cart",
        description="View cart. Supports summary_only, save_to_file, and limit options.",
        inputSchema={
            "type": "object",
            "properties": {
                "summary_only": {"type": "boolean"},
                "save_to_file": {"type": "boolean"},
                "limit": {"type": "integer"},
            },
        },
    ),
    Tool(
        name="offers",
        description="Coupons. action: list|clip_all|sync.",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "clip_all", "sync"]},
                "cat": {"type": "string"},
                "save_to_file": {"type": "boolean"},
                "limit": {"type": "integer"},
            },
        },
    ),
    Tool(
        name="plan",
        description="Parse grocery list, find matches. Does not add to cart.",
        inputSchema={
            "type": "object",
            "properties": {
                "items": {"type": "string"},
                "budget": {"type": "number"},
                "summary_only": {"type": "boolean"},
                "save_to_file": {"type": "boolean"},
            },
            "required": ["items"],
        },
    ),
    Tool(
        name="price",
        description="Price history by SKU, search local DB, or clear cache. Use clear=true when user changes store/delivery location.",
        inputSchema={
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "q": {"type": "string"},
                "clear": {
                    "type": "string",
                    "description": "Clear cache: 'true' for products only, 'all' for everything",
                },
            },
        },
    ),
    Tool(
        name="orders",
        description="Order history.",
        inputSchema={
            "type": "object",
            "properties": {
                "days": {"type": "integer"},
                "limit": {"type": "integer"},
                "summary_only": {"type": "boolean"},
                "save_to_file": {"type": "boolean"},
            },
        },
    ),
    Tool(
        name="favorites",
        description="Purchase history from 'previously purchased' API.",
        inputSchema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["products", "brands", "stats", "sync"],
                    "description": "products=by recency, brands=by product count, stats=summary, sync=refresh from API",
                },
                "limit": {"type": "integer"},
            },
        },
    ),
    Tool(
        name="auth",
        description="Check authentication status (no cookie values exposed).",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="knowledge",
        description="Search installed T1D reference books: GI tables, insulin math, carb counting, recipes, meal planning. Use when the user asks about nutrition science, insulin timing, or needs recipe ideas.",
        inputSchema={
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Search terms (space separated). Empty string lists books."},
                "book": {"type": "string", "description": "Specific book filename stem to search (optional, omit to search all)"},
                "heading": {"type": "string", "description": "Fetch full content of this heading (use after searching to get complete text)"},
                "limit": {"type": "integer"},
            },
        },
    ),
    Tool(
        name="read_saved",
        description="Read a previously saved result file (from save_to_file=true). Returns the JSON contents.",
        inputSchema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Filename (e.g. 'cart-20260308-143022.json') or just the prefix ('cart')"},
            },
        },
    ),
    Tool(
        name="add_plan",
        description="Bulk add items to cart. Pass comma-separated 'sku:cents' or 'sku:cents:qty' pairs.",
        inputSchema={
            "type": "object",
            "properties": {
                "items": {"type": "string", "description": "Comma-separated 'sku:cents' or 'sku:cents:qty' (e.g. '10101877:499,10102003:299:2')"},
            },
            "required": ["items"],
        },
    ),
    Tool(
        name="search_batch",
        description="Search multiple products at once. Returns best match per query. Use instead of calling search N times.",
        inputSchema={
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of product queries (max 15)",
                },
            },
            "required": ["queries"],
        },
    ),
    Tool(
        name="cart_diff",
        description="Compare a list of expected SKUs against the actual cart. Shows missing, extra, and quantity mismatches.",
        inputSchema={
            "type": "object",
            "properties": {
                "expected_skus": {
                    "type": "string",
                    "description": "Comma-separated SKUs (or sku:qty pairs) that should be in cart",
                },
            },
            "required": ["expected_skus"],
        },
    ),
]


# ============================================================================
# TOOL HANDLERS
# ============================================================================


async def handle_search(args: dict) -> str:
    """Search with reasoning and unit pricing. No side effects."""
    client = get_api_client()
    query = args["q"]

    products = search_products(
        client,
        query,
        on_sale=args.get("sale", False),
        dietary_filter=args.get("diet"),
        limit=5,
    )

    if not products:
        return json.dumps({"error": "No products found", "query": query})

    # Sync to local price DB (non-destructive) with store tracking
    store_mismatch_warning = None
    try:
        store_id = get_store_id(client)
    except Exception:
        store_id = ""
    try:
        conn = get_connection()
        try:
            sync_products_from_search(conn, products, store_id=store_id)
            if store_id:
                store_mismatch_warning = check_store_mismatch(conn, store_id)
        finally:
            conn.close()
    except Exception:
        pass

    # Build product dicts with unit pricing
    products_as_dicts = []
    for p in products:
        price_cents = p.sale_price_cents or p.price_cents
        price = price_cents / 100

        unit_pricing_dict = {}
        ppo = p.price_per_oz
        try:
            unit_prices = calculate_unit_prices(
                price_cents,
                p.name,
                p.size or "",
                unit_oz=p.unit_oz,
                weight_lbs=p.weight_lbs,
            )
            unit_pricing_dict = unit_prices.to_dict() if unit_prices else {}
            if unit_prices and unit_prices.price_per_oz is not None:
                ppo = unit_prices.price_per_oz
        except Exception:
            pass

        products_as_dicts.append(
            {
                "name": p.name,
                "sku": str(p.sku),
                "price": price,
                "brand": p.brand,
                "on_sale": p.sale_price_cents is not None,
                "oz": p.unit_oz,
                "price_per_oz": ppo,
                "unit_pricing": unit_pricing_dict,
            }
        )

    decision = evaluate_options(
        products_as_dicts, query,
        prefer_organic=_prefs.general.prefer_organic,
        preferred_brands=_preferred_brands,
    )

    best_product = next(
        (p for p in products_as_dicts if p["sku"] == str(decision.sku)), None
    )
    unit_pricing_dict = (best_product or {}).get("unit_pricing", {})

    result = {
        "sku": decision.sku,
        "name": decision.product_name,
        "price": f"${decision.price:.2f}",
        "cents": round(decision.price * 100),
    }

    if unit_pricing_dict and unit_pricing_dict.get("best"):
        best_metric = unit_pricing_dict["best"]
        result[best_metric] = unit_pricing_dict.get(best_metric, "")

    if decision.flags:
        if "SALE" in decision.flags:
            result["sale"] = True
        if "PRICE_WARNING" in decision.flags:
            result["high_price"] = True

    # T1D annotation
    mem = load_memory()
    t1d = score_t1d(decision.product_name, mem.t1d.gi_ceiling)
    if t1d.gi is not None:
        result["gi"] = t1d.gi
        result["gi_cat"] = t1d.category
    if t1d.flag:
        result["gi_flag"] = t1d.flag
    if t1d.swap_suggestion:
        result["gi_swap"] = t1d.swap_suggestion

    if store_mismatch_warning:
        result["store_warning"] = store_mismatch_warning["message"]

    # Price sanity check — flag implausible unit prices
    if best_product:
        price_warning = check_price_sanity(
            decision.product_name,
            round(decision.price * 100),
            best_product.get("oz"),
        )
        if price_warning:
            result["price_suspect"] = price_warning

    # Blacklist check — warn if this SKU or product name is blacklisted
    try:
        blacklist_warning = _check_blacklist(decision.sku, decision.product_name)
        if blacklist_warning:
            result["blacklisted"] = blacklist_warning
    except Exception:
        pass

    return json.dumps(result)


def _cart_snapshot(client) -> dict:
    """Return compact cart summary for post-mutation feedback."""
    cart = get_cart(client)
    if not cart:
        return {"cart_total": "$0.00", "cart_items": 0, "cart_units": 0}
    items = cart.get("lineItems", [])
    total = cart.get("totalPrice", {}).get("centAmount", 0) / 100
    total_units = sum(item.get("quantity", 1) for item in items)
    return {"cart_total": f"${total:.2f}", "cart_items": len(items), "cart_units": total_units}


async def handle_add(args: dict) -> str:
    """Add to cart. Returns cart state after mutation."""
    client = get_api_client()
    sku = args["sku"]
    qty = args.get("qty", 1)

    item = CartItem(sku=sku, quantity=qty, price_cents=args["cents"])
    success = api_add_to_cart(client, [item])

    product_name = None
    if success:
        try:
            products = get_products_by_sku(client, [sku])
            if products:
                master = products[0].get("masterData", {}).get("current", {})
                product_name = master.get("name", "Unknown")
        except Exception:
            pass

    result: dict[str, Any] = {"ok": success, "sku": sku, "qty": qty}
    if product_name:
        result["name"] = product_name

    # Return cart state so the model can verify without a round-trip
    try:
        result.update(_cart_snapshot(client))
    except Exception:
        pass

    return json.dumps(result)


async def handle_remove(args: dict) -> str:
    """Remove from cart. Returns reason on failure + cart state after.

    remove_from_cart() already fetches the cart internally to find lineItemId,
    so we skip a redundant pre-check and only diagnose failure after the fact.
    """
    client = get_api_client()
    sku = args["sku"]

    success = remove_from_cart(client, sku)

    if not success:
        # Diagnose: empty cart, SKU missing, or API error
        cart = get_cart(client)
        if not cart:
            return json.dumps({"ok": False, "sku": sku, "reason": "cart_fetch_failed"})

        line_items = cart.get("lineItems", [])
        found = any(item.get("variant", {}).get("sku") == sku for item in line_items)
        result: dict[str, Any] = {
            "ok": False,
            "sku": sku,
            "reason": "api_error" if found else "not_in_cart",
        }
        total = cart.get("totalPrice", {}).get("centAmount", 0) / 100
        result["cart_total"] = f"${total:.2f}"
        result["cart_items"] = len(line_items)
        result["cart_units"] = sum(i.get("quantity", 1) for i in line_items)
        if not found:
            result["cart_skus"] = [i.get("variant", {}).get("sku", "") for i in line_items[:10]]
        return json.dumps(result)

    result = {"ok": True, "sku": sku}

    # Return cart state after successful mutation
    try:
        result.update(_cart_snapshot(client))
    except Exception:
        pass

    return json.dumps(result)


async def handle_cart(args: dict) -> str:
    """View cart."""
    client = get_api_client()
    cart = get_cart(client)
    limit = min(args.get("limit", 10), 50)
    summary_only = args.get("summary_only", False)
    save_to_file_flag = args.get("save_to_file", False)

    if not cart:
        if summary_only:
            return json.dumps({"items_count": 0, "total_units": 0, "total": "$0.00"})
        return json.dumps({"items": [], "total": "$0.00", "count": 0, "total_units": 0})

    items = []
    for item in cart.get("lineItems", []):
        name = item.get("name", {})
        if isinstance(name, dict):
            name = name.get("en-US", "Unknown item")
        price = item.get("totalPrice", {}).get("centAmount", 0) / 100
        items.append(
            {
                "name": _truncate(name),
                "qty": item.get("quantity", 1),
                "price": f"${price:.2f}",
                "sku": item.get("variant", {}).get("sku", ""),
            }
        )

    total = cart.get("totalPrice", {}).get("centAmount", 0) / 100
    total_units = sum(i["qty"] for i in items)
    full_cart = {"items": items, "count": len(items), "total_units": total_units, "total": f"${total:.2f}"}

    if save_to_file_flag:
        filepath = save_result_to_file("cart", full_cart)
        return json.dumps(
            {"file_saved": filepath, "items_count": len(items), "total_units": total_units, "total": f"${total:.2f}"}
        )

    if summary_only:
        summary = {"items_count": len(items), "total_units": total_units, "total": f"${total:.2f}"}
        if items:
            summary["top_items"] = [
                {"name": i["name"], "qty": i["qty"], "price": i["price"]}
                for i in items[:3]
            ]
        return json.dumps(summary)

    shown_items = items[:limit]
    response = {"items": shown_items, "count": len(shown_items), "total_units": total_units, "total": f"${total:.2f}"}
    if len(items) > limit:
        response["note"] = f"Showing {limit} of {len(items)} items"

    return json.dumps(response)


async def handle_offers(args: dict) -> str:
    """Manage offers/coupons."""
    action = args.get("action", "list")
    client = get_api_client()
    save_to_file_flag = args.get("save_to_file", False)

    if action == "clip_all":
        clipped, failed, error_samples = clip_all_offers(client)
        result = {"clipped": clipped, "failed": failed}
        if error_samples:
            result["errors"] = error_samples
        return json.dumps(result)

    if action == "sync":
        offers = get_offers(client, rows=500)
        conn = get_connection()
        try:
            count = sync_coupons_from_api(conn, offers)
        finally:
            conn.close()
        result = {"synced": count}
        if len(offers) >= 500:
            result["note"] = "Hit 500 limit, may have more"
        return json.dumps(result)

    # Default: list
    limit = min(args.get("limit", 10), 100)
    offers = get_offers(client, category=args.get("cat"), clipped="Unclipped", rows=100)

    results = [
        {
            "id": o.id,
            "headline": _truncate(o.headline),
            "discount": f"${o.discount_amount:.2f}" if o.discount_amount else "See details",
            "expires": o.end_date[:10] if o.end_date else "",
        }
        for o in offers
    ]

    full_results = {"offers": results, "count": len(results)}

    if save_to_file_flag:
        filepath = save_result_to_file("offers", full_results)
        return json.dumps({"file_saved": filepath, "offers_count": len(results)})

    shown_offers = results[:limit]
    response = {"offers": shown_offers, "count": len(shown_offers)}
    if len(results) > limit:
        response["note"] = f"Showing {limit} of {len(results)} offers"

    return json.dumps(response)


async def handle_build_list(args: dict) -> str:
    """Build grocery list with reasoning."""
    from .cart_builder import parse_grocery_list

    client = get_api_client()
    all_parsed = parse_grocery_list(args["items"])
    parsed = all_parsed[:MAX_PLAN_ITEMS]
    truncated = len(all_parsed) > MAX_PLAN_ITEMS
    budget = args.get("budget")
    summary_only = args.get("summary_only", False)
    save_to_file = args.get("save_to_file", False)

    mem = load_memory()
    gi_ceiling = mem.t1d.gi_ceiling

    results = []
    total = 0.0
    confirm_needed = []
    not_found = []

    for item_name, qty in parsed:
        products = find_best_product(client, item_name, max_results=5)

        if not products:
            results.append({"item": item_name, "qty": qty, "found": False})
            not_found.append(item_name)
            continue

        decision = evaluate_options(
            products, item_name,
            prefer_organic=_prefs.general.prefer_organic,
            preferred_brands=_preferred_brands,
        )

        # Check order history for recent purchases
        recently_bought = False
        buy_note = ""
        try:
            conn = get_connection()
            try:
                last_date = get_last_purchase_date(conn, decision.sku)
            finally:
                conn.close()
            if last_date:
                should_buy, buy_note = should_buy_this_trip(
                    decision.product_name, last_purchased=last_date
                )
                if not should_buy:
                    recently_bought = True
        except Exception:
            pass

        freq, _ = get_purchase_frequency(decision.product_name, item_name)
        line_total = decision.price * qty
        total += line_total

        r = {
            "item": item_name,
            "qty": qty,
            "match": _truncate(decision.product_name),
            "sku": decision.sku,
            "unit": f"${decision.price:.2f}",
            "line": f"${line_total:.2f}",
            "cents": round(decision.price * 100),
        }

        if recently_bought:
            r["recently_bought"] = True
            r["buy_note"] = buy_note

        if decision.flags:
            r["flags"] = decision.flags

        if freq in (PurchaseFrequency.MONTHLY, PurchaseFrequency.QUARTERLY):
            r["confirm"] = freq.name.lower()
            confirm_needed.append(item_name)

        # T1D annotation
        t1d = score_t1d(decision.product_name, gi_ceiling)
        if t1d.gi is not None:
            r["gi"] = t1d.gi
            r["gi_cat"] = t1d.category
        if t1d.flag:
            r["gi_flag"] = t1d.flag
        if t1d.swap_suggestion:
            r["gi_swap"] = t1d.swap_suggestion

        results.append(r)

    full_response: dict[str, Any] = {"items": results, "total": f"${total:.2f}"}

    if budget:
        full_response["budget"] = f"${budget:.2f}"
        full_response["under"] = total <= budget

    if confirm_needed:
        full_response["confirm_needed"] = confirm_needed

    if not_found:
        full_response["not_found"] = not_found

    if truncated:
        full_response["truncated"] = f"Limited to {MAX_PLAN_ITEMS} items"

    if save_to_file:
        filepath = save_result_to_file("grocery-plan", full_response)
        summary: dict[str, Any] = {
            "file_saved": filepath,
            "items_count": len([r for r in results if r.get("found", True)]),
            "total": full_response["total"],
        }
        if not_found:
            summary["not_found_count"] = len(not_found)
        if confirm_needed:
            summary["confirm_needed"] = confirm_needed
        if budget:
            summary["budget_status"] = f"${total:.2f} of ${budget:.2f}"
        return json.dumps(summary)

    if summary_only:
        summary = {
            "items_count": len([r for r in results if r.get("found", True)]),
            "total": full_response["total"],
        }
        if not_found:
            summary["not_found"] = not_found
        if confirm_needed:
            summary["confirm_needed"] = confirm_needed
        if budget:
            summary["budget_status"] = f"${total:.2f} of ${budget:.2f}"
        if truncated:
            summary["truncated"] = full_response["truncated"]
        return json.dumps(summary)

    return json.dumps(full_response)


async def handle_price_check(args: dict) -> str:
    """Check price history or search local DB."""
    conn = get_connection()
    try:
        # Clear cache when store/location changes
        if args.get("clear"):
            tables = ["products", "price_history"]
            if args.get("clear") == "all":
                tables.extend(["purchase_history", "coupons", "order_items"])
            counts = {}
            for table in tables:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                conn.execute(f"DELETE FROM {table}")
                counts[table] = count
            conn.commit()
            return json.dumps({
                "cleared": counts,
                "message": "Cache cleared. Fresh searches will rebuild from API.",
            })

        if args.get("q"):
            results = search_products_local(conn, args["q"], 10)
            if not results:
                return json.dumps({"error": "No local results"})
            return json.dumps(
                {
                    "products": [
                        {
                            "sku": r["sku"],
                            "name": _truncate(r["name"]),
                            "price": f"${r['price_cents']/100:.2f}"
                            if r.get("price_cents")
                            else None,
                        }
                        for r in results
                    ],
                    "count": len(results),
                }
            )

        if args.get("sku"):
            record = get_product_with_history(conn, args["sku"])
            if not record:
                return json.dumps({"error": "No history", "sku": args["sku"]})

            is_deal, reason = is_good_deal(conn, args["sku"], record.current_price_cents)

            return json.dumps(
                {
                    "sku": record.sku,
                    "name": _truncate(record.name),
                    "current": f"${record.current_price_cents/100:.2f}",
                    "avg": f"${record.avg_price/100:.2f}",
                    "min": f"${record.min_price/100:.2f}",
                    "max": f"${record.max_price/100:.2f}",
                    "deal": is_deal,
                    "analysis": reason,
                }
            )

        stats = get_price_stats(conn)
        return json.dumps(stats)
    finally:
        conn.close()


async def handle_orders(args: dict) -> str:
    """Get order history."""
    client = get_api_client()
    days_back = min(args.get("days", 90), 365)
    limit = min(args.get("limit", 10), 30)
    summary_only = args.get("summary_only", False)
    save_to_file_flag = args.get("save_to_file", False)

    all_orders = get_orders(client, days_back=days_back, limit=30)

    # Note: Orders API doesn't return lineItems, so we can't sync individual products here.
    # Use `favorites sync` instead, which pulls from the "previously purchased" search filter.

    results = []
    total_spent = 0.0
    for o in all_orders:
        # totalPrice is already in dollars (e.g., 87.33), not cents
        total_price = o.get("totalPrice", 0)
        if isinstance(total_price, dict):
            # Handle centAmount format if API ever changes
            total_price = total_price.get("centAmount", 0) / 100

        total_spent += total_price

        results.append(
            {
                "id": str(o.get("orderId", ""))[-8:],
                "date": str(o.get("createdDate", ""))[:10],
                "status": o.get("orderStatus", {}).get("value", ""),
                "total": f"${total_price:.2f}",
                "product_total": f"${o.get('productAmount', 0):.2f}",
            }
        )

    if save_to_file_flag:
        filepath = save_result_to_file("orders", {"orders": results, "count": len(results)})
        summary: dict[str, Any] = {
            "file_saved": filepath,
            "orders_count": len(results),
            "total_spent": f"${total_spent:.2f}",
        }
        if results:
            summary["date_range"] = f"{results[-1]['date']} to {results[0]['date']}"
        return json.dumps(summary)

    if summary_only:
        summary = {
            "orders_count": len(results),
            "total_spent": f"${total_spent:.2f}",
        }
        if results:
            summary["date_range"] = f"{results[-1]['date']} to {results[0]['date']}"
            summary["most_recent"] = results[0]["date"]
        return json.dumps(summary)

    shown_orders = results[:limit]
    response = {"orders": shown_orders, "count": len(shown_orders)}
    if len(results) > limit:
        response["note"] = f"Showing {limit} of {len(results)} orders"

    return json.dumps(response)


async def handle_auth(args: dict) -> str:
    """Check authentication status."""
    status = check_auth_status()
    return json.dumps(status)


async def handle_favorites(args: dict) -> str:
    """Get purchase history analysis: top products, brands, patterns."""
    query_type = args.get("type", "products")
    limit = min(args.get("limit", 20), 50)

    conn = get_connection()
    try:
        if query_type == "sync":
            # Refresh from API - paginate until exhausted
            client = get_api_client()
            all_products = []
            offset = 0
            page_size = 30
            max_items = 2000  # Safety cap
            last_page_skus: set[str] = set()

            while len(all_products) < max_items:
                page = get_previously_purchased(client, offset=offset, limit=page_size)
                if not page:
                    break

                # Detect stuck pagination (API ignoring offset)
                current_skus = {p.sku for p in page}
                if current_skus == last_page_skus:
                    break  # Same page returned twice
                last_page_skus = current_skus

                all_products.extend(page)
                if len(page) < page_size:
                    break  # Last page
                offset += page_size

            synced = sync_previously_purchased(conn, all_products)
            # Also sync to products table for price/brand data
            sync_products_from_search(conn, all_products)
            return json.dumps({
                "synced": synced,
                "message": f"Synced {synced} previously purchased products",
            })

        elif query_type == "brands":
            brands = get_favorite_brands(conn, limit=limit)
            return json.dumps({"brands": brands, "count": len(brands)})

        elif query_type == "stats":
            stats = get_purchase_stats(conn)
            return json.dumps(stats)

        else:  # products (default)
            products = get_favorite_products(conn, limit=limit)
            return json.dumps({"products": products, "count": len(products)})

    finally:
        conn.close()


async def handle_deals(args: dict) -> str:
    """Return best-value items: on-sale products with clipped coupons + price history."""
    client = get_api_client()
    limit = min(args.get("limit", 15), 50)
    gi_filter = args.get("gi_filter", False)

    # Load clipped coupons from local DB first (sync separately via offers sync)
    conn = get_connection()
    try:
        clipped_rows = conn.execute(
            "SELECT offer_id, headline, discount_amount FROM coupons WHERE is_clipped = 1 LIMIT 200"
        ).fetchall()
    finally:
        conn.close()

    # Fetch currently on-sale products (up to 30)
    try:
        sale_products = search_products(client, "", on_sale=True, limit=30)
    except Exception:
        sale_products = []

    # Also fetch previously purchased items that are on sale
    try:
        prev_products = search_products(client, "", previously_purchased=True, on_sale=True, limit=20)
        seen_skus = {p.sku for p in sale_products}
        sale_products += [p for p in prev_products if p.sku not in seen_skus]
    except Exception:
        pass

    # Load clipped coupon offers from API to get product_skus
    coupon_matches: dict[str, list] = {}
    try:
        clipped_offers = get_offers(client, clipped="Clipped", rows=100)
        products_as_dicts = [{"sku": p.sku, "name": p.name} for p in sale_products]
        coupon_matches = find_coupon_matches(clipped_offers, products_as_dicts)
    except Exception:
        pass

    # Build deal results with T1D and price history context
    mem = load_memory()
    gi_ceiling = mem.t1d.gi_ceiling
    results = []
    conn = get_connection()
    try:
        for p in sale_products:
            if not p.sale_price_cents:
                continue

            discount_cents = p.price_cents - p.sale_price_cents
            discount_pct = (discount_cents / p.price_cents * 100) if p.price_cents else 0

            entry: dict[str, Any] = {
                "sku": p.sku,
                "name": _truncate(p.name),
                "sale": f"${p.sale_price_cents / 100:.2f}",
                "was": f"${p.price_cents / 100:.2f}",
                "save": f"${discount_cents / 100:.2f} ({discount_pct:.0f}%)",
            }

            # Unit pricing
            if p.price_per_oz:
                entry["per_oz"] = f"${p.price_per_oz:.3f}"

            # Price history context
            try:
                is_deal, reason = is_good_deal(conn, p.sku, p.sale_price_cents)
                if is_deal:
                    entry["history"] = reason
            except Exception:
                pass

            # Clipped coupon match
            if p.sku in coupon_matches:
                entry["coupons"] = coupon_matches[p.sku]

            # T1D annotation
            t1d = score_t1d(p.name, gi_ceiling)
            if t1d.gi is not None:
                entry["gi"] = t1d.gi
                entry["gi_cat"] = t1d.category
            if t1d.flag:
                entry["gi_flag"] = t1d.flag
            if t1d.swap_suggestion and t1d.flag == "HIGH_GI":
                entry["gi_swap"] = t1d.swap_suggestion

            if gi_filter and t1d.category != "low":
                continue

            results.append(entry)
    finally:
        conn.close()

    # Sort: coupon+sale first, then by discount %
    results.sort(
        key=lambda x: (1 if "coupons" in x else 0, float(x.get("save", "0").split(" ")[0][1:])),
        reverse=True,
    )

    clipped_count = len(clipped_rows)
    return json.dumps({
        "deals": results[:limit],
        "total_found": len(results),
        "clipped_coupons_on_file": clipped_count,
        "tip": "Run 'offers sync' then 'offers clip_all' to refresh coupons before shopping.",
    })


async def handle_knowledge(args: dict) -> str:
    """Search installed T1D reference books."""
    from .knowledge import KNOWLEDGE_DIR, _chunk_file

    query = args.get("q", "").strip()
    book = args.get("book")
    heading = args.get("heading")

    # Fetch mode: get full content of a specific heading
    if heading and book:
        target = (KNOWLEDGE_DIR / f"{book}.md").resolve()
        if not target.exists() or not str(target).startswith(str(KNOWLEDGE_DIR.resolve())):
            return json.dumps({"error": f"Book not found: {book}"})
        try:
            chunks = _chunk_file(target)
            for h, content in chunks:
                if h.lower() == heading.lower():
                    return json.dumps({"book": book, "heading": h, "content": content})
            return json.dumps({"error": f"Heading not found: {heading}", "available": [h for h, _ in chunks[:20]]})
        except OSError as e:
            return json.dumps({"error": str(e)})

    if not query:
        books = list_books()
        return json.dumps({"books": books, "tip": "Pass q= to search, or book+heading to fetch full section"})

    results = search_knowledge(
        query,
        book=book,
        limit=min(args.get("limit", 5), 10),
    )
    if not results:
        return json.dumps({"results": [], "tip": "Try broader keywords or check installed books with knowledge q=''"})
    return json.dumps({"results": results, "count": len(results)})


async def handle_memory(args: dict) -> str:
    """Read or write shopping memory."""
    action = args.get("action", "get")

    if action == "get":
        mem = load_memory()
        section = args.get("section")
        limit = args.get("limit", 20)

        # Section-filtered responses — use exact dataclass field names for round-trip safety
        if section == "t1d":
            t = mem.t1d
            return json.dumps({
                "carb_target_per_meal": t.carb_target_per_meal,
                "gi_ceiling": t.gi_ceiling,
                "target_bg": t.target_bg,
                "avoid_high_gi": t.avoid_high_gi,
                "prefer_low_carb": t.prefer_low_carb,
                "insulin_to_carb_ratio": t.insulin_to_carb_ratio or None,
                "correction_factor": t.correction_factor or None,
                "avoid_items": t.avoid_items or None,
                "safe_snacks": t.safe_snacks or None,
                "favorite_proteins": t.favorite_proteins or None,
                "favorite_recipes": t.favorite_recipes or None,
            })
        if section == "shopping":
            s = mem.shopping
            return json.dumps({
                "weekly_budget": s.weekly_budget,
                "prefer_store_brand": s.prefer_store_brand,
                "max_unit_price_oz": s.max_unit_price_oz,
                "staples": s.staples or None,
                "avoid_brands": s.avoid_brands or None,
                "preferred_store_section": s.preferred_store_section or None,
            })
        if section == "notes":
            # Paginated notes (alphabetical by key - no timestamp tracking)
            notes = dict(sorted(mem.notes.items())[:limit])
            return json.dumps({"notes": notes, "total": len(mem.notes)})

        # Full summary (default)
        return json.dumps(get_summary(mem))

    if action == "set":
        section = args.get("section", "")
        key = args.get("key", "")
        value = args.get("value", "")
        if not section or not key:
            return json.dumps({"error": "section and key required for action=set"})
        ok, msg = set_field(section, key, value)
        return json.dumps({"ok": ok, "message": msg})

    if action == "note":
        key = args.get("key", "")
        value = args.get("value", "")
        if not key or not value:
            return json.dumps({"error": "key and value required for action=note"})
        add_note(key, value)
        return json.dumps({"ok": True, "note_key": key})

    return json.dumps({"error": f"Unknown action: {action}. Use get, set, or note."})


async def handle_read_saved(args: dict) -> str:
    """Read a previously saved result file."""
    filename = args.get("filename", "").strip()
    base_dir = Path.home() / ".local" / "share" / "raley-assistant"

    if not filename:
        # List available files
        if not base_dir.exists():
            return json.dumps({"files": [], "tip": "No saved files yet"})
        files = sorted(base_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:20]
        return json.dumps({
            "files": [f.name for f in files],
            "tip": "Pass filename= to read a specific file",
        })

    # Sanitize filename
    safe_name = re.sub(r"[^\w.-]", "", filename)
    if not safe_name.endswith(".json"):
        # Find most recent file matching prefix
        candidates = sorted(base_dir.glob(f"{safe_name}*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not candidates:
            return json.dumps({"error": f"No files matching prefix: {safe_name}"})
        target = candidates[0]
    else:
        target = base_dir / safe_name

    target = target.resolve()
    if not str(target).startswith(str(base_dir.resolve())):
        return json.dumps({"error": "Invalid path"})
    if not target.exists():
        return json.dumps({"error": f"File not found: {target.name}"})

    try:
        with open(target) as f:
            data = json.load(f)
        return json.dumps({"filename": target.name, "data": data})
    except (json.JSONDecodeError, OSError) as e:
        return json.dumps({"error": f"Failed to read: {e}"})


async def handle_add_plan(args: dict) -> str:
    """Bulk add items to cart from plan output."""
    client = get_api_client()
    items_str = args.get("items", "")

    if not items_str:
        return json.dumps({"error": "items required: comma-separated 'sku:cents' or 'sku:cents:qty'"})

    # Parse sku:cents:qty format
    cart_items = []
    errors = []
    for part in items_str.split(","):
        part = part.strip()
        if not part:
            continue
        pieces = part.split(":")
        if len(pieces) < 2:
            errors.append(f"Invalid format: {part}")
            continue
        try:
            sku = pieces[0].strip()
            cents = int(pieces[1].strip())
            qty = int(pieces[2].strip()) if len(pieces) > 2 else 1
            cart_items.append(CartItem(sku=sku, quantity=qty, price_cents=cents))
        except ValueError:
            errors.append(f"Invalid numbers: {part}")

    if not cart_items:
        return json.dumps({"error": "No valid items to add", "parse_errors": errors})

    success = api_add_to_cart(client, cart_items)
    result: dict[str, Any] = {
        "ok": success,
        "attempted": len(cart_items),
        "added": len(cart_items) if success else 0,
        "skus": [c.sku for c in cart_items],
    }
    if not success:
        result["error"] = "Cart API returned failure"
    if errors:
        result["parse_errors"] = errors

    # Return cart state after mutation
    try:
        result.update(_cart_snapshot(client))
    except Exception:
        pass

    return json.dumps(result)


async def handle_search_batch(args: dict) -> str:
    """Search multiple products at once, return best match per query."""
    import time

    client = get_api_client()
    queries = args.get("queries", [])

    if not queries:
        return json.dumps({"error": "queries array required"})

    max_queries = 15
    if len(queries) > max_queries:
        queries = queries[:max_queries]

    mem = load_memory()
    gi_ceiling = mem.t1d.gi_ceiling
    results = []

    # Fetch store_id once for the whole batch (consistent with handle_search)
    try:
        store_id = get_store_id(client)
    except Exception:
        store_id = ""

    for i, query in enumerate(queries):
        query = query.strip()
        if not query:
            results.append({"query": query, "found": False})
            continue

        try:
            products = search_products(client, query, limit=5)
        except Exception:
            results.append({"query": query, "found": False, "error": "search_failed"})
            continue

        if not products:
            results.append({"query": query, "found": False})
            continue

        # Sync to DB with store tracking (consistent with handle_search)
        try:
            conn = get_connection()
            try:
                sync_products_from_search(conn, products, store_id=store_id)
            finally:
                conn.close()
        except Exception:
            pass

        # Build product dicts for reasoning
        products_as_dicts = []
        for p in products:
            price_cents = p.sale_price_cents or p.price_cents
            price = price_cents / 100
            products_as_dicts.append({
                "name": p.name,
                "sku": str(p.sku),
                "price": price,
                "brand": p.brand,
                "on_sale": p.sale_price_cents is not None,
                "oz": p.unit_oz,
                "price_per_oz": p.price_per_oz,
            })

        decision = evaluate_options(
            products_as_dicts, query,
            prefer_organic=_prefs.general.prefer_organic,
            preferred_brands=_preferred_brands,
        )

        entry: dict[str, Any] = {
            "query": query,
            "found": True,
            "sku": decision.sku,
            "name": _truncate(decision.product_name),
            "price": f"${decision.price:.2f}",
            "cents": round(decision.price * 100),
        }

        if decision.flags:
            entry["flags"] = decision.flags

        # T1D annotation
        t1d = score_t1d(decision.product_name, gi_ceiling)
        if t1d.gi is not None:
            entry["gi"] = t1d.gi
            entry["gi_cat"] = t1d.category
        if t1d.flag:
            entry["gi_flag"] = t1d.flag

        results.append(entry)

        # Rate limiting between searches: 200ms normally, 500ms every 10 (per CLAUDE.md)
        if i < len(queries) - 1:
            time.sleep(0.5 if (i + 1) % 10 == 0 else 0.2)

    total_cents = sum(r.get("cents", 0) for r in results if r.get("found"))
    return json.dumps({
        "results": results,
        "found": sum(1 for r in results if r.get("found")),
        "not_found": sum(1 for r in results if not r.get("found")),
        "estimated_total": f"${total_cents / 100:.2f}",
    })


async def handle_cart_diff(args: dict) -> str:
    """Compare expected SKUs against actual cart contents."""
    client = get_api_client()
    cart = get_cart(client)

    if not cart:
        return json.dumps({"error": "Could not fetch cart"})

    # Parse expected: "sku1,sku2" or "sku1:2,sku2:1"
    expected_str = args.get("expected_skus", "")
    if not expected_str:
        return json.dumps({"error": "expected_skus required (comma-separated SKUs or sku:qty pairs)"})
    expected: dict[str, int] = {}
    for part in expected_str.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            pieces = part.split(":")
            expected[pieces[0].strip()] = int(pieces[1].strip()) if len(pieces) > 1 else 1
        else:
            expected[part] = 1

    # Build actual cart state
    actual: dict[str, dict] = {}
    for item in cart.get("lineItems", []):
        sku = item.get("variant", {}).get("sku", "")
        if sku:
            name = item.get("name", {})
            if isinstance(name, dict):
                name = name.get("en-US", "Unknown")
            actual[sku] = {
                "qty": item.get("quantity", 1),
                "name": _truncate(name),
            }

    missing = []
    qty_mismatch = []
    matched = []

    for sku, expected_qty in expected.items():
        if sku not in actual:
            missing.append({"sku": sku, "expected_qty": expected_qty})
        elif actual[sku]["qty"] != expected_qty:
            qty_mismatch.append({
                "sku": sku,
                "name": actual[sku]["name"],
                "expected_qty": expected_qty,
                "actual_qty": actual[sku]["qty"],
            })
        else:
            matched.append({"sku": sku, "name": actual[sku]["name"], "qty": expected_qty})

    extra = [
        {"sku": sku, "name": info["name"], "qty": info["qty"]}
        for sku, info in actual.items()
        if sku not in expected
    ]

    total = cart.get("totalPrice", {}).get("centAmount", 0) / 100
    total_units = sum(info["qty"] for info in actual.values())

    result: dict[str, Any] = {
        "matched": len(matched),
        "cart_total": f"${total:.2f}",
        "cart_items": len(actual),
        "cart_units": total_units,
    }

    if missing:
        result["missing"] = missing
    if qty_mismatch:
        result["qty_mismatch"] = qty_mismatch
    if extra:
        result["extra"] = extra[:10]  # Cap to avoid huge responses
        if len(extra) > 10:
            result["extra_truncated"] = len(extra)

    if not missing and not qty_mismatch:
        result["status"] = "cart_matches"
    else:
        result["status"] = "differences_found"

    return json.dumps(result)


TOOL_HANDLERS = {
    "search": handle_search,
    "add": handle_add,
    "remove": handle_remove,
    "cart": handle_cart,
    "offers": handle_offers,
    "plan": handle_build_list,
    "price": handle_price_check,
    "orders": handle_orders,
    "favorites": handle_favorites,
    "auth": handle_auth,
    "deals": handle_deals,
    "memory": handle_memory,
    "knowledge": handle_knowledge,
    "read_saved": handle_read_saved,
    "add_plan": handle_add_plan,
    "search_batch": handle_search_batch,
    "cart_diff": handle_cart_diff,
}


# ============================================================================
# MCP SERVER SETUP
# ============================================================================


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]

    try:
        result = await handler(arguments)
        return [TextContent(type="text", text=result)]
    except Exception as e:
        raw = str(e) if str(e) else "Unknown error"
        # Strip paths and potential credential fragments from error messages
        sanitized = re.sub(r"(/[\w./-]+)", "<path>", raw)
        sanitized = re.sub(r"(FLDR\.\w+=)[^\s;]+", r"\1<redacted>", sanitized)
        msg = sanitized[:MAX_ERROR_LENGTH]
        return [TextContent(type="text", text=json.dumps({"error": msg}))]


# ---------------------------------------------------------------------------
# Channel 4: MCP Prompts — slash commands /mcp__raley-assistant__<name>
# ---------------------------------------------------------------------------

_PROMPTS = [
    Prompt(
        name="weekly_deals",
        description="Surface this week's best Raley's deals: clipped coupons + sale items + price history context",
        arguments=[
            PromptArgument(name="gi_only", description="'yes' to filter to low-GI items only", required=False),
        ],
    ),
    Prompt(
        name="t1d_meal_plan",
        description="Build a T1D-optimized weekly meal plan and shopping list based on current deals and user memory",
        arguments=[
            PromptArgument(name="budget", description="Weekly budget in dollars (e.g. '120')", required=False),
            PromptArgument(name="servings", description="Number of servings per meal (default 2)", required=False),
        ],
    ),
    Prompt(
        name="seasonal_now",
        description="What California produce is in season right now at Raley's, with GI ratings and meal ideas",
        arguments=[],
    ),
    Prompt(
        name="coupon_matchup",
        description="Cross-reference clipped coupons against order history — find stack deals and expiring offers",
        arguments=[],
    ),
]


@server.list_prompts()
async def list_prompts() -> list[Prompt]:
    return _PROMPTS


@server.get_prompt()
async def get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
    args = arguments or {}
    mem = load_memory()
    season = current_season()

    if name == "weekly_deals":
        gi_only = args.get("gi_only", "no").lower() in ("yes", "true", "1")
        gi_note = " Only include items with GI < 55." if gi_only else ""
        text = (
            f"Please help me find the best deals at Raley's this week.\n\n"
            f"1. First run `offers sync` to refresh coupons, then `offers clip_all` to clip everything.\n"
            f"2. Run `deals` (with gi_filter={str(gi_only).lower()}) to see this week's top values.\n"
            f"3. Cross-reference with my order history (`orders summary_only=true`) to prioritize items I buy regularly.\n"
            f"4. For the top 5-8 deals, check price history with `price` to confirm they're genuinely good prices.\n\n"
            f"Present results as a ranked list: item, sale price, savings %, GI category, clipped coupon status.{gi_note}\n\n"
            f"My T1D GI ceiling is {mem.t1d.gi_ceiling}. Flag anything above that."
        )

    elif name == "t1d_meal_plan":
        budget = args.get("budget", "")
        servings = args.get("servings", "2")
        budget_note = f" Target budget: ${budget}/week." if budget else ""
        t1d_note = (
            f"Carb target: {mem.t1d.carb_target_per_meal}g/meal. "
            f"GI ceiling: {mem.t1d.gi_ceiling}. "
            f"Avoid: {', '.join(mem.t1d.avoid_items) or 'none noted'}. "
            f"Favorite proteins: {', '.join(mem.t1d.favorite_proteins) or 'open'}."
        )
        text = (
            f"Please build a T1D-friendly weekly meal plan and Raley's shopping list "
            f"for {servings} servings per meal.{budget_note}\n\n"
            f"**My T1D config**: {t1d_note}\n\n"
            f"**Approach**:\n"
            f"1. Check `memory get` for my full profile and any notes on liked/disliked items.\n"
            f"2. Check `deals` to build meals around what's on sale this week.\n"
            f"3. For each meal: approx carb count, GI level, protein source, and whether it's seasonal ({season}).\n"
            f"4. Generate a shopping list grouped by store section (produce, proteins, dairy, pantry, frozen).\n"
            f"5. Use `plan` with the full list to get SKUs and prices.\n\n"
            f"Format: 7 dinners + 5 lunches + 7 breakfasts. Include 3-5 low-GI snack options.\n"
            f"Each recipe: name, carbs/serving, GI category, key ingredients, estimated cost."
        )

    elif name == "seasonal_now":
        season_produce = {
            "winter": "citrus (oranges, grapefruit, clementines), kale, Brussels sprouts, root vegetables, pomegranate, persimmon",
            "spring": "asparagus, artichokes, English peas, strawberries, cherries, spring onions, fava beans",
            "summer": "heirloom tomatoes, stone fruit (peaches, nectarines, plums), corn, zucchini, peppers, basil, berries, figs",
            "fall": "winter squash, apples, pears, grapes, Brussels sprouts, cauliflower, pomegranate, sweet potato",
        }
        text = (
            f"It's {season} in California. Here's what to look for at Raley's right now:\n\n"
            f"**In season**: {season_produce[season]}\n\n"
            f"Please:\n"
            f"1. Search for 4-5 of these items to confirm availability and current prices.\n"
            f"2. For each: GI rating, peak-season note, and a quick T1D-friendly meal idea.\n"
            f"3. Note which have the best unit pricing (freshest + cheapest per lb).\n"
            f"4. Suggest 2-3 complete meals built around what's in season + currently on sale.\n\n"
            f"My GI ceiling is {mem.t1d.gi_ceiling}. Flag anything above it and suggest a swap."
        )

    elif name == "coupon_matchup":
        text = (
            f"Please cross-reference my clipped coupons against my shopping history.\n\n"
            f"1. Run `offers sync` to refresh the coupon DB.\n"
            f"2. Run `offers list` to see available coupons — clip anything relevant.\n"
            f"3. Run `deals` to see sale items where I have a coupon (stacking discount).\n"
            f"4. Check `orders` to find what I buy regularly — look for coupon overlap.\n\n"
            f"Present: stack deals (sale + clipped coupon), items worth stocking up on, "
            f"and anything expiring soon I should use. "
            f"Include GI ratings for all items (ceiling: {mem.t1d.gi_ceiling})."
        )

    else:
        text = f"Unknown prompt: {name}"

    return GetPromptResult(
        description=next((p.description for p in _PROMPTS if p.name == name), ""),
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(type="text", text=text),
            )
        ],
    )


async def run_server():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="raley-assistant",
                server_version="0.3.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
                instructions=MCP_INSTRUCTIONS,
            ),
        )


def main():
    import asyncio

    asyncio.run(run_server())


if __name__ == "__main__":
    main()
