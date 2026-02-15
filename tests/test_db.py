"""Tests for raley_assistant.db — SQLite price tracking."""

import sqlite3
from dataclasses import dataclass
from raley_assistant.db import (
    SCHEMA,
    sync_products_from_search,
    sync_order_items,
    get_last_purchase_date,
    is_good_deal,
    get_product_with_history,
    search_products_local,
    get_price_stats,
)


def _mem_conn() -> sqlite3.Connection:
    """Create in-memory DB with schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@dataclass
class FakeProduct:
    sku: str; name: str; brand: str; price_cents: int
    sale_price_cents: int | None; size: str
    unit_oz: float | None; price_per_oz: float | None


def _prod(sku="SKU1", name="Test Product", price=499, sale=None):
    return FakeProduct(
        sku=sku, name=name, brand="TestBrand", price_cents=price,
        sale_price_cents=sale, size="16oz", unit_oz=16.0,
        price_per_oz=price / 100 / 16 if price else None,
    )


# ── sync_products_from_search ───────────────────────────────────────

def test_sync_inserts_new_products():
    conn = _mem_conn()
    count = sync_products_from_search(conn, [_prod(), _prod("SKU2", "Other")])
    assert count == 2
    rows = conn.execute("SELECT COUNT(*) as c FROM products").fetchone()
    assert rows["c"] == 2


def test_sync_upserts_existing():
    conn = _mem_conn()
    sync_products_from_search(conn, [_prod(price=499)])
    sync_products_from_search(conn, [_prod(price=399)])
    row = conn.execute("SELECT price_cents FROM products WHERE sku='SKU1'").fetchone()
    assert row["price_cents"] == 399


def test_sync_appends_price_history():
    conn = _mem_conn()
    sync_products_from_search(conn, [_prod(price=499)])
    sync_products_from_search(conn, [_prod(price=399)])
    rows = conn.execute("SELECT COUNT(*) as c FROM price_history WHERE sku='SKU1'").fetchone()
    assert rows["c"] == 2


def test_sync_skips_duplicate_price():
    conn = _mem_conn()
    sync_products_from_search(conn, [_prod(price=499)])
    sync_products_from_search(conn, [_prod(price=499)])
    rows = conn.execute("SELECT COUNT(*) as c FROM price_history WHERE sku='SKU1'").fetchone()
    assert rows["c"] == 1  # Same price → no new observation


# ── is_good_deal ────────────────────────────────────────────────────

def test_good_deal_not_enough_history():
    conn = _mem_conn()
    sync_products_from_search(conn, [_prod(price=499)])
    deal, reason = is_good_deal(conn, "SKU1", 399)
    assert deal is False
    assert "Not enough" in reason


def test_good_deal_below_avg():
    conn = _mem_conn()
    # Populate history with varying prices
    for price in [600, 550, 500, 450]:
        conn.execute(
            "INSERT INTO price_history (sku, price_cents, observed_at) VALUES (?, ?, datetime('now'))",
            ("SKU1", price),
        )
    conn.commit()
    # avg = 525, 15% below = 446.25
    deal, reason = is_good_deal(conn, "SKU1", 400)
    assert deal is True
    assert "below avg" in reason.lower()


def test_not_a_deal_above_avg():
    conn = _mem_conn()
    for price in [400, 450, 500]:
        conn.execute(
            "INSERT INTO price_history (sku, price_cents, observed_at) VALUES (?, ?, datetime('now'))",
            ("SKU1", price),
        )
    conn.commit()
    # avg = 450, 10% above = 495
    deal, reason = is_good_deal(conn, "SKU1", 550)
    assert deal is False
    assert "above avg" in reason.lower()


# ── get_product_with_history ────────────────────────────────────────

def test_product_with_history_returns_none():
    conn = _mem_conn()
    assert get_product_with_history(conn, "NONEXISTENT") is None


def test_product_with_history():
    conn = _mem_conn()
    sync_products_from_search(conn, [_prod(price=499)])
    h = get_product_with_history(conn, "SKU1")
    assert h is not None
    assert h.name == "Test Product"
    assert h.observations >= 1


# ── search_products_local ───────────────────────────────────────────

def test_local_search():
    conn = _mem_conn()
    sync_products_from_search(conn, [
        _prod("A1", "Organic Whole Milk"),
        _prod("A2", "Regular Skim Milk"),
        _prod("A3", "Orange Juice"),
    ])
    results = search_products_local(conn, "milk")
    assert len(results) == 2
    names = {r["name"] for r in results}
    assert "Orange Juice" not in names


# ── get_price_stats ─────────────────────────────────────────────────

def test_price_stats():
    conn = _mem_conn()
    sync_products_from_search(conn, [_prod(), _prod("SKU2")])
    stats = get_price_stats(conn)
    assert stats["products_tracked"] == 2
    assert stats["price_observations"] >= 2


# ── sync_order_items ──────────────────────────────────────────────

def test_sync_order_items():
    conn = _mem_conn()
    orders = [
        {
            "createdDate": "2024-06-15T10:30:00Z",
            "orderId": "order-123",
            "lineItems": [
                {"variant": {"sku": "SKU1"}, "name": {"en-US": "Whole Milk"}},
                {"variant": {"sku": "SKU2"}, "name": {"en-US": "Bread"}},
            ],
        }
    ]
    count = sync_order_items(conn, orders)
    assert count == 2
    rows = conn.execute("SELECT COUNT(*) as c FROM order_items").fetchone()
    assert rows["c"] == 2


def test_sync_order_items_dedup():
    conn = _mem_conn()
    orders = [
        {
            "createdDate": "2024-06-15T10:30:00Z",
            "orderId": "order-123",
            "lineItems": [
                {"variant": {"sku": "SKU1"}, "name": "Milk"},
            ],
        }
    ]
    sync_order_items(conn, orders)
    sync_order_items(conn, orders)  # Same data again
    rows = conn.execute("SELECT COUNT(*) as c FROM order_items").fetchone()
    assert rows["c"] == 1


def test_get_last_purchase_date_found():
    conn = _mem_conn()
    orders = [
        {
            "createdDate": "2024-06-01T00:00:00Z",
            "orderId": "o1",
            "lineItems": [{"variant": {"sku": "SKU1"}, "name": "Milk"}],
        },
        {
            "createdDate": "2024-06-15T00:00:00Z",
            "orderId": "o2",
            "lineItems": [{"variant": {"sku": "SKU1"}, "name": "Milk"}],
        },
    ]
    sync_order_items(conn, orders)
    last = get_last_purchase_date(conn, "SKU1")
    assert last == "2024-06-15"


def test_get_last_purchase_date_not_found():
    conn = _mem_conn()
    last = get_last_purchase_date(conn, "NONEXISTENT")
    assert last is None


def test_sync_order_items_skips_missing_sku():
    conn = _mem_conn()
    orders = [
        {
            "createdDate": "2024-06-15T00:00:00Z",
            "orderId": "o1",
            "lineItems": [
                {"variant": {}, "name": "No SKU Item"},
                {"variant": {"sku": "SKU1"}, "name": "Good Item"},
            ],
        }
    ]
    sync_order_items(conn, orders)
    rows = conn.execute("SELECT COUNT(*) as c FROM order_items").fetchone()
    assert rows["c"] == 1
