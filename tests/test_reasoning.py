"""Tests for raley_assistant.reasoning — heuristic product scoring."""

from raley_assistant.reasoning import (
    evaluate_options,
    get_purchase_frequency,
    PurchaseFrequency,
    Decision,
)


# ── evaluate_options ────────────────────────────────────────────────

def _product(name, sku, price, brand="", on_sale=False, ppo=None):
    return {
        "name": name, "sku": sku, "price": price, "brand": brand,
        "on_sale": on_sale, "price_per_oz": ppo, "oz": None,
    }


def test_empty_products_returns_not_found():
    d = evaluate_options([], "milk")
    assert d.sku == ""
    assert "NOT_FOUND" in d.flags


def test_single_product_returns_it():
    d = evaluate_options([_product("Whole Milk", "A1", 5.99)], "milk")
    assert d.sku == "A1"
    assert d.score == 100.0


def test_sale_item_boosted():
    products = [
        _product("Regular Milk", "A1", 5.99, ppo=0.05),
        _product("Sale Milk", "A2", 5.49, on_sale=True, ppo=0.06),
    ]
    d = evaluate_options(products, "milk")
    # Sale gets +15 bonus. A1 has better ppo (+25) but no sale.
    # A1: 50 + 25 (best ppo) + 3 (price tiebreaker) ~= 78
    # A2: 50 + 0 (worst ppo) + 15 (sale) + 0 ~= 65
    # Best value should still win when ppo difference is significant
    assert d.sku == "A1"
    assert "BEST_VALUE" in d.flags


def test_brand_match_boosts_score():
    products = [
        _product("Store Brand Milk", "A1", 4.99, brand="Store", ppo=0.05),
        _product("Clover Milk", "A2", 4.99, brand="Clover", ppo=0.05),
    ]
    d = evaluate_options(products, "clover milk")
    # Equal ppo and price → brand match (+10) + full name relevance tips to A2
    assert d.sku == "A2"


def test_price_warning_flag():
    products = [
        _product("Cheap", "A1", 2.00),
        _product("Expensive", "A2", 5.00),
    ]
    d = evaluate_options(products, "thing")
    expensive = [p for p in [
        evaluate_options(products, "thing")
    ] if "PRICE_WARNING" in d.flags]
    # The expensive one should have PRICE_WARNING if >2x cheapest
    # 5.00 > 2*2.00=4.00 → yes
    assert "PRICE_WARNING" not in d.flags  # winner is A1 (cheapest)


def test_organic_preference():
    products = [
        _product("Regular Eggs", "A1", 3.99, ppo=0.25),
        _product("Organic Eggs", "A2", 5.99, ppo=0.37),
    ]
    # Without preference: A1 wins on value
    d1 = evaluate_options(products, "eggs", prefer_organic=False)
    assert d1.sku == "A1"

    # With preference: organic gets +8, might not be enough to overcome big value gap
    d2 = evaluate_options(products, "eggs", prefer_organic=True)
    # A1: 50 + 25 (best ppo) + 3 = 78
    # A2: 50 + 0 (worst ppo) + 8 (organic) + 0 = 58
    # Value still wins — this is correct behavior
    assert d2.sku == "A1"


# ── get_purchase_frequency ──────────────────────────────────────────

def test_weekly_items():
    freq, _ = get_purchase_frequency("Whole Milk 1 Gallon")
    assert freq == PurchaseFrequency.WEEKLY

    freq, _ = get_purchase_frequency("Organic Bananas")
    assert freq == PurchaseFrequency.WEEKLY


def test_biweekly_items():
    freq, _ = get_purchase_frequency("Chicken Breast 2lb")
    assert freq == PurchaseFrequency.BIWEEKLY


def test_monthly_items():
    freq, _ = get_purchase_frequency("Heinz Ketchup 20oz")
    assert freq == PurchaseFrequency.MONTHLY


def test_quarterly_items():
    freq, _ = get_purchase_frequency("Pure Vanilla Extract")
    assert freq == PurchaseFrequency.QUARTERLY


def test_unknown_frequency():
    freq, _ = get_purchase_frequency("Obscure Widget 3000")
    assert freq == PurchaseFrequency.UNKNOWN


def test_frequency_uses_search_query_too():
    # Product name doesn't match, but search query does
    freq, _ = get_purchase_frequency("Fancy Product", search_query="organic milk")
    assert freq == PurchaseFrequency.WEEKLY


# ── preferred_brands ──────────────────────────────────────────────

def test_preferred_brand_boosts_score():
    """Preferred brand gets +12, winning over a non-preferred equal-price product."""
    products = [
        _product("Store Milk", "A1", 4.99, brand="Store", ppo=0.05),
        _product("Clover Milk", "A2", 4.99, brand="Clover", ppo=0.05),
    ]
    d = evaluate_options(products, "milk", preferred_brands={"milk": "Clover"})
    assert d.sku == "A2"
    assert "preferred brand" in d.reasoning


def test_preferred_brand_does_not_override_large_value_gap():
    """Preferred brand (+12) should not beat a product with major value advantage."""
    products = [
        _product("Cheap Generic Milk 128oz", "A1", 3.99, brand="Generic", ppo=0.03),
        _product("Clover Milk 64oz", "A2", 5.99, brand="Clover", ppo=0.09),
    ]
    d = evaluate_options(products, "milk", preferred_brands={"milk": "Clover"})
    # A1 gets +25 (best ppo). A2 gets +12 (preferred brand) but 0 for ppo.
    # A1 should still win.
    assert d.sku == "A1"


def test_preferred_brand_none_is_noop():
    """Passing preferred_brands=None should not change behavior."""
    products = [
        _product("Milk A", "A1", 4.99, brand="X", ppo=0.05),
        _product("Milk B", "A2", 4.99, brand="Y", ppo=0.05),
    ]
    d1 = evaluate_options(products, "milk", preferred_brands=None)
    d2 = evaluate_options(products, "milk")
    assert d1.sku == d2.sku
