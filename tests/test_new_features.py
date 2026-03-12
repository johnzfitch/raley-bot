"""Tests for new features: cart improvements, search_batch, cart_diff,
price sanity, store mismatch, blacklist, and remove failure reasons.

All handlers are async. Since pytest-asyncio may not be available,
these tests use asyncio.run() to invoke async handlers from sync tests.
"""

import asyncio
import json
from unittest.mock import patch, MagicMock

from raley_assistant.api import Product
from raley_assistant.reasoning import check_price_sanity
from raley_assistant.db import check_store_mismatch


# ── Helpers ───────────────────────────────────────────────────────

def _fake_product(
    name="Test Milk", sku="SKU1", price_cents=499,
    brand="TestBrand", sale_cents=None, size="64oz",
    unit_oz=64.0,
):
    ppo = price_cents / 100 / unit_oz if unit_oz else None
    return Product(
        sku=sku, name=name, brand=brand,
        price_cents=price_cents, sale_price_cents=sale_cents,
        on_sale=sale_cents is not None, image_url=None,
        size=size, weight_lbs=None, unit_oz=unit_oz,
        price_per_oz=ppo,
    )


def _run(coro):
    """Run an async coroutine from a sync test."""
    return asyncio.run(coro)


# ── A. Cart returns total_units ───────────────────────────────────


@patch("raley_assistant.mcp_server.get_cart")
@patch("raley_assistant.mcp_server.get_api_client")
def test_cart_returns_total_units(mock_client, mock_cart):
    from raley_assistant.mcp_server import handle_cart

    mock_client.return_value = MagicMock()
    mock_cart.return_value = {
        "lineItems": [
            {"name": {"en-US": "Milk"}, "quantity": 2, "totalPrice": {"centAmount": 998},
             "variant": {"sku": "SKU1"}},
            {"name": {"en-US": "Bread"}, "quantity": 3, "totalPrice": {"centAmount": 897},
             "variant": {"sku": "SKU2"}},
        ],
        "totalPrice": {"centAmount": 1895},
    }

    result = json.loads(_run(handle_cart({})))

    assert result["count"] == 2           # unique line items
    assert result["total_units"] == 5     # 2 + 3
    assert result["total"] == "$18.95"


@patch("raley_assistant.mcp_server.get_cart")
@patch("raley_assistant.mcp_server.get_api_client")
def test_cart_summary_returns_total_units(mock_client, mock_cart):
    from raley_assistant.mcp_server import handle_cart

    mock_client.return_value = MagicMock()
    mock_cart.return_value = {
        "lineItems": [
            {"name": {"en-US": "Milk"}, "quantity": 2, "totalPrice": {"centAmount": 998},
             "variant": {"sku": "SKU1"}},
        ],
        "totalPrice": {"centAmount": 998},
    }

    result = json.loads(_run(handle_cart({"summary_only": True})))

    assert "total_units" in result
    assert result["total_units"] == 2
    assert result["items_count"] == 1


@patch("raley_assistant.mcp_server.get_cart")
@patch("raley_assistant.mcp_server.get_api_client")
def test_cart_empty_returns_zero_units(mock_client, mock_cart):
    from raley_assistant.mcp_server import handle_cart

    mock_client.return_value = MagicMock()
    mock_cart.return_value = {}

    result = json.loads(_run(handle_cart({"summary_only": True})))
    assert result["total_units"] == 0


# ── B. Add returns cart state ─────────────────────────────────────


@patch("raley_assistant.mcp_server.get_cart")
@patch("raley_assistant.mcp_server.get_products_by_sku")
@patch("raley_assistant.mcp_server.api_add_to_cart")
@patch("raley_assistant.mcp_server.get_api_client")
def test_add_returns_cart_state(mock_client, mock_add, mock_get_sku, mock_cart):
    from raley_assistant.mcp_server import handle_add

    mock_client.return_value = MagicMock()
    mock_add.return_value = True
    mock_get_sku.return_value = []
    mock_cart.return_value = {
        "lineItems": [
            {"quantity": 1, "variant": {"sku": "SKU1"}},
            {"quantity": 2, "variant": {"sku": "SKU2"}},
        ],
        "totalPrice": {"centAmount": 1500},
    }

    result = json.loads(_run(handle_add({"sku": "SKU1", "cents": 499})))

    assert result["ok"] is True
    assert result["cart_total"] == "$15.00"
    assert result["cart_items"] == 2
    assert result["cart_units"] == 3


