"""Unit pricing calculations for product comparison."""

from dataclasses import dataclass
from typing import Optional
import re


# Standard unit conversions
OZ_PER_LB = 16
G_PER_OZ = 28.3495
G_PER_LB = 453.592
ML_PER_OZ = 29.5735
ML_PER_GAL = 3785.41
OZ_PER_GAL = 128


@dataclass
class UnitPrice:
    """Normalized unit pricing for comparison."""

    price_per_oz: Optional[float] = None
    price_per_lb: Optional[float] = None
    price_per_g: Optional[float] = None
    price_per_ml: Optional[float] = None
    price_per_unit: Optional[float] = None
    unit_count: Optional[int] = None
    best_metric: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to compact dict for JSON serialization."""
        result = {}
        if self.price_per_oz is not None:
            result["per_oz"] = f"${self.price_per_oz:.2f}"
        if self.price_per_lb is not None:
            result["per_lb"] = f"${self.price_per_lb:.2f}"
        if self.price_per_g is not None:
            result["per_g"] = f"${self.price_per_g:.3f}"
        if self.price_per_ml is not None:
            result["per_ml"] = f"${self.price_per_ml:.3f}"
        if self.price_per_unit is not None:
            result["per_unit"] = f"${self.price_per_unit:.2f}"
        if self.best_metric:
            result["best"] = self.best_metric
        return result


def extract_quantity_from_name(
    product_name: str, size_field: str = ""
) -> tuple[Optional[float], Optional[str]]:
    """Extract quantity and unit from product name and size field."""
    text = f"{product_name} {size_field}".lower()

    patterns = [
        r"(\d+\.?\d*)\s*(oz|ounce|ounces)\b",
        r"(\d+\.?\d*)\s*(lb|lbs|pound|pounds)\b",
        r"(\d+\.?\d*)\s*(kg|kilogram|kilograms)\b",
        r"(\d+\.?\d*)\s*(gal|gallon|gallons)\b",
        r"(\d+\.?\d*)\s*(ml|milliliter|milliliters)\b",
        r"(\d+\.?\d*)\s*(fl\.?\s?oz|fluid ounce)\b",
        r"(\d+\.?\d*)\s*(l|liter|liters)\b",
        r"(\d+\.?\d*)\s*(g|gram|grams)\b",
        r"(\d+\.?\d*)\s*(?:ct|count|pack|ea|each)\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            qty = float(match.group(1))
            unit = match.group(2) if len(match.groups()) > 1 else "ct"

            unit_map = {
                "ounce": "oz",
                "ounces": "oz",
                "lb": "lb",
                "lbs": "lb",
                "pound": "lb",
                "pounds": "lb",
                "g": "g",
                "gram": "g",
                "grams": "g",
                "kg": "kg",
                "kilogram": "kg",
                "kilograms": "kg",
                "ml": "ml",
                "milliliter": "ml",
                "milliliters": "ml",
                "l": "l",
                "liter": "l",
                "liters": "l",
                "gal": "gal",
                "gallon": "gal",
                "gallons": "gal",
                "fl.oz": "floz",
                "fl oz": "floz",
                "fluid ounce": "floz",
                "ct": "ct",
                "count": "ct",
                "pack": "ct",
                "ea": "ct",
                "each": "ct",
            }
            unit = unit_map.get(unit, unit)

            return qty, unit

    return None, None


def normalize_to_oz(qty: float, unit: str) -> Optional[float]:
    """Convert various units to ounces (weight)."""
    if unit == "oz":
        return qty
    elif unit == "lb":
        return qty * OZ_PER_LB
    elif unit == "g":
        return qty / G_PER_OZ
    elif unit == "kg":
        return (qty * 1000) / G_PER_OZ
    return None


def normalize_to_ml(qty: float, unit: str) -> Optional[float]:
    """Convert liquid units to milliliters."""
    if unit == "ml":
        return qty
    elif unit == "l":
        return qty * 1000
    elif unit == "floz":
        return qty * ML_PER_OZ
    elif unit == "gal":
        return qty * ML_PER_GAL
    return None


def calculate_unit_prices(
    price_cents: int,
    product_name: str,
    size_field: str = "",
    unit_oz: Optional[float] = None,
    weight_lbs: Optional[float] = None,
) -> UnitPrice:
    """Calculate all applicable unit prices for a product."""
    price_dollars = price_cents / 100
    result = UnitPrice()

    is_count_item = bool(
        re.search(
            r"\b(\d+)\s*(?:ct\b|count\b|pack\b|-count|-ct)", product_name.lower()
        )
    )

    # Prefer API-provided measurements over name parsing
    if unit_oz is not None and unit_oz > 0:
        result.price_per_oz = price_dollars / unit_oz
        result.price_per_lb = price_dollars / (unit_oz / OZ_PER_LB)
        result.price_per_g = price_dollars / (unit_oz * G_PER_OZ)

        if is_count_item:
            match = re.search(
                r"\b(\d+)\s*(?:ct\b|count\b|pack\b|-count|-ct)",
                product_name.lower(),
            )
            if match:
                count = int(match.group(1))
                result.unit_count = count
                result.price_per_unit = price_dollars / count
                result.best_metric = "per_unit"
                return result

        result.best_metric = "per_lb" if unit_oz >= 16 else "per_oz"
        return result

    if weight_lbs is not None and weight_lbs > 0:
        oz = weight_lbs * OZ_PER_LB
        result.price_per_oz = price_dollars / oz
        result.price_per_lb = price_dollars / weight_lbs
        result.price_per_g = price_dollars / (weight_lbs * G_PER_LB)
        result.best_metric = "per_lb" if weight_lbs >= 1 else "per_oz"
        return result

    # Fallback to name parsing
    qty, unit = extract_quantity_from_name(product_name, size_field)

    if not qty or not unit:
        return UnitPrice()

    oz = normalize_to_oz(qty, unit)
    if oz:
        result.price_per_oz = price_dollars / oz
        result.price_per_lb = price_dollars / (oz / OZ_PER_LB)
        result.price_per_g = price_dollars / (oz * G_PER_OZ)
        result.best_metric = "per_lb" if oz >= 16 else "per_oz"

    ml = normalize_to_ml(qty, unit)
    if ml:
        result.price_per_ml = price_dollars / ml
        result.best_metric = "per_ml"

    if unit == "ct":
        result.unit_count = int(qty)
        result.price_per_unit = price_dollars / qty
        result.best_metric = "per_unit"

    return result


def compare_value(product1: dict, product2: dict) -> Optional[str]:
    """Compare two products and return which is better value."""
    p1_pricing = calculate_unit_prices(
        round(product1["price"] * 100),
        product1.get("name", ""),
        product1.get("size", ""),
    )
    p2_pricing = calculate_unit_prices(
        round(product2["price"] * 100),
        product2.get("name", ""),
        product2.get("size", ""),
    )

    for metric in ["price_per_oz", "price_per_lb", "price_per_ml", "price_per_unit"]:
        v1 = getattr(p1_pricing, metric)
        v2 = getattr(p2_pricing, metric)

        if v1 and v2:
            diff_pct = abs((v1 - v2) / v2 * 100)
            cheaper = product1["name"] if v1 < v2 else product2["name"]
            metric_name = metric.replace("price_", "")
            return f"{cheaper[:30]} is {diff_pct:.0f}% cheaper {metric_name}"

    return None


def best_value_from_list(products: list[dict]) -> Optional[dict]:
    """Find best value product from list based on unit pricing."""
    if not products:
        return None

    scored = []
    for p in products:
        pricing = calculate_unit_prices(
            round(p["price"] * 100),
            p.get("name", ""),
            p.get("size", ""),
        )

        score = None
        if pricing.price_per_oz:
            score = pricing.price_per_oz
        elif pricing.price_per_ml:
            score = pricing.price_per_ml
        elif pricing.price_per_unit:
            score = pricing.price_per_unit

        if score:
            scored.append((score, p))

    if not scored:
        return min(products, key=lambda x: x["price"])

    return min(scored, key=lambda x: x[0])[1]
