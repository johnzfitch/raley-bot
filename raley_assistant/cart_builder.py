"""High-level cart building for agent use.

Provides the quick_add() one-liner and build_cart_from_list() for
programmatic grocery list processing.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from .api import (
    CurlClient,
    load_cookies,
    search_products,
    BASE_URL,
)

# Canonical cookies location (matches auth.py and mcp_server.py)
DEFAULT_COOKIES = Path.home() / ".config" / "raley-assistant" / "cookies.json"


@dataclass
class CartItem:
    """Simplified cart item for display."""

    name: str
    sku: str
    qty: int
    unit_price: float
    total: float


def get_client(cookies_path: Path | str | None = None) -> CurlClient:
    """Get client with default or specified cookies."""
    path = cookies_path or DEFAULT_COOKIES
    return CurlClient(load_cookies(path))


def parse_grocery_list(text: str) -> list[tuple[str, int]]:
    """Parse freeform grocery list into (item, quantity) pairs.

    Examples:
        "5 sweet potatoes" -> [("sweet potatoes", 5)]
        "milk" -> [("milk", 1)]
        "2 packs ground chicken" -> [("ground chicken", 2)]
    """
    items = []
    lines = re.split(r"[,\n]", text)

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Try to extract quantity at start
        match = re.match(
            r"^(\d+)\s*(?:x|pack[s]?|bag[s]?|bunch(?:es)?|each)?\s*(.+)", line, re.I
        )
        if match:
            qty = int(match.group(1))
            item = match.group(2).strip()
        else:
            # Check for quantity at end like "ground chicken - 2"
            match = re.match(
                r"^(.+?)\s*[-–]\s*(\d+)\s*(?:lb|lbs|oz|pack|packs)?$", line, re.I
            )
            if match:
                item = match.group(1).strip()
                qty = int(match.group(2))
            else:
                qty = 1
                item = line

        item = re.sub(r"\s+", " ", item)
        items.append((item, qty))

    return items


def find_best_product(
    client: CurlClient,
    query: str,
    max_results: int = 5,
    prefer_value: bool = True,
) -> list[dict]:
    """Search and return simplified product options, sorted by value.

    Returns list of {name, sku, price, brand, oz, price_per_oz} dicts.
    """
    products = search_products(client, query, limit=max_results)

    results = []
    for p in products:
        price = (p.sale_price_cents or p.price_cents) / 100
        results.append(
            {
                "name": p.name,
                "sku": p.sku,
                "price": price,
                "brand": p.brand,
                "on_sale": p.sale_price_cents is not None,
                "oz": p.unit_oz,
                "price_per_oz": p.price_per_oz,
            }
        )

    if prefer_value:
        with_pricing = [r for r in results if r["price_per_oz"]]
        without_pricing = [r for r in results if not r["price_per_oz"]]
        with_pricing.sort(key=lambda x: x["price_per_oz"])
        results = with_pricing + without_pricing

    return results


def add_to_cart(client: CurlClient, sku: str, qty: int, price_cents: int) -> bool:
    """Add single item to cart. Returns True on success."""
    cart_item = [
        {
            "quantity": qty,
            "sku": sku,
            "fields": [
                {"name": "unitSellType", "value": "byEach"},
                {
                    "name": "regularPrice",
                    "value": {
                        "type": "centPrecision",
                        "currencyCode": "USD",
                        "centAmount": price_cents,
                        "fractionDigits": 2,
                    },
                },
            ],
        }
    ]

    status, _ = client.post(f"{BASE_URL}/api/cart/item/add", json_body=cart_item)
    return status == 200


def build_cart_from_list(
    grocery_list: str,
    auto_add: bool = False,
    cookies_path: Path | str | None = None,
) -> list[CartItem]:
    """Parse grocery list, find best products, optionally add to cart."""
    client = get_client(cookies_path)
    items = parse_grocery_list(grocery_list)

    cart = []

    for item_name, qty in items:
        products = find_best_product(client, item_name, max_results=1)

        if not products:
            continue

        best = products[0]
        price_cents = round(best["price"] * 100)

        if auto_add:
            add_to_cart(client, best["sku"], qty, price_cents)

        cart.append(
            CartItem(
                name=best["name"],
                sku=best["sku"],
                qty=qty,
                unit_price=best["price"],
                total=best["price"] * qty,
            )
        )

    return cart


def cart_summary(cart: list[CartItem]) -> str:
    """Generate concise cart summary string."""
    lines = []
    total = 0

    for item in cart:
        lines.append(f"{item.qty}x {item.name[:35]} ${item.total:.2f}")
        total += item.total

    lines.append(f"TOTAL: ${total:.2f}")
    return "\n".join(lines)


def quick_add(grocery_list: str) -> str:
    """Parse list, add to cart, return summary. One function for agents."""
    cart = build_cart_from_list(grocery_list, auto_add=True)
    return cart_summary(cart)