# ── F. Remove returns failure reason ──────────────────────────────


@patch("raley_assistant.mcp_server.remove_from_cart")
@patch("raley_assistant.mcp_server.get_cart")
@patch("raley_assistant.mcp_server.get_api_client")
def test_remove_not_in_cart_returns_reason(mock_client, mock_cart, mock_remove):
    """SKU not in cart: remove_from_cart returns False, then we diagnose with get_cart."""
    from raley_assistant.mcp_server import handle_remove

    mock_client.return_value = MagicMock()
    mock_remove.return_value = False  # remove_from_cart already fetches cart internally
    # get_cart is called once by the failure-diagnosis path
    mock_cart.return_value = {
        "lineItems": [
            {"id": "line-1", "quantity": 1, "variant": {"sku": "OTHER_SKU"}},
        ],
        "totalPrice": {"centAmount": 499},
    }

    result = json.loads(_run(handle_remove({"sku": "NONEXISTENT"})))

    assert result["ok"] is False
    assert result["reason"] == "not_in_cart"
    assert result["cart_total"] == "$4.99"
    assert "cart_skus" in result
    assert "OTHER_SKU" in result["cart_skus"]


@patch("raley_assistant.mcp_server.remove_from_cart")
@patch("raley_assistant.mcp_server.get_cart")
@patch("raley_assistant.mcp_server.get_api_client")
def test_remove_empty_cart_returns_reason(mock_client, mock_cart, mock_remove):
    """Cart fetch fails during failure diagnosis."""
    from raley_assistant.mcp_server import handle_remove

    mock_client.return_value = MagicMock()
    mock_remove.return_value = False
    mock_cart.return_value = {}  # empty dict = failed cart fetch

    result = json.loads(_run(handle_remove({"sku": "SKU1"})))

    assert result["ok"] is False
    assert result["reason"] == "cart_fetch_failed"


@patch("raley_assistant.mcp_server.remove_from_cart")
@patch("raley_assistant.mcp_server.get_cart")
@patch("raley_assistant.mcp_server.get_api_client")
def test_remove_success_returns_cart_state(mock_client, mock_cart, mock_remove):
    """Success path: remove_from_cart returns True, then _cart_snapshot is called."""
    from raley_assistant.mcp_server import handle_remove

    mock_client.return_value = MagicMock()
    mock_remove.return_value = True
    # get_cart called once by _cart_snapshot after successful remove
    mock_cart.return_value = {
        "lineItems": [],
        "totalPrice": {"centAmount": 0},
    }

    result = json.loads(_run(handle_remove({"sku": "SKU1"})))

    assert result["ok"] is True
    assert result["cart_total"] == "$0.00"
    assert result["cart_items"] == 0


@patch("raley_assistant.mcp_server.remove_from_cart")
@patch("raley_assistant.mcp_server.get_cart")
@patch("raley_assistant.mcp_server.get_api_client")
def test_remove_api_error_returns_reason(mock_client, mock_cart, mock_remove):
    """Remove fails despite SKU being in cart (API error)."""
    from raley_assistant.mcp_server import handle_remove

    mock_client.return_value = MagicMock()
    mock_remove.return_value = False
    # Diagnosis: SKU IS in cart, so reason is api_error not not_in_cart
    mock_cart.return_value = {
        "lineItems": [{"id": "line-1", "quantity": 1, "variant": {"sku": "SKU1"}}],
        "totalPrice": {"centAmount": 499},
    }

    result = json.loads(_run(handle_remove({"sku": "SKU1"})))

    assert result["ok"] is False
    assert result["reason"] == "api_error"


# ── E. Price sanity checking ─────────────────────────────────────


