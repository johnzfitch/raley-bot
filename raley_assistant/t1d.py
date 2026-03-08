"""Type 1 Diabetes nutrition module for Raley assistant.

Provides:
  - Embedded glycemic index database (~220 common grocery items)
  - GI lookup by product name (keyword fuzzy match)
  - T1D suitability scoring for product comparison
  - Coupon-to-product cross-reference for deal hunting

GI values sourced from: University of Sydney GI database, ADA guidelines,
and "Think Like a Pancreas" (Scheiner, 2023).

GI scale:
  Low    < 55  — preferred for T1D
  Medium 55-69 — use in portions
  High   >= 70  — flag, offer swap
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Glycemic Index Database
# Each entry: (keyword_pattern, gi_score, notes)
# Lower in the list = checked later (so more specific rules override generic)
# ---------------------------------------------------------------------------

_GI_DB: list[tuple[str, int, str]] = [
    # --- PROTEINS (GI ~0, don't spike BG) ---
    ("chicken|turkey|pork|beef|lamb|veal|bison|venison", 0, "protein — no GI"),
    ("salmon|tuna|tilapia|cod|halibut|shrimp|crab|lobster|scallop|sardine|anchovy", 0, "protein — no GI"),
    ("egg|eggs", 0, "protein — no GI"),
    ("tofu|tempeh|edamame", 15, "plant protein, low GI"),
    ("lentil", 32, "excellent legume"),
    ("chickpea|garbanzo", 28, "excellent legume"),
    ("black bean|kidney bean|navy bean|pinto bean|cannellini|white bean", 30, "legumes, low GI"),
    ("split pea|green pea|snow pea|snap pea", 48, "peas, low-medium"),

    # --- DAIRY ---
    ("greek yogurt|greek-style yogurt", 11, "low GI, high protein"),
    ("cottage cheese", 10, "low GI, high protein"),
    ("cream cheese|sour cream|creme fraiche", 0, "fat — no meaningful GI"),
    ("butter|ghee|cream", 0, "fat — no meaningful GI"),
    ("cheddar|mozzarella|parmesan|swiss|gouda|brie|feta|ricotta|provolone", 0, "cheese — no GI"),
    ("yogurt|kefir", 36, "fermented dairy"),
    ("milk|dairy milk", 39, "low GI"),
    ("oat milk", 69, "medium-high — check label"),
    ("almond milk", 25, "low GI"),
    ("coconut milk", 40, "low GI"),
    ("soy milk", 34, "low GI"),
    ("ice cream", 61, "medium — use sparingly"),
    ("frozen yogurt|froyo", 37, "moderate"),

    # --- LEAFY GREENS (~GI 15, negligible) ---
    ("spinach|kale|arugula|chard|collard|beet green|turnip green", 15, "leafy greens — negligible carbs"),
    ("lettuce|romaine|iceberg|butter lettuce|radicchio|endive", 15, "leafy greens"),
    ("cabbage|bok choy|napa cabbage", 10, "crucifer — very low"),

    # --- CRUCIFEROUS ---
    ("broccoli|cauliflower|broccolini|broccoli rabe", 15, "crucifer, negligible GI"),
    ("brussels sprout", 15, "crucifer"),
    ("asparagus", 15, "very low GI"),
    ("artichoke", 15, "low GI, high fiber"),
    ("celery", 15, "near-zero GI"),
    ("cucumber", 15, "very low GI"),
    ("zucchini|summer squash|yellow squash", 15, "very low GI"),

    # --- OTHER VEGETABLES ---
    ("bell pepper|sweet pepper", 15, "low GI"),
    ("mushroom", 15, "negligible GI"),
    ("green bean|haricot vert", 15, "low GI"),
    ("fennel", 15, "low GI"),
    ("eggplant|aubergine", 15, "low GI"),
    ("okra", 20, "low GI"),
    ("tomato|cherry tomato|roma tomato", 35, "low GI, lycopene"),
    ("onion|shallot|scallion|green onion|chive", 15, "low GI"),
    ("garlic", 30, "low GI"),
    ("leek", 25, "low GI"),
    ("pumpkin|kabocha", 64, "medium-high — portion control"),
    ("butternut squash|acorn squash|delicata", 51, "medium"),
    ("spaghetti squash", 20, "low GI alternative to pasta"),
    ("carrot", 35, "low-medium raw, higher cooked"),
    ("beet|beetroot", 64, "medium-high — portions"),
    ("parsnip", 52, "medium"),
    ("turnip|rutabaga", 30, "low-medium"),
    ("radish", 15, "very low GI"),
    ("jicama", 23, "low GI, high fiber"),
    ("avocado", 10, "healthy fat, negligible GI"),
    ("corn|sweet corn|maize", 52, "medium — watch portions"),
    ("potato|russet|yukon gold|red potato", 78, "HIGH GI — swap to sweet potato"),
    ("sweet potato|yam", 63, "medium — better than potato"),

    # --- FRUITS ---
    ("strawberry", 40, "low GI, high vitamin C"),
    ("raspberry|blackberry|boysenberry", 25, "low GI, high fiber"),
    ("blueberry", 53, "low-medium, antioxidants"),
    ("cherry|tart cherry", 22, "low GI"),
    ("grapefruit|pomelo", 25, "low GI"),
    ("lemon|lime", 20, "negligible GI, mostly juice/zest"),
    ("orange|mandarin|clementine|tangerine", 43, "low-medium GI"),
    ("peach|nectarine", 42, "low GI"),
    ("plum|prune", 29, "low GI"),
    ("apricot", 34, "low-medium"),
    ("apple|fuji|gala|granny smith|honeycrisp", 38, "low GI, keep skin on"),
    ("pear|bosc|bartlett|anjou", 38, "low GI, high fiber"),
    ("kiwi|kiwifruit", 50, "low-medium"),
    ("grapes|grape|concord", 46, "low-medium — easy to overeat"),
    ("fig", 61, "medium — limit dried"),
    ("pomegranate", 35, "low GI, antioxidants"),
    ("mango", 60, "medium — portion control"),
    ("papaya", 60, "medium"),
    ("pineapple", 66, "medium-high — portions"),
    ("banana", 51, "medium — riper = higher GI"),
    ("cantaloupe|honeydew|melon", 65, "medium-high"),
    ("watermelon", 72, "HIGH — high GI despite water content"),
    ("date|medjool", 103, "VERY HIGH — diabetic caution"),
    ("raisin|dried cranberry|dried apricot", 64, "medium-high — concentrated sugars"),

    # --- GRAINS & BREAD ---
    ("barley|barley flour|pearl barley", 28, "excellent — highest beta-glucan"),
    ("bulgur|cracked wheat", 46, "low-medium, whole grain"),
    ("farro|freekeh|spelt|emmer", 40, "ancient grains, lower GI"),
    ("quinoa", 53, "low-medium, complete protein"),
    ("millet", 71, "medium-high — portions"),
    ("steel.cut oat|irish oat|scottish oat", 55, "low-medium, least processed"),
    ("rolled oat|old fashioned oat", 57, "low-medium"),
    ("instant oat|quick oat", 83, "HIGH — avoid"),
    ("granola", 62, "medium-high — watch serving size"),
    ("bran flake|all-bran|fiber one", 37, "low-medium, high fiber"),
    ("shredded wheat", 67, "medium"),
    ("corn flake|frosted flake|fruit loop", 81, "HIGH — spike risk"),
    ("cheerio|oat ring cereal", 74, "high — portions"),
    ("whole grain bread|whole wheat bread|whole wheat", 69, "medium — better than white"),
    ("sourdough|sourdough bread", 54, "low-medium — fermentation lowers GI"),
    ("rye bread|pumpernickel|dark rye", 58, "medium — good fiber"),
    ("ezekiel|sprouted grain bread|sprouted bread", 36, "low GI — best bread choice"),
    ("white bread|sandwich bread|enriched bread", 75, "HIGH — limit"),
    ("bagel", 72, "HIGH — portion control"),
    ("pita|flatbread", 57, "medium"),
    ("corn tortilla|taco shell", 52, "medium"),
    ("flour tortilla|wheat tortilla", 30, "low-medium"),
    ("white rice|jasmine rice|short grain rice", 73, "HIGH — swap to alternatives"),
    ("brown rice|long grain brown", 68, "medium — acceptable"),
    ("wild rice", 57, "medium"),
    ("basmati rice", 57, "medium — lower than other white rice"),
    ("cauliflower rice|riced cauliflower", 15, "excellent swap for rice"),
    ("white pasta|spaghetti|linguine|penne|rigatoni|fettuccine", 49, "medium — al dente lowers GI"),
    ("whole wheat pasta|whole grain pasta", 42, "low-medium"),
    ("chickpea pasta|lentil pasta|bean pasta|edamame pasta", 22, "low GI, high protein"),
    ("glass noodle|rice noodle|rice vermicelli", 58, "medium"),
    ("udon", 55, "medium"),
    ("soba|buckwheat noodle", 46, "low-medium"),
    ("gnocchi", 68, "medium-high"),

    # --- CRACKERS & SNACKS ---
    ("rice cake|puffed rice", 82, "HIGH — swap to nuts"),
    ("pretzel|hard pretzel", 83, "HIGH — avoid"),
    ("water cracker|plain cracker|saltine", 74, "high"),
    ("whole grain cracker|rye crispbread|wasa|finn crisp", 50, "medium — decent fiber"),
    ("graham cracker", 74, "high"),
    ("popcorn", 65, "medium — plain is best"),
    ("potato chip|tortilla chip|corn chip", 60, "medium — watch portions"),
    ("pringle|cheese puff|cheese curl", 74, "high"),
    ("nut|almond|cashew|walnut|pecan|pistachio|macadamia|hazelnut", 15, "excellent — low GI, healthy fat"),
    ("peanut|peanut butter", 14, "low GI — natural/no-sugar-added"),
    ("almond butter|cashew butter|sunflower butter|tahini", 20, "low GI nut butters"),
    ("mixed nut|trail mix", 20, "watch for added sugar/raisins"),
    ("granola bar|protein bar|energy bar|larabar|rxbar|kind bar", 50, "medium — check label"),

    # --- SWEETENERS & CONDIMENTS ---
    ("sugar|white sugar|brown sugar|cane sugar", 68, "HIGH — limit"),
    ("honey", 58, "medium — use sparingly"),
    ("maple syrup|pure maple", 54, "medium — real maple lower GI than pancake syrup"),
    ("pancake syrup|corn syrup|agave syrup", 68, "HIGH"),
    ("stevia|monk fruit|erythritol|xylitol|allulose", 0, "zero GI sweeteners"),
    ("jam|jelly|preserve|marmalade", 51, "medium — watch portions"),
    ("ketchup|catsup", 55, "medium — surprisingly high sugar"),
    ("mustard|yellow mustard|dijon|whole grain mustard", 5, "negligible GI"),
    ("mayo|mayonnaise", 0, "fat — no GI"),
    ("salsa|pico de gallo", 20, "low GI"),
    ("guacamole", 10, "low GI — healthy fat"),
    ("hummus", 28, "low GI — chickpea base"),
    ("vinegar|balsamic|apple cider vinegar|red wine vinegar", 5, "may lower meal GI"),
    ("olive oil|avocado oil|coconut oil|vegetable oil", 0, "fat — no GI"),
    ("soy sauce|tamari|coconut aminos", 10, "negligible GI"),
    ("hot sauce|sriracha|tabasco", 5, "negligible GI"),
    ("ranch|caesar|blue cheese|thousand island", 0, "fat-based — low GI"),
    ("barbecue sauce|bbq sauce|teriyaki sauce|hoisin", 55, "medium — watch sugar"),
    ("tomato sauce|marinara|pasta sauce", 45, "medium — check for added sugar"),

    # --- BEVERAGES ---
    ("orange juice|oj|apple juice|grape juice|fruit juice", 50, "medium — whole fruit better"),
    ("vegetable juice|tomato juice|v8", 43, "medium-low"),
    ("soda|cola|pepsi|coke|sprite|ginger ale|dr pepper", 63, "HIGH — liquid sugar"),
    ("diet soda|diet coke|diet pepsi|zero sugar|sugar free soda", 0, "zero GI — watch artificial sweeteners"),
    ("sports drink|gatorade|powerade", 78, "HIGH — for exercise only"),
    ("energy drink|red bull|monster|celsius", 53, "medium + caffeine"),
    ("kombucha", 15, "low GI, check sugar label"),
    ("coconut water", 55, "medium — high natural sugar"),
    ("whole milk|2% milk|skim milk", 39, "low GI"),
    ("almond milk|oat milk|soy milk|cashew milk", 25, "low-medium — check added sugar"),
    ("sparkling water|seltzer|club soda|mineral water", 0, "zero GI"),
    ("coffee|espresso|cold brew", 0, "zero GI — watch additives"),
    ("tea|green tea|herbal tea|black tea", 0, "zero GI"),
    ("beer|lager|ale|stout", 66, "medium — impacts BG"),
    ("wine|red wine|white wine", 0, "minimal carbs, affects BG differently"),
    ("spirits|vodka|whiskey|tequila|gin|rum", 0, "no carbs, but hypoglycemia risk"),

    # --- BAKING ---
    ("all purpose flour|white flour|bread flour|cake flour", 70, "HIGH"),
    ("whole wheat flour|whole grain flour", 58, "medium"),
    ("almond flour|almond meal", 20, "low GI excellent swap"),
    ("coconut flour", 25, "low GI, high fiber"),
    ("chickpea flour|garbanzo flour|besan", 35, "low-medium"),
    ("oat flour", 55, "medium"),
    ("baking powder|baking soda|yeast|cream of tartar", 0, "leavening — no GI"),
    ("cocoa powder|unsweetened cocoa", 20, "low GI"),
    ("dark chocolate|70% chocolate|85% chocolate|bittersweet", 22, "low GI, >70% cocoa"),
    ("milk chocolate|white chocolate|chocolate chip", 43, "medium — portions"),
    ("vanilla|vanilla extract|vanilla bean", 0, "flavoring — no GI"),
]

# Pre-compile patterns for performance
_GI_PATTERNS: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(pattern, re.IGNORECASE), gi, note)
    for pattern, gi, note in _GI_DB
]


def get_gi(product_name: str) -> tuple[int | None, str]:
    """Look up GI for a product by fuzzy keyword match.

    Returns (gi_score, note) where gi_score is None if not found.
    Later matches in the DB override earlier ones (more specific wins).
    """
    result_gi: int | None = None
    result_note = ""

    for pattern, gi, note in _GI_PATTERNS:
        if pattern.search(product_name):
            result_gi = gi
            result_note = note  # last match wins (more specific)

    return result_gi, result_note


def gi_category(gi: int | None) -> str:
    """Classify a GI score into low/medium/high."""
    if gi is None:
        return "unknown"
    if gi < 55:
        return "low"
    if gi < 70:
        return "medium"
    return "high"


@dataclass
class T1DScore:
    """T1D suitability assessment for a product."""
    gi: int | None
    category: str          # "low" | "medium" | "high" | "unknown"
    flag: str              # "" | "WARN_GI" | "HIGH_GI"
    note: str              # human-readable GI context
    swap_suggestion: str   # lower-GI alternative if applicable


# Common swap suggestions for high-GI items
_SWAPS: dict[str, str] = {
    "white rice": "cauliflower rice (GI 15) or basmati rice (GI 57)",
    "white bread": "Ezekiel/sprouted bread (GI 36) or sourdough (GI 54)",
    "potato": "sweet potato (GI 63) or cauliflower mash",
    "instant oat": "steel-cut oats (GI 55) or rolled oats (GI 57)",
    "watermelon": "strawberries (GI 40) or raspberries (GI 25)",
    "corn flake": "All-Bran or steel-cut oats",
    "rice cake": "whole-grain crackers or nuts",
    "pretzel": "nuts or cheese",
    "sports drink": "water + electrolyte tablets",
    "soda": "sparkling water with lemon",
    "date": "fresh berries",
    "bagel": "Ezekiel bread or sourdough",
    "white pasta": "chickpea pasta (GI 22) or whole-wheat pasta (GI 42)",
}


def score_t1d(product_name: str, gi_ceiling: int = 55) -> T1DScore:
    """Score a product for T1D suitability based on GI.

    Args:
        product_name: Product name string from Raley's search
        gi_ceiling: User's max acceptable GI (default 55 = low GI threshold)

    Returns T1DScore with GI, category, flags, and swap suggestions.
    """
    gi, note = get_gi(product_name)
    cat = gi_category(gi)

    flag = ""
    if gi is not None:
        if gi >= 70:
            flag = "HIGH_GI"
        elif gi > gi_ceiling:
            flag = "WARN_GI"

    # Find swap suggestion
    swap = ""
    name_lower = product_name.lower()
    for key, suggestion in _SWAPS.items():
        if key in name_lower:
            swap = suggestion
            break

    return T1DScore(gi=gi, category=cat, flag=flag, note=note, swap_suggestion=swap)


def annotate_product(product: dict, gi_ceiling: int = 55) -> dict:
    """Add T1D fields to a product dict (from search/plan results).

    Modifies in place and returns the dict.
    """
    name = product.get("name", "")
    t1d = score_t1d(name, gi_ceiling)

    if t1d.gi is not None:
        product["gi"] = t1d.gi
        product["gi_cat"] = t1d.category
    if t1d.flag:
        product.setdefault("flags", [])
        if isinstance(product["flags"], list) and t1d.flag not in product["flags"]:
            product["flags"].append(t1d.flag)
    if t1d.swap_suggestion:
        product["gi_swap"] = t1d.swap_suggestion

    return product


def find_coupon_matches(
    offers: list,
    products: list[dict],
) -> dict[str, list]:
    """Cross-reference a product list against clipped coupons.

    Args:
        offers: list of Offer objects (from api.get_offers)
        products: list of product dicts (with "sku" key)

    Returns dict mapping sku → list of matching offer headlines.
    """
    # Build a set of (sku → offers) from the Offer.product_skus field
    sku_to_offers: dict[str, list[str]] = {}
    for offer in offers:
        if not offer.is_clipped:
            continue
        for sku in getattr(offer, "product_skus", []):
            sku_to_offers.setdefault(sku, []).append(
                f"{offer.headline[:50]} (save ${offer.discount_amount:.2f})"
                if offer.discount_amount else offer.headline[:50]
            )

    matches: dict[str, list] = {}
    for product in products:
        sku = str(product.get("sku", ""))
        if sku in sku_to_offers:
            matches[sku] = sku_to_offers[sku]

    return matches
