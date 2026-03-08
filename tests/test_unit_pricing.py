"""Tests for raley_assistant.unit_pricing — unit price normalization."""

import math
from raley_assistant.unit_pricing import (
    extract_quantity_from_name,
    normalize_to_oz,
    calculate_unit_prices,
    compare_value,
    best_value_from_list,
    UnitPrice,
)


# ── extract_quantity_from_name ──────────────────────────────────────

def test_extract_oz():
    qty, unit = extract_quantity_from_name("Oat Milk 64oz")
    assert qty == 64.0
    assert unit == "oz"


def test_extract_lb():
    qty, unit = extract_quantity_from_name("Chicken Breast 2lb")
    assert qty == 2.0
    assert unit in ("lb", "lbs")


def test_extract_from_size_field():
    qty, unit = extract_quantity_from_name("Some Product", size_field="16 oz")
    assert qty == 16.0
    assert unit == "oz"


def test_extract_gallon():
    qty, unit = extract_quantity_from_name("Whole Milk 1 Gallon")
    assert qty == 1.0
    assert unit == "gal"


def test_extract_count():
    qty, unit = extract_quantity_from_name("Eggs 12ct")
    assert qty == 12.0
    assert unit == "ct"


def test_extract_none_when_no_unit():
    qty, unit = extract_quantity_from_name("Mystery Product")
    assert qty is None
    assert unit is None


# ── normalize_to_oz ─────────────────────────────────────────────────

def test_oz_passthrough():
    assert normalize_to_oz(16, "oz") == 16.0


def test_lb_to_oz():
    result = normalize_to_oz(2, "lb")
    assert result == 32.0


def test_gallon_to_ml():
    from raley_assistant.unit_pricing import normalize_to_ml, ML_PER_GAL
    result = normalize_to_ml(1, "gal")
    assert result == ML_PER_GAL


# ── calculate_unit_prices ───────────────────────────────────────────

def test_unit_price_from_api_oz():
    up = calculate_unit_prices(499, "Cereal 12oz", unit_oz=12.0)
    assert up.price_per_oz is not None
    assert math.isclose(up.price_per_oz, 4.99 / 12.0, rel_tol=0.01)
    assert up.price_per_lb is not None
    assert up.best_metric == "per_oz"  # <16oz → per_oz


def test_unit_price_from_api_oz_large():
    up = calculate_unit_prices(899, "Flour 5lb", unit_oz=80.0)
    assert up.best_metric == "per_lb"  # >=16oz → per_lb


def test_unit_price_from_weight_lbs():
    up = calculate_unit_prices(599, "Chicken", weight_lbs=2.0)
    assert up.price_per_oz is not None
    assert up.price_per_lb is not None
    assert math.isclose(up.price_per_lb, 5.99 / 2.0, rel_tol=0.01)


def test_unit_price_count_item():
    up = calculate_unit_prices(399, "Paper Towels 6ct", unit_oz=100.0)
    assert up.price_per_unit is not None
    assert up.unit_count == 6
    assert math.isclose(up.price_per_unit, 3.99 / 6, rel_tol=0.01)
    assert up.best_metric == "per_unit"


def test_unit_price_name_parsing_fallback():
    up = calculate_unit_prices(299, "Soda 12oz Can")
    assert up.price_per_oz is not None
    assert math.isclose(up.price_per_oz, 2.99 / 12.0, rel_tol=0.01)


def test_to_dict_formatting():
    up = UnitPrice(price_per_oz=0.4158, price_per_lb=6.65, best_metric="per_oz")
    d = up.to_dict()
    assert d["per_oz"] == "$0.42"
    assert d["per_lb"] == "$6.65"
    assert d["best"] == "per_oz"
    assert "per_ml" not in d


# ── compare_value / best_value_from_list ────────────────────────────

def test_compare_value_picks_cheaper():
    p1 = {"name": "Product A 16oz", "sku": "1", "price": 1.60, "price_per_oz": 0.10}
    p2 = {"name": "Product B 16oz", "sku": "2", "price": 3.20, "price_per_oz": 0.20}
    result = compare_value(p1, p2)
    assert result is not None
    assert "Product A" in result


def test_best_value_empty():
    assert best_value_from_list([]) is None


def test_best_value_picks_cheapest():
    products = [
        {"name": "Expensive 8oz", "sku": "1", "price": 4.00},
        {"name": "Cheap 16oz", "sku": "2", "price": 1.60},
        {"name": "Medium 10oz", "sku": "3", "price": 3.00},
    ]
    best = best_value_from_list(products)
    assert best is not None
    assert best["sku"] == "2"
