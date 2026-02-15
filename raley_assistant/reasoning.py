"""Purchase reasoning engine for Raley.

Provides heuristic-based product selection and purchase frequency analysis.
This is not ML — it's a simple scoring system that weighs price, value ($/oz),
sale status, brand match, and organic preference to recommend the best option
from a set of search results.

The purchase frequency classifier uses product category heuristics to flag
items that are bought infrequently (monthly, quarterly) so the plan tool
can prompt for confirmation before adding them.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import re


class PurchaseFrequency(Enum):
    """How often a product category is typically purchased."""

    WEEKLY = "weekly"          # Produce, dairy, bread
    BIWEEKLY = "biweekly"      # Meat, snacks
    MONTHLY = "monthly"        # Condiments, spices, cleaning
    QUARTERLY = "quarterly"    # Specialty items, bulk
    UNKNOWN = "unknown"


@dataclass
class Decision:
    """Product selection decision with reasoning."""

    sku: str
    product_name: str
    price: float
    score: float
    flags: list[str] = field(default_factory=list)
    reasoning: str = ""


# Category keywords → purchase frequency mapping
_FREQUENCY_PATTERNS: list[tuple[PurchaseFrequency, list[str]]] = [
    (PurchaseFrequency.WEEKLY, [
        "milk", "bread", "eggs", "banana", "lettuce", "spinach", "tomato",
        "onion", "potato", "apple", "berry", "yogurt", "butter", "cream",
        "juice", "water", "soda",
    ]),
    (PurchaseFrequency.BIWEEKLY, [
        "chicken", "beef", "pork", "fish", "salmon", "shrimp", "turkey",
        "cheese", "cereal", "rice", "pasta", "chips", "crackers", "cookie",
        "frozen", "pizza",
    ]),
    (PurchaseFrequency.MONTHLY, [
        "ketchup", "mustard", "mayo", "sauce", "vinegar", "oil", "spice",
        "seasoning", "pepper", "salt", "sugar", "flour", "soap", "detergent",
        "cleaner", "tissue", "towel", "bag", "wrap", "foil",
    ]),
    (PurchaseFrequency.QUARTERLY, [
        "vanilla", "extract", "baking powder", "baking soda", "yeast",
        "honey", "maple", "syrup", "jam", "jelly", "preserve",
        "supplement", "vitamin",
    ]),
]


def get_purchase_frequency(
    product_name: str, search_query: str = ""
) -> tuple[PurchaseFrequency, str]:
    """Classify how often this product is typically purchased.

    Returns (frequency, reason_string).
    Uses both product name and original search query for matching.
    """
    text = f"{product_name} {search_query}".lower()

    for freq, keywords in _FREQUENCY_PATTERNS:
        for kw in keywords:
            if kw in text:
                return freq, f"'{kw}' is typically a {freq.value} purchase"

    return PurchaseFrequency.UNKNOWN, "Could not classify purchase frequency"


def evaluate_options(
    products: list[dict],
    query: str,
    prefer_organic: bool = False,
    prefer_value: bool = True,
    preferred_brands: dict[str, str] | None = None,
) -> Decision:
    """Score and rank product options, return the best one.

    Scoring factors (higher is better):
        - Lower price_per_oz → +weight (value)
        - On sale → +15 points
        - Brand name match to query → +10 points
        - Preferred brand (from preferences) → +12 points
        - Organic (if preferred) → +8 points
        - Lower absolute price → minor tiebreaker

    Flags applied:
        - "SALE" if product is on sale
        - "BEST_VALUE" if best $/oz in the set
        - "PRICE_WARNING" if price is >2x the cheapest option
        - "ORGANIC" if product name contains organic

    Args:
        products: List of product dicts from find_best_product()
            Required keys: name, sku, price
            Optional keys: brand, on_sale, oz, price_per_oz
        query: Original search query for relevance scoring
        prefer_organic: Boost organic products in scoring
        prefer_value: Prefer unit price over absolute price
        preferred_brands: Category→brand mapping from user preferences

    Returns:
        Decision for the highest-scoring product
    """
    if not products:
        return Decision(
            sku="",
            product_name="No products found",
            price=0.0,
            score=0.0,
            flags=["NOT_FOUND"],
            reasoning="No matching products",
        )

    if len(products) == 1:
        p = products[0]
        flags = []
        if p.get("on_sale"):
            flags.append("SALE")
        if "organic" in p.get("name", "").lower():
            flags.append("ORGANIC")
        return Decision(
            sku=str(p["sku"]),
            product_name=p["name"],
            price=p["price"],
            score=100.0,
            flags=flags,
            reasoning="Only option available",
        )

    # Find reference points
    prices = [p["price"] for p in products]
    min_price = min(prices)
    max_price = max(prices)

    ppo_values = [p["price_per_oz"] for p in products if p.get("price_per_oz")]
    min_ppo = min(ppo_values) if ppo_values else None

    query_lower = query.lower()
    query_words = set(query_lower.split())

    scored: list[tuple[float, dict, list[str], str]] = []

    for p in products:
        score = 50.0  # Base score
        flags: list[str] = []
        reasons: list[str] = []

        # Value scoring (price_per_oz)
        if prefer_value and p.get("price_per_oz") and min_ppo:
            ppo = p["price_per_oz"]
            # Best value gets +25, worst gets 0, linear interpolation
            if ppo == min_ppo:
                score += 25
                flags.append("BEST_VALUE")
                reasons.append("best $/oz")
            elif max(ppo_values) > min_ppo:
                ratio = 1 - ((ppo - min_ppo) / (max(ppo_values) - min_ppo))
                score += ratio * 20
                reasons.append(f"${ppo:.2f}/oz")

        # Sale bonus
        if p.get("on_sale"):
            score += 15
            flags.append("SALE")
            reasons.append("on sale")

        # Brand match (query string)
        brand = p.get("brand", "").lower()
        if brand and brand in query_lower:
            score += 10
            reasons.append("brand match")

        # Preferred brand bonus (from user preferences, independent of query)
        if preferred_brands and brand:
            for _cat, pref_brand in preferred_brands.items():
                if pref_brand.lower() == brand:
                    score += 12
                    reasons.append(f"preferred brand ({pref_brand})")
                    break

        # Name relevance — count query word matches
        name_lower = p.get("name", "").lower()
        word_matches = sum(1 for w in query_words if w in name_lower)
        if query_words:
            relevance = word_matches / len(query_words)
            score += relevance * 12
            if relevance >= 0.8:
                reasons.append("strong name match")

        # Organic preference
        if "organic" in name_lower:
            flags.append("ORGANIC")
            if prefer_organic:
                score += 8
                reasons.append("organic (preferred)")

        # Price warning — if >2x cheapest
        if min_price > 0 and p["price"] > min_price * 2:
            flags.append("PRICE_WARNING")
            reasons.append(f"${p['price']:.2f} is >2x cheapest")

        # Minor tiebreaker: lower absolute price
        if max_price > min_price:
            price_ratio = 1 - ((p["price"] - min_price) / (max_price - min_price))
            score += price_ratio * 3

        reasoning = "; ".join(reasons) if reasons else "baseline scoring"
        scored.append((score, p, flags, reasoning))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    best_score, best_product, best_flags, best_reasoning = scored[0]

    return Decision(
        sku=str(best_product["sku"]),
        product_name=best_product["name"],
        price=best_product["price"],
        score=best_score,
        flags=best_flags,
        reasoning=best_reasoning,
    )


def should_buy_this_trip(
    product_name: str,
    last_purchased: Optional[str] = None,
    typical_interval_days: Optional[int] = None,
) -> tuple[bool, str]:
    """Determine if a product should be bought on this shopping trip.

    This is a simple heuristic: if we know the purchase frequency and
    the last purchase date, we can estimate whether the user is likely
    to need more.

    NOTE: Currently unused by the MCP server. Kept for future integration
    with order history analysis.

    Args:
        product_name: Product name for frequency classification
        last_purchased: ISO date string of last purchase (optional)
        typical_interval_days: Override for purchase interval (optional)

    Returns:
        (should_buy, reason_string)
    """
    freq, _ = get_purchase_frequency(product_name)

    if not last_purchased:
        return True, "No purchase history — adding to be safe"

    from datetime import datetime, timezone

    try:
        last_date = datetime.fromisoformat(last_purchased.replace("Z", "+00:00"))
    except ValueError:
        return True, "Could not parse last purchase date"

    days_since = (datetime.now(timezone.utc) - last_date.replace(tzinfo=timezone.utc)).days

    # Use typical interval or estimate from frequency
    interval = typical_interval_days
    if not interval:
        interval_map = {
            PurchaseFrequency.WEEKLY: 7,
            PurchaseFrequency.BIWEEKLY: 14,
            PurchaseFrequency.MONTHLY: 30,
            PurchaseFrequency.QUARTERLY: 90,
            PurchaseFrequency.UNKNOWN: 14,  # Conservative default
        }
        interval = interval_map[freq]

    if days_since >= interval * 0.8:
        return True, f"Last bought {days_since}d ago (typical interval: {interval}d)"

    return False, f"Bought {days_since}d ago, next expected in ~{interval - days_since}d"