def test_price_sanity_flags_cheap_chicken():
    warning = check_price_sanity("Chicken Breast 4pk", 18, 16.0)
    assert warning is not None
    assert "PRICE_DATA_SUSPECT" in warning


def test_price_sanity_passes_normal_chicken():
    # $7.99 for 2 lbs (32 oz) = $3.99/lb — reasonable
    warning = check_price_sanity("Chicken Breast 2lb", 799, 32.0)
    assert warning is None


def test_price_sanity_flags_cheap_beef():
    # $0.50 for 16 oz = $0.50/lb — impossibly cheap
    warning = check_price_sanity("Ground Beef 80/20", 50, 16.0)
    assert warning is not None
    assert "PRICE_DATA_SUSPECT" in warning


def test_price_sanity_ignores_non_meat():
    # $0.18/lb for bananas is fine
    warning = check_price_sanity("Organic Bananas", 18, 16.0)
    assert warning is None


def test_price_sanity_no_weight():
    warning = check_price_sanity("Mystery Product", 100, None)
    assert warning is None


# ── C. Store mismatch detection ──────────────────────────────────


def test_store_mismatch_detected():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE products (
            sku TEXT PRIMARY KEY, name TEXT, brand TEXT, price_cents INTEGER,
            sale_price_cents INTEGER, size TEXT, unit_oz REAL, price_per_oz REAL,
            last_seen TEXT, first_seen TEXT, store_id TEXT DEFAULT ''
        )
    """)
    # Insert 10 products from store "old-store"
    for i in range(10):
        conn.execute(
            "INSERT INTO products VALUES (?, ?, '', 100, NULL, '', NULL, NULL, '', '', ?)",
            (f"SKU{i}", f"Product {i}", "old-store"),
        )
    conn.commit()

    result = check_store_mismatch(conn, "new-store")
    assert result is not None
    assert result["warning"] == "store_changed"
    assert result["cached_store"] == "old-store"
    assert result["stale_products"] == 10
    conn.close()


def test_store_mismatch_not_detected_same_store():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE products (
            sku TEXT PRIMARY KEY, name TEXT, brand TEXT, price_cents INTEGER,
            sale_price_cents INTEGER, size TEXT, unit_oz REAL, price_per_oz REAL,
            last_seen TEXT, first_seen TEXT, store_id TEXT DEFAULT ''
        )
    """)
    for i in range(10):
        conn.execute(
            "INSERT INTO products VALUES (?, ?, '', 100, NULL, '', NULL, NULL, '', '', ?)",
            (f"SKU{i}", f"Product {i}", "same-store"),
        )
    conn.commit()

    result = check_store_mismatch(conn, "same-store")
    assert result is None
    conn.close()


