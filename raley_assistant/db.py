"""SQLite price history and coupon tracking for Raley.

Maintains a local database of product prices over time so the reasoning
engine can detect deals, track price trends, and make purchase frequency
recommendations.

Schema:
    products     — current product catalog snapshot
    price_history — append-only price observations
    coupons      — synced coupon/offer state
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_DIR = Path.home() / ".local" / "share" / "raley-assistant"
DB_PATH = DB_DIR / "raley.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    brand TEXT DEFAULT '',
    price_cents INTEGER NOT NULL,
    sale_price_cents INTEGER,
    size TEXT DEFAULT '',
    unit_oz REAL,
    price_per_oz REAL,
    last_seen TEXT NOT NULL,
    first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    sale_price_cents INTEGER,
    observed_at TEXT NOT NULL,
    FOREIGN KEY (sku) REFERENCES products(sku)
);

CREATE INDEX IF NOT EXISTS idx_price_history_sku ON price_history(sku);
CREATE INDEX IF NOT EXISTS idx_price_history_observed ON price_history(observed_at);

CREATE TABLE IF NOT EXISTS coupons (
    offer_id TEXT PRIMARY KEY,
    code TEXT DEFAULT '',
    headline TEXT NOT NULL,
    description TEXT DEFAULT '',
    category TEXT DEFAULT '',
    discount_amount REAL DEFAULT 0,
    end_date TEXT DEFAULT '',
    is_clipped INTEGER DEFAULT 0,
    offer_type TEXT DEFAULT '',
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_coupons_clipped ON coupons(is_clipped);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL,
    product_name TEXT DEFAULT '',
    purchased_at TEXT NOT NULL,
    order_id TEXT DEFAULT '',
    UNIQUE(sku, purchased_at)
);

CREATE INDEX IF NOT EXISTS idx_order_items_sku ON order_items(sku);
CREATE INDEX IF NOT EXISTS idx_order_items_purchased ON order_items(purchased_at);
"""


@dataclass
class ProductHistory:
    """Product with aggregated price history."""

    sku: str
    name: str
    brand: str
    current_price: int  # cents
    avg_price: float  # cents
    min_price: int  # cents
    max_price: int  # cents
    observations: int
    first_seen: str
    last_seen: str


