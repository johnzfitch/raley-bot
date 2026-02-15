"""MCP Server for Raley Grocery Assistant — Consolidated Tools.

Named after Raley — someone who makes grocery day feel less like a chore.
"""

import json
import os
from pathlib import Path
from typing import Any
from datetime import datetime

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

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
)
from .reasoning import (
    evaluate_options,
    get_purchase_frequency,
    should_buy_this_trip,
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


def get_api_client():
    """Get authenticated API client."""
    if not COOKIES_PATH.exists():
        raise RuntimeError(
            f"Cookies not found at {COOKIES_PATH}. Run 'raley login' first."
        )
    return create_client(COOKIES_PATH)


def save_result_to_file(filename_prefix: str, data: dict) -> str:
    """Save result data to file with restricted permissions. Returns file path."""
    base_dir = Path.home() / ".local" / "share" / "raley-assistant"
    base_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filepath = base_dir / f"{filename_prefix}-{timestamp}.json"

    # Write with owner-only permissions
    fd = os.open(str(filepath), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)

    return str(filepath)


# ============================================================================
# TOOL DEFINITIONS
# ============================================================================

TOOLS = [
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
        description="Parse grocery list, find matches. Does NOT add to cart.",
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
        description="Price history by SKU, or search local DB.",
        inputSchema={
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "q": {"type": "string"},
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
        name="auth",
        description="Check authentication status (no cookie values exposed).",
        inputSchema={
            "type": "object",
            "properties": {},
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

    # Sync to local price DB (non-destructive)
    try:
        conn = get_connection()
        sync_products_from_search(conn, products)
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
        "cents": int(decision.price * 100),
    }

    if unit_pricing_dict and unit_pricing_dict.get("best"):
        best_metric = unit_pricing_dict["best"]
        result[best_metric] = unit_pricing_dict.get(best_metric, "")

    if decision.flags:
        if "SALE" in decision.flags:
            result["sale"] = True
        if "PRICE_WARNING" in decision.flags:
            result["high_price"] = True

    return json.dumps(result)


async def handle_add(args: dict) -> str:
    """Add to cart."""
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

    result = {"ok": success, "sku": sku, "qty": qty}
    if product_name:
        result["name"] = product_name

    return json.dumps(result)


async def handle_remove(args: dict) -> str:
    """Remove from cart."""
    client = get_api_client()
    success = remove_from_cart(client, args["sku"])
    return json.dumps({"ok": success, "sku": args["sku"]})


async def handle_cart(args: dict) -> str:
    """View cart."""
    client = get_api_client()
    cart = get_cart(client)
    limit = args.get("limit", 10)
    summary_only = args.get("summary_only", False)
    save_to_file_flag = args.get("save_to_file", False)

    if not cart:
        if summary_only:
            return json.dumps({"items_count": 0, "total": "$0.00"})
        return json.dumps({"items": [], "total": "$0.00", "count": 0})

    items = []
    for item in cart.get("lineItems", []):
        name = item.get("name", {})
        if isinstance(name, dict):
            name = name.get("en-US", "Unknown item")
        price = item.get("totalPrice", {}).get("centAmount", 0) / 100
        items.append(
            {
                "name": name[:40],
                "qty": item.get("quantity", 1),
                "price": f"${price:.2f}",
                "sku": item.get("variant", {}).get("sku", ""),
            }
        )

    total = cart.get("totalPrice", {}).get("centAmount", 0) / 100
    full_cart = {"items": items, "count": len(items), "total": f"${total:.2f}"}

    if save_to_file_flag:
        filepath = save_result_to_file("cart", full_cart)
        return json.dumps(
            {"file_saved": filepath, "items_count": len(items), "total": f"${total:.2f}"}
        )

    if summary_only:
        summary = {"items_count": len(items), "total": f"${total:.2f}"}
        if items:
            summary["top_items"] = [
                {"name": i["name"], "qty": i["qty"], "price": i["price"]}
                for i in items[:3]
            ]
        return json.dumps(summary)

    shown_items = items[:limit]
    response = {"items": shown_items, "count": len(shown_items), "total": f"${total:.2f}"}
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
        count = sync_coupons_from_api(conn, offers)
        conn.close()
        result = {"synced": count}
        if len(offers) >= 500:
            result["note"] = "Hit 500 limit, may have more"
        return json.dumps(result)

    # Default: list
    limit = args.get("limit", 10)
    offers = get_offers(client, category=args.get("cat"), clipped="Unclipped", rows=100)

    results = [
        {
            "id": o.id,
            "headline": o.headline[:50],
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
            last_date = get_last_purchase_date(conn, decision.sku)
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
            "match": decision.product_name[:40],
            "sku": decision.sku,
            "unit": f"${decision.price:.2f}",
            "line": f"${line_total:.2f}",
            "cents": int(decision.price * 100),
        }

        if recently_bought:
            r["recently_bought"] = True
            r["buy_note"] = buy_note

        if decision.flags:
            r["flags"] = decision.flags

        if freq in (PurchaseFrequency.MONTHLY, PurchaseFrequency.QUARTERLY):
            r["confirm"] = freq.name.lower()
            confirm_needed.append(item_name)

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

    if args.get("q"):
        results = search_products_local(conn, args["q"], 10)
        conn.close()
        if not results:
            return json.dumps({"error": "No local results"})
        return json.dumps(
            {
                "products": [
                    {
                        "sku": r["sku"],
                        "name": r["name"][:40],
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
            conn.close()
            return json.dumps({"error": "No history", "sku": args["sku"]})

        is_deal, reason = is_good_deal(conn, args["sku"], record.current_price)
        conn.close()

        return json.dumps(
            {
                "sku": record.sku,
                "name": record.name[:40],
                "current": f"${record.current_price/100:.2f}",
                "avg": f"${record.avg_price/100:.2f}",
                "min": f"${record.min_price/100:.2f}",
                "max": f"${record.max_price/100:.2f}",
                "deal": is_deal,
                "analysis": reason,
            }
        )

    stats = get_price_stats(conn)
    conn.close()
    return json.dumps(stats)


async def handle_orders(args: dict) -> str:
    """Get order history."""
    client = get_api_client()
    days_back = args.get("days", 90)
    limit = min(args.get("limit", 10), 30)
    summary_only = args.get("summary_only", False)
    save_to_file_flag = args.get("save_to_file", False)

    all_orders = get_orders(client, days_back=days_back, limit=30)

    # Sync order items to local DB for purchase frequency tracking
    try:
        conn = get_connection()
        sync_order_items(conn, all_orders)
        conn.close()
    except Exception:
        pass

    results = []
    total_spent = 0.0
    for o in all_orders:
        total_price = o.get("totalPrice", 0)
        if isinstance(total_price, dict):
            total_price = total_price.get("centAmount", 0)

        total_spent += total_price / 100

        results.append(
            {
                "id": str(o.get("orderId", ""))[-8:],
                "date": str(o.get("createdDate", ""))[:10],
                "status": o.get("orderStatus", {}).get("value", ""),
                "total": f"${total_price/100:.2f}",
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


TOOL_HANDLERS = {
    "search": handle_search,
    "add": handle_add,
    "remove": handle_remove,
    "cart": handle_cart,
    "offers": handle_offers,
    "plan": handle_build_list,
    "price": handle_price_check,
    "orders": handle_orders,
    "auth": handle_auth,
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
        msg = str(e)[:MAX_ERROR_LENGTH] if str(e) else "Unknown error"
        return [TextContent(type="text", text=json.dumps({"error": msg}))]


async def run_server():
    async with stdio_server() as (read_stream, write_stream):
        init_options = server.create_initialization_options()
        await server.run(read_stream, write_stream, init_options)


def main():
    import asyncio

    asyncio.run(run_server())


if __name__ == "__main__":
    main()