def test_store_mismatch_empty_store_id():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE products (
            sku TEXT PRIMARY KEY, name TEXT, brand TEXT, price_cents INTEGER,
            sale_price_cents INTEGER, size TEXT, unit_oz REAL, price_per_oz REAL,
            last_seen TEXT, first_seen TEXT, store_id TEXT DEFAULT ''
        )
    """)

    result = check_store_mismatch(conn, "")
    assert result is None
    conn.close()


# ── D. search_batch ───────────────────────────────────────────────


@patch("raley_assistant.mcp_server.get_connection")
@patch("raley_assistant.mcp_server.search_products")
@patch("raley_assistant.mcp_server.get_api_client")
def test_search_batch_returns_per_query(mock_client, mock_search, mock_conn):
    from raley_assistant.mcp_server import handle_search_batch

    mock_client.return_value = MagicMock()
    mock_conn.return_value = MagicMock()

    mock_search.side_effect = [
        [_fake_product("Whole Milk", "M1", 499, "Clover")],
        [_fake_product("White Bread", "B1", 399, "Wonder")],
        [],  # not found
    ]

    result = json.loads(_run(handle_search_batch({
        "queries": ["milk", "bread", "unicorn"]
    })))

    assert result["found"] == 2
    assert result["not_found"] == 1
    assert len(result["results"]) == 3
    assert result["results"][0]["sku"] == "M1"
    assert result["results"][1]["sku"] == "B1"
    assert result["results"][2]["found"] is False


@patch("raley_assistant.mcp_server.get_api_client")
def test_search_batch_empty_queries(mock_client):
    from raley_assistant.mcp_server import handle_search_batch

    mock_client.return_value = MagicMock()

    result = json.loads(_run(handle_search_batch({"queries": []})))
    assert "error" in result


@patch("raley_assistant.mcp_server.get_connection")
@patch("raley_assistant.mcp_server.search_products")
@patch("raley_assistant.mcp_server.get_api_client")
def test_search_batch_caps_at_15(mock_client, mock_search, mock_conn):
    from raley_assistant.mcp_server import handle_search_batch

    mock_client.return_value = MagicMock()
    mock_conn.return_value = MagicMock()
    mock_search.return_value = [_fake_product("Item", "S1", 100)]

    queries = [f"item{i}" for i in range(20)]
    result = json.loads(_run(handle_search_batch({"queries": queries})))

    # Should only process 15
    assert len(result["results"]) == 15


# ── G. cart_diff ──────────────────────────────────────────────────


@patch("raley_assistant.mcp_server.get_cart")
@patch("raley_assistant.mcp_server.get_api_client")
def test_cart_diff_matches(mock_client, mock_cart):
    from raley_assistant.mcp_server import handle_cart_diff

    mock_client.return_value = MagicMock()
    mock_cart.return_value = {
        "lineItems": [
            {"name": {"en-US": "Milk"}, "quantity": 2, "variant": {"sku": "SKU1"}},
            {"name": {"en-US": "Bread"}, "quantity": 1, "variant": {"sku": "SKU2"}},
        ],
        "totalPrice": {"centAmount": 1200},
    }

    result = json.loads(_run(handle_cart_diff({
        "expected_skus": "SKU1:2,SKU2:1"
    })))

    assert result["status"] == "cart_matches"
    assert result["matched"] == 2


@patch("raley_assistant.mcp_server.get_cart")
@patch("raley_assistant.mcp_server.get_api_client")
def test_cart_diff_finds_missing(mock_client, mock_cart):
    from raley_assistant.mcp_server import handle_cart_diff

    mock_client.return_value = MagicMock()
    mock_cart.return_value = {
        "lineItems": [
            {"name": {"en-US": "Milk"}, "quantity": 1, "variant": {"sku": "SKU1"}},
        ],
        "totalPrice": {"centAmount": 499},
    }

    result = json.loads(_run(handle_cart_diff({
        "expected_skus": "SKU1:1,SKU2:1"
    })))

    assert result["status"] == "differences_found"
    assert len(result["missing"]) == 1
    assert result["missing"][0]["sku"] == "SKU2"


@patch("raley_assistant.mcp_server.get_cart")
@patch("raley_assistant.mcp_server.get_api_client")
def test_cart_diff_qty_mismatch(mock_client, mock_cart):
    from raley_assistant.mcp_server import handle_cart_diff

    mock_client.return_value = MagicMock()
    mock_cart.return_value = {
        "lineItems": [
            {"name": {"en-US": "Milk"}, "quantity": 3, "variant": {"sku": "SKU1"}},
        ],
        "totalPrice": {"centAmount": 1497},
    }

    result = json.loads(_run(handle_cart_diff({
        "expected_skus": "SKU1:2"
    })))

    assert result["status"] == "differences_found"
    assert len(result["qty_mismatch"]) == 1
    assert result["qty_mismatch"][0]["expected_qty"] == 2
    assert result["qty_mismatch"][0]["actual_qty"] == 3


@patch("raley_assistant.mcp_server.get_cart")
@patch("raley_assistant.mcp_server.get_api_client")
def test_cart_diff_shows_extra_items(mock_client, mock_cart):
    from raley_assistant.mcp_server import handle_cart_diff

    mock_client.return_value = MagicMock()
    mock_cart.return_value = {
        "lineItems": [
            {"name": {"en-US": "Milk"}, "quantity": 1, "variant": {"sku": "SKU1"}},
            {"name": {"en-US": "Surprise"}, "quantity": 1, "variant": {"sku": "SKU99"}},
        ],
        "totalPrice": {"centAmount": 998},
    }

    result = json.loads(_run(handle_cart_diff({
        "expected_skus": "SKU1:1"
    })))

    assert result["status"] == "cart_matches"  # no missing or mismatched
    assert len(result["extra"]) == 1
    assert result["extra"][0]["sku"] == "SKU99"


# ── I. Blacklist integration ─────────────────────────────────────


def test_check_blacklist_matches_sku():
    from raley_assistant.mcp_server import _check_blacklist
    from raley_assistant.memory import ShoppingMemory

    mem = ShoppingMemory(notes={"blacklist": "SKU123 bad product, SKU456 also bad"})
    with patch("raley_assistant.mcp_server.load_memory", return_value=mem):
        result = _check_blacklist("SKU123", "Some Product")
    assert result is not None
    assert "blacklist" in result.lower()


def test_check_blacklist_matches_name():
    from raley_assistant.mcp_server import _check_blacklist
    from raley_assistant.memory import ShoppingMemory

    mem = ShoppingMemory(notes={"blacklist_brands": "Terrible Brand products always bad"})
    with patch("raley_assistant.mcp_server.load_memory", return_value=mem):
        result = _check_blacklist("SKU999", "Terrible Brand Milk 64oz")
    assert result is not None


def test_check_blacklist_no_match():
    from raley_assistant.mcp_server import _check_blacklist
    from raley_assistant.memory import ShoppingMemory

    mem = ShoppingMemory(notes={"blacklist": "SKU123 bad product"})
    with patch("raley_assistant.mcp_server.load_memory", return_value=mem):
        result = _check_blacklist("SKU999", "Good Brand Milk")
    assert result is None


def test_check_blacklist_avoid_brands():
    from raley_assistant.mcp_server import _check_blacklist
    from raley_assistant.memory import ShoppingMemory, ShoppingConfig

    mem = ShoppingMemory(
        shopping=ShoppingConfig(avoid_brands=["BadCo"]),
        notes={},
    )
    with patch("raley_assistant.mcp_server.load_memory", return_value=mem):
        result = _check_blacklist("SKU1", "BadCo Premium Cheese")
    assert result is not None
    assert "avoid" in result.lower()


def test_check_blacklist_empty_memory():
    from raley_assistant.mcp_server import _check_blacklist
    from raley_assistant.memory import ShoppingMemory

    mem = ShoppingMemory()
    with patch("raley_assistant.mcp_server.load_memory", return_value=mem):
        result = _check_blacklist("SKU1", "Product Name")
    assert result is None


# ── B. add_plan returns cart state ────────────────────────────────


@patch("raley_assistant.mcp_server.get_cart")
@patch("raley_assistant.mcp_server.api_add_to_cart")
@patch("raley_assistant.mcp_server.get_api_client")
def test_add_plan_returns_cart_state(mock_client, mock_add, mock_cart):
    from raley_assistant.mcp_server import handle_add_plan

    mock_client.return_value = MagicMock()
    mock_add.return_value = True
    mock_cart.return_value = {
        "lineItems": [
            {"quantity": 1, "variant": {"sku": "SKU1"}},
            {"quantity": 2, "variant": {"sku": "SKU2"}},
        ],
        "totalPrice": {"centAmount": 1200},
    }

    result = json.loads(_run(handle_add_plan({"items": "SKU1:499,SKU2:299:2"})))

    assert result["ok"] is True
    assert result["cart_total"] == "$12.00"
    assert result["cart_items"] == 2
    assert result["cart_units"] == 3


# ── db.sync_products_from_search with store_id ───────────────────


def test_sync_products_stores_store_id():
    import sqlite3
    from raley_assistant.db import get_connection, sync_products_from_search, SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    products = [_fake_product("Milk", "SKU1", 499)]
    sync_products_from_search(conn, products, store_id="store-123")

    row = conn.execute("SELECT store_id FROM products WHERE sku = 'SKU1'").fetchone()
    assert row["store_id"] == "store-123"
    conn.close()