def get_connection() -> sqlite3.Connection:
    """Get database connection, creating schema if needed."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def sync_products_from_search(conn: sqlite3.Connection, products: list) -> int:
    """Sync Product objects from a search into the local DB.

    Upserts product records and appends price history observations.
    Returns number of products synced.
    """
    now = datetime.now(timezone.utc).isoformat()
    count = 0

    for p in products:
        effective_price = p.sale_price_cents or p.price_cents

        # Upsert product
        conn.execute(
            """
            INSERT INTO products (sku, name, brand, price_cents, sale_price_cents,
                                  size, unit_oz, price_per_oz, last_seen, first_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sku) DO UPDATE SET
                name = excluded.name,
                brand = excluded.brand,
                price_cents = excluded.price_cents,
                sale_price_cents = excluded.sale_price_cents,
                size = excluded.size,
                unit_oz = excluded.unit_oz,
                price_per_oz = excluded.price_per_oz,
                last_seen = excluded.last_seen
            """,
            (
                p.sku,
                p.name,
                p.brand,
                p.price_cents,
                p.sale_price_cents,
                p.size,
                p.unit_oz,
                p.price_per_oz,
                now,
                now,
            ),
        )

        # Append price observation (skip if same price recorded in last hour)
        last = conn.execute(
            """
            SELECT price_cents, sale_price_cents FROM price_history
            WHERE sku = ? ORDER BY observed_at DESC LIMIT 1
            """,
            (p.sku,),
        ).fetchone()

        if not last or last["price_cents"] != p.price_cents or last["sale_price_cents"] != p.sale_price_cents:
            conn.execute(
                """
                INSERT INTO price_history (sku, price_cents, sale_price_cents, observed_at)
                VALUES (?, ?, ?, ?)
                """,
                (p.sku, p.price_cents, p.sale_price_cents, now),
            )

        count += 1

    conn.commit()
    return count


def sync_coupons_from_api(conn: sqlite3.Connection, offers: list) -> int:
    """Sync Offer objects from the API into the local DB.

    Returns number of coupons synced.
    """
    now = datetime.now(timezone.utc).isoformat()
    count = 0

    for o in offers:
        conn.execute(
            """
            INSERT INTO coupons (offer_id, code, headline, description, category,
                                 discount_amount, end_date, is_clipped, offer_type, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(offer_id) DO UPDATE SET
                headline = excluded.headline,
                description = excluded.description,
                discount_amount = excluded.discount_amount,
                is_clipped = excluded.is_clipped,
                synced_at = excluded.synced_at
            """,
            (
                o.id,
                o.code,
                o.headline,
                o.description,
                o.category,
                o.discount_amount,
                o.end_date,
                1 if o.is_clipped else 0,
                o.offer_type,
                now,
            ),
        )
        count += 1

    conn.commit()
    return count


def sync_order_items(conn: sqlite3.Connection, orders: list[dict]) -> int:
    """Extract and store (sku, product_name, date) from order history.

    Expects order dicts from api.get_orders() which have:
      - createdDate: ISO date string
      - orderId: order identifier
      - lineItems: list of items, each with variant.sku and name

    Returns number of items synced.
    """
    count = 0
    for order in orders:
        order_date = order.get("createdDate", "")[:10]
        order_id = str(order.get("orderId", ""))

        if not order_date:
            continue

        for item in order.get("lineItems", []):
            variant = item.get("variant", {})
            sku = variant.get("sku", "")
            if not sku:
                continue

            name = item.get("name", "")
            if isinstance(name, dict):
                name = name.get("en-US", "")

            conn.execute(
                """
                INSERT OR IGNORE INTO order_items (sku, product_name, purchased_at, order_id)
                VALUES (?, ?, ?, ?)
                """,
                (sku, name, order_date, order_id),
            )
            count += 1

    conn.commit()
    return count


def get_last_purchase_date(conn: sqlite3.Connection, sku: str) -> str | None:
    """Get the most recent purchase date for a SKU.

    Returns ISO date string or None if never purchased.
    """
    row = conn.execute(
        "SELECT purchased_at FROM order_items WHERE sku = ? ORDER BY purchased_at DESC LIMIT 1",
        (sku,),
    ).fetchone()
    return row["purchased_at"] if row else None


def get_product_with_history(
    conn: sqlite3.Connection, sku: str
) -> Optional[ProductHistory]:
    """Get product with aggregated price statistics."""
    product = conn.execute(
        "SELECT * FROM products WHERE sku = ?", (sku,)
    ).fetchone()

    if not product:
        return None

    stats = conn.execute(
        """
        SELECT
            COUNT(*) as observations,
            AVG(COALESCE(sale_price_cents, price_cents)) as avg_price,
            MIN(COALESCE(sale_price_cents, price_cents)) as min_price,
            MAX(COALESCE(sale_price_cents, price_cents)) as max_price
        FROM price_history
        WHERE sku = ?
        """,
        (sku,),
    ).fetchone()

    effective_price = product["sale_price_cents"] or product["price_cents"]

    return ProductHistory(
        sku=sku,
        name=product["name"],
        brand=product["brand"] or "",
        current_price=effective_price,
        avg_price=stats["avg_price"] or effective_price,
        min_price=stats["min_price"] or effective_price,
        max_price=stats["max_price"] or effective_price,
        observations=stats["observations"] or 1,
        first_seen=product["first_seen"],
        last_seen=product["last_seen"],
    )


def is_good_deal(
    conn: sqlite3.Connection, sku: str, current_price_cents: int
) -> tuple[bool, str]:
    """Determine if the current price is a good deal based on history.

    Returns (is_deal, reason_string).

    Thresholds:
        - >=15% below average → good deal
        - At or near historical minimum → great deal
        - >=10% above average → price warning
    """
    stats = conn.execute(
        """
        SELECT
            AVG(COALESCE(sale_price_cents, price_cents)) as avg_price,
            MIN(COALESCE(sale_price_cents, price_cents)) as min_price,
            MAX(COALESCE(sale_price_cents, price_cents)) as max_price,
            COUNT(*) as observations
        FROM price_history
        WHERE sku = ?
        """,
        (sku,),
    ).fetchone()

    if not stats or stats["observations"] < 2:
        return False, "Not enough price history to evaluate"

    avg = stats["avg_price"]
    min_price = stats["min_price"]
    max_price = stats["max_price"]

    if avg == 0:
        return False, "No valid price data"

    pct_vs_avg = ((current_price_cents - avg) / avg) * 100

    # At or near historical minimum (within 5%)
    if min_price > 0 and current_price_cents <= min_price * 1.05:
        return True, f"Near historical low (${min_price/100:.2f}). {abs(pct_vs_avg):.0f}% below avg."

    # Significantly below average
    if pct_vs_avg <= -15:
        return True, f"{abs(pct_vs_avg):.0f}% below avg (${avg/100:.2f}). Good deal."

    # Moderately below average
    if pct_vs_avg <= -5:
        return True, f"{abs(pct_vs_avg):.0f}% below avg. Decent price."

    # Above average
    if pct_vs_avg >= 10:
        return False, f"{pct_vs_avg:.0f}% above avg (${avg/100:.2f}). Consider waiting."

    return False, f"Near average price (${avg/100:.2f})."


def search_products_local(
    conn: sqlite3.Connection, query: str, limit: int = 10
) -> list[dict]:
    """Search the local product database by name.

    Uses SQLite LIKE for simple substring matching. Returns list of dicts.
    """
    rows = conn.execute(
        """
        SELECT sku, name, brand, price_cents, sale_price_cents, last_seen
        FROM products
        WHERE name LIKE ? OR brand LIKE ?
        ORDER BY last_seen DESC
        LIMIT ?
        """,
        (f"%{query}%", f"%{query}%", limit),
    ).fetchall()

    return [
        {
            "sku": r["sku"],
            "name": r["name"],
            "brand": r["brand"],
            "price_cents": r["sale_price_cents"] or r["price_cents"],
            "last_seen": r["last_seen"],
        }
        for r in rows
    ]


def get_price_stats(conn: sqlite3.Connection) -> dict:
    """Get overall database statistics."""
    products_count = conn.execute("SELECT COUNT(*) as c FROM products").fetchone()["c"]
    history_count = conn.execute(
        "SELECT COUNT(*) as c FROM price_history"
    ).fetchone()["c"]
    coupons_count = conn.execute("SELECT COUNT(*) as c FROM coupons").fetchone()["c"]
    clipped_count = conn.execute(
        "SELECT COUNT(*) as c FROM coupons WHERE is_clipped = 1"
    ).fetchone()["c"]

    # Date range
    date_range = conn.execute(
        "SELECT MIN(observed_at) as first, MAX(observed_at) as last FROM price_history"
    ).fetchone()

    return {
        "products_tracked": products_count,
        "price_observations": history_count,
        "coupons_synced": coupons_count,
        "coupons_clipped": clipped_count,
        "tracking_since": date_range["first"] if date_range else None,
        "last_observation": date_range["last"] if date_range else None,
    }


def get_price_trend(conn: sqlite3.Connection, sku: str, days: int = 30) -> list[dict]:
    """Get price trend for a product over the last N days.

    Returns list of {date, price_cents} observations, chronological.
    """
    rows = conn.execute(
        """
        SELECT observed_at, COALESCE(sale_price_cents, price_cents) as price
        FROM price_history
        WHERE sku = ?
          AND observed_at >= datetime('now', ?)
        ORDER BY observed_at ASC
        """,
        (sku, f"-{days} days"),
    ).fetchall()

    return [{"date": r["observed_at"][:10], "price_cents": r["price"]} for r in rows]


def sync_previously_purchased(conn: sqlite3.Connection, products: list) -> int:
    """Sync products from the 'previously purchased' API into order_items.

    Since the orders API doesn't return line items, we use the previously
    purchased search filter to build purchase history. Each sync increments
    a seen count and updates last_seen date.

    Returns number of products synced.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = 0

    for p in products:
        # Use INSERT OR REPLACE to track each product
        # We store today's date as purchased_at since we don't know actual dates
        conn.execute(
            """
            INSERT INTO order_items (sku, product_name, purchased_at, order_id)
            VALUES (?, ?, ?, 'previously_purchased')
            ON CONFLICT(sku, purchased_at) DO UPDATE SET
                product_name = excluded.product_name
            """,
            (p.sku, p.name, now),
        )
        count += 1

    conn.commit()
    return count


def get_favorite_products(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Get most frequently purchased products.

    Returns products ordered by number of times seen in purchase history.
    """
    rows = conn.execute(
        """
        SELECT
            oi.sku,
            oi.product_name,
            p.brand,
            COUNT(*) as purchase_count,
            MAX(oi.purchased_at) as last_purchased,
            p.price_cents,
            p.sale_price_cents
        FROM order_items oi
        LEFT JOIN products p ON oi.sku = p.sku
        GROUP BY oi.sku
        ORDER BY purchase_count DESC, last_purchased DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    return [
        {
            "sku": r["sku"],
            "name": r["product_name"],
            "brand": r["brand"] or "",
            "purchase_count": r["purchase_count"],
            "last_purchased": r["last_purchased"],
            "current_price": r["sale_price_cents"] or r["price_cents"],
        }
        for r in rows
    ]


def get_favorite_brands(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Get most frequently purchased brands.

    Returns brands ordered by number of distinct products purchased.
    """
    rows = conn.execute(
        """
        SELECT
            p.brand,
            COUNT(DISTINCT oi.sku) as product_count,
            COUNT(*) as total_purchases
        FROM order_items oi
        JOIN products p ON oi.sku = p.sku
        WHERE p.brand != '' AND p.brand IS NOT NULL
        GROUP BY p.brand
        ORDER BY product_count DESC, total_purchases DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    return [
        {
            "brand": r["brand"],
            "products": r["product_count"],
            "purchases": r["total_purchases"],
        }
        for r in rows
    ]


def get_purchase_stats(conn: sqlite3.Connection) -> dict:
    """Get overall purchase history statistics."""
    total_products = conn.execute(
        "SELECT COUNT(DISTINCT sku) as c FROM order_items"
    ).fetchone()["c"]

    total_purchases = conn.execute(
        "SELECT COUNT(*) as c FROM order_items"
    ).fetchone()["c"]

    date_range = conn.execute(
        "SELECT MIN(purchased_at) as first, MAX(purchased_at) as last FROM order_items"
    ).fetchone()

    return {
        "unique_products": total_products,
        "total_purchase_records": total_purchases,
        "tracking_since": date_range["first"] if date_range else None,
        "last_sync": date_range["last"] if date_range else None,
    }
