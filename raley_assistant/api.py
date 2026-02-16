"""Raley API client - Uses curl to bypass TLS fingerprinting.

Named after Raley, an inspiring person in our lives.
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Callable
from urllib.parse import quote, urlencode

BASE_URL = "https://www.raleys.com"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:146.0) Gecko/20100101 Firefox/146.0"

# Rate limiting defaults
DEFAULT_TIMEOUT = 30
MAX_COOKIE_VALUE_LEN = 8192  # RFC 6265 practical limit


def _sanitize_cookie_value(value: str) -> str:
    """Strip characters that could cause header injection."""
    # Remove newlines, carriage returns, and null bytes
    return value.replace("\n", "").replace("\r", "").replace("\x00", "")


class CurlClient:
    """HTTP client using curl to bypass F5 bot detection."""

    def __init__(self, cookies: list[dict]):
        self.cookie_str = "; ".join(
            f"{_sanitize_cookie_value(c['name'])}={_sanitize_cookie_value(c['value'])}"
            for c in cookies
            if c.get("name") and c.get("value") and len(c.get("value", "")) < MAX_COOKIE_VALUE_LEN
        )

    def _run_curl(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        json_body: Any = None,
        headers: dict | None = None,
    ) -> tuple[int, str]:
        """Run curl and return (status_code, body)."""
        if params:
            # Use proper URL encoding instead of naive string concatenation
            query = urlencode(params, quote_via=quote)
            url = f"{url}?{query}"

        cmd = ["curl", "-s", "-w", "\n%{http_code}", url]

        if method == "POST":
            cmd.extend(["-X", "POST"])

        # Default headers
        cmd.extend(["-H", f"User-Agent: {USER_AGENT}"])
        cmd.extend(["-H", "Accept: application/json, text/plain, */*"])
        cmd.extend(["-H", "Accept-Language: en-US,en;q=0.5"])
        cmd.extend(["-H", f"Cookie: {self.cookie_str}"])

        # Custom headers (sanitize values)
        if headers:
            for name, value in headers.items():
                safe_value = _sanitize_cookie_value(str(value))
                cmd.extend(["-H", f"{name}: {safe_value}"])

        # JSON body
        if json_body is not None:
            cmd.extend(["-H", "Content-Type: application/json"])
            cmd.extend(["-d", json.dumps(json_body)])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            return 0, '{"error": "Request timed out"}'

        output = result.stdout.strip()

        # Last line is status code
        lines = output.rsplit("\n", 1)
        if len(lines) == 2:
            body, status = lines
            try:
                return int(status), body
            except ValueError:
                return 0, output
        return 0, output

    def get(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict | str]:
        """GET request, returns (status, json_or_text)."""
        status, body = self._run_curl("GET", url, params=params, headers=headers)
        try:
            return status, json.loads(body)
        except json.JSONDecodeError:
            return status, body

    def post(
        self,
        url: str,
        json_body: Any = None,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict | str]:
        """POST request, returns (status, json_or_text)."""
        status, body = self._run_curl(
            "POST", url, params=params, json_body=json_body, headers=headers
        )
        try:
            return status, json.loads(body)
        except json.JSONDecodeError:
            return status, body


def load_cookies(path: Path | str) -> list[dict]:
    """Load cookies from JSON file."""
    with open(path) as f:
        data = json.load(f)
    # Handle both {"cookies": [...]} and [...] formats
    if isinstance(data, dict) and "cookies" in data:
        return data["cookies"]
    return data


def create_client(cookies_path: Path | str) -> CurlClient:
    """Create client from cookies file."""
    cookies = load_cookies(cookies_path)
    return CurlClient(cookies)


# ============================================================================
# OFFERS/COUPONS API
# ============================================================================


@dataclass
class Offer:
    """A Something Extra offer/coupon."""

    id: str  # ExtPromotionId
    code: str  # PromotionCode
    headline: str
    description: str
    category: str
    discount_amount: float
    end_date: str
    is_clipped: bool  # IsAccepted
    image_url: str | None
    max_apply: int
    offer_type: str  # "mfg", "SomethingExtra", "WeeklyExclusive"
    badge_type: str  # ExtBadgeTypeCode
    product_skus: list[str]  # ProductList SKUs this applies to


def get_offers(
    client: CurlClient,
    category: str | None = None,
    clipped: str | None = None,  # "Clipped" or "Unclipped"
    offset: int = 0,
    rows: int = 100,
) -> list[Offer]:
    """Fetch offers/coupons from the API."""
    params = {
        "type": "",
        "offset": str(offset),
        "rows": str(rows),
    }
    if category:
        params["category"] = category
    if clipped:
        params["clipped"] = clipped

    status, data = client.get(
        f"{BASE_URL}/api/offers/get-offers",
        params=params,
        headers={
            "x-url-path": "/something-extra/offers-and-savings",
            "Referer": f"{BASE_URL}/something-extra/offers-and-savings",
        },
    )

    if status != 200:
        raise RuntimeError(f"Failed to fetch offers: {status}")

    offers = []
    for item in data.get("data", []):
        badge_type = item.get("ExtBadgeTypeCode", "SomethingExtra")
        product_list = item.get("ProductList", [])
        skus = [str(p.get("ExtProductId", "")) for p in product_list if p.get("ExtProductId")]

        offers.append(
            Offer(
                id=str(item.get("ExtPromotionId", "")),
                code=item.get("PromotionCode", ""),
                headline=item.get("Headline", ""),
                description=item.get("Description", ""),
                category=item.get("PromotionCategoryName", ""),
                discount_amount=item.get("DiscountAmount", 0),
                end_date=item.get("EndDate", ""),
                is_clipped=item.get("IsAccepted", False),
                image_url=item.get("QualifiedImageUrl"),
                max_apply=item.get("MaxApply", 1),
                offer_type=badge_type,
                badge_type=badge_type,
                product_skus=skus,
            )
        )

    return offers


def get_offer_categories(client: CurlClient) -> list[str]:
    """Get available offer categories."""
    status, data = client.get(
        f"{BASE_URL}/api/offers/get-offers-filters",
        params={"type": ""},
        headers={"x-url-path": "/something-extra/offers-and-savings"},
    )

    if status != 200:
        return []

    categories = []
    for filter_group in data.get("data", []):
        if filter_group.get("id") == "category":
            for item in filter_group.get("items", []):
                categories.append(item.get("title", ""))
    return categories


def clip_offer(client: CurlClient, offer: Offer) -> tuple[bool, str]:
    """Clip/accept an offer.

    Uses different endpoints based on offer type:
    - mfg (manufacturer coupons): /api/offers/accept-coupons
    - SomethingExtra/WeeklyExclusive: /api/offers/accept

    Returns: (success, error_message)
    """
    if offer.offer_type == "mfg":
        endpoint = f"{BASE_URL}/api/offers/accept-coupons"
        body = {"offerId": offer.id, "offerType": "mfg"}
    else:
        endpoint = f"{BASE_URL}/api/offers/accept"
        body = {"offerId": offer.id, "offerType": offer.offer_type}

    status, response = client.post(
        endpoint,
        json_body=body,
        headers={"x-url-path": "/something-extra/offers-and-savings"},
    )

    if status == 200:
        return True, ""

    error_msg = f"HTTP {status}"
    if isinstance(response, dict):
        error_msg = response.get("message", response.get("error", error_msg))
    elif isinstance(response, str) and len(response) < 200:
        error_msg = response

    return False, error_msg


def clip_all_offers(
    client: CurlClient,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> tuple[int, int, list[str]]:
    """Clip all unclipped offers with rate limiting.

    Args:
        client: Authenticated API client
        on_progress: Optional callback(current, total, clipped_so_far)

    Returns (clipped_count, failed_count, error_samples).
    """
    import time

    all_offers = get_offers(client, clipped=None, rows=500)
    unclipped_offers = [o for o in all_offers if not o.is_clipped]

    clipped = 0
    failed = 0
    error_samples: list[str] = []

    for i, offer in enumerate(unclipped_offers):
        success, error_msg = clip_offer(client, offer)
        if success:
            clipped += 1
        else:
            failed += 1
            if (
                "expired" not in error_msg.lower()
                and "no longer valid" not in error_msg.lower()
                and len(error_samples) < 3
            ):
                error_samples.append(f"{offer.headline[:30]}: {error_msg}")

        if on_progress:
            on_progress(i + 1, len(unclipped_offers), clipped)

        # Rate limiting: 200ms between requests, 500ms every 10 requests
        if i % 10 == 9:
            time.sleep(0.5)
        else:
            time.sleep(0.2)

    return clipped, failed, error_samples


# ============================================================================
# SEARCH API
# ============================================================================


@dataclass
class Product:
    """A product from search."""

    sku: str
    name: str
    brand: str
    price_cents: int
    sale_price_cents: int | None
    on_sale: bool
    image_url: str | None
    size: str
    weight_lbs: float | None
    unit_oz: float | None
    price_per_oz: float | None


def _parse_product_from_item(item: dict) -> Product:
    """Parse a single product from API response item. Shared by search and browse."""
    master = item.get("masterData", {}).get("current", {})
    name = master.get("name", "")
    variant = master.get("masterVariant", {})
    sku = variant.get("sku", item.get("key", ""))

    # Price parsing
    price_info = variant.get("price") or {}
    price_value = price_info.get("value") or {}
    current_cents = price_value.get("centAmount", 0)

    price_cents = current_cents
    sale_price_cents = None
    regular_price_cents = None

    custom = price_info.get("custom", {})
    for field in custom.get("customFieldsRaw", []):
        if field.get("name") == "regularPrice":
            reg_value = field.get("value", {})
            regular_price_cents = reg_value.get("centAmount")
            break

    if regular_price_cents and regular_price_cents > current_cents:
        price_cents = regular_price_cents
        sale_price_cents = current_cents

    # Image
    images = variant.get("images", [])
    image_url = images[0].get("url") if images else None

    # Attributes
    brand = ""
    size = ""
    weight_lbs = None
    units_per_pkg = None
    unit_of_measure = None

    for attr in variant.get("attributesRaw", []):
        attr_name = attr.get("name", "")
        attr_value = attr.get("value")
        if attr_name == "brand" and isinstance(attr_value, str):
            brand = attr_value
        elif attr_name == "productSize" and isinstance(attr_value, str):
            size = attr_value
        elif attr_name == "weightInPounds":
            weight_lbs = float(attr_value) if attr_value else None
        elif attr_name == "unitsPerPackage":
            try:
                units_per_pkg = float(attr_value)
            except (ValueError, TypeError):
                pass
        elif attr_name == "unitOfMeasure":
            unit_of_measure = attr_value

    # Unit pricing
    unit_oz = None
    price_per_oz = None
    final_price = (sale_price_cents or price_cents) / 100

    if weight_lbs:
        oz_from_lbs = weight_lbs * 16
        price_per_oz = final_price / oz_from_lbs if oz_from_lbs > 0 else None
        unit_oz = oz_from_lbs
    elif units_per_pkg and unit_of_measure == "oz":
        unit_oz = units_per_pkg
        price_per_oz = final_price / units_per_pkg if units_per_pkg > 0 else None

    return Product(
        sku=sku,
        name=name,
        brand=brand,
        price_cents=price_cents,
        sale_price_cents=sale_price_cents,
        on_sale=sale_price_cents is not None,
        image_url=image_url,
        size=size,
        weight_lbs=weight_lbs,
        unit_oz=unit_oz,
        price_per_oz=price_per_oz,
    )


def _parse_products(data: dict) -> list[Product]:
    """Parse products from API response."""
    docs = data.get("docs", {}).get("data", [])
    return [_parse_product_from_item(item) for item in docs]


def search_products(
    client: CurlClient,
    query: str,
    on_sale: bool = False,
    previously_purchased: bool = False,
    dietary_filter: str | None = None,
    offset: int = 0,
    limit: int = 30,
) -> list[Product]:
    """Search for products."""
    filters = []

    if on_sale:
        filters.append({
            "id": "on_sale_store_ids",
            "operator": "AND",
            "selected": False,
            "items": [{"default": False, "id": "onSale", "selected": True, "title": "On Sale"}],
        })

    if previously_purchased:
        filters.append({
            "id": "past_purchase_customer_ids",
            "selected": False,
            "items": [{
                "default": False,
                "id": "previouslyPurchased",
                "selected": True,
                "title": "Previously Purchased",
            }],
        })

    if dietary_filter:
        filters.append({
            "id": "shelfGuide",
            "operator": "AND",
            "selected": False,
            "items": [{"default": False, "id": dietary_filter, "selected": True, "title": dietary_filter}],
        })

    body = {
        "query": query,
        "selectedFilters": filters,
        "sortQuery": "",
        "searchType": "keyword",
        "offset": offset,
        "limit": limit,
        "showSponsoredProducts": True,
    }

    status, data = client.post(f"{BASE_URL}/api/search", json_body=body)

    if status != 200:
        raise RuntimeError(f"Search failed: {status}")

    return _parse_products(data)


def get_previously_purchased(
    client: CurlClient, offset: int = 0, limit: int = 30
) -> list[Product]:
    """Get previously purchased products."""
    return search_products(client, "", previously_purchased=True, offset=offset, limit=limit)


# ============================================================================
# CART API
# ============================================================================


@dataclass
class CartItem:
    """Item to add to cart."""

    sku: str
    quantity: int
    price_cents: int
    sell_type: str = "byEach"
    estimated_weight: float | None = None


def add_to_cart(client: CurlClient, items: list[CartItem]) -> bool:
    """Add items to cart."""
    cart_items = []

    for item in items:
        fields = [
            {"name": "unitSellType", "value": item.sell_type},
            {
                "name": "regularPrice",
                "value": {
                    "type": "centPrecision",
                    "currencyCode": "USD",
                    "centAmount": item.price_cents,
                    "fractionDigits": 2,
                },
            },
        ]

        if item.estimated_weight is not None:
            fields.append({"name": "estimatedTotalWeight", "value": item.estimated_weight})

        cart_items.append({
            "quantity": item.quantity,
            "sku": item.sku,
            "fields": fields,
        })

    status, _ = client.post(f"{BASE_URL}/api/cart/item/add", json_body=cart_items)
    return status == 200


def remove_from_cart(client: CurlClient, sku: str) -> bool:
    """Remove item from cart."""
    status, _ = client.post(f"{BASE_URL}/api/cart/item/remove", json_body={"sku": sku})
    return status == 200


def get_cart(client: CurlClient) -> dict:
    """Get current cart contents."""
    status, data = client.get(f"{BASE_URL}/api/cart")
    if status != 200:
        return {}
    return data


# ============================================================================
# USER/SESSION API
# ============================================================================


def check_session(client: CurlClient) -> dict | None:
    """Check if session is valid. Returns user data or None."""
    status, data = client.get(f"{BASE_URL}/api/auth/session")
    if status != 200 or not data or not data.get("user"):
        return None
    return data


def get_user_profile(client: CurlClient) -> dict | None:
    """Get user profile."""
    status, data = client.get(f"{BASE_URL}/api/user/profile")
    if status != 200:
        return None
    return data


def get_points(client: CurlClient) -> dict | None:
    """Get Something Extra points."""
    status, data = client.get(f"{BASE_URL}/api/something-extra/get-points")
    if status != 200:
        return None
    return data


def get_orders(
    client: CurlClient,
    days_back: int = 90,
    limit: int = 30,
) -> list[dict]:
    """Get order history with line items."""
    from datetime import datetime, timedelta, timezone

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)

    body = {
        "offset": 0,
        "rows": limit,
        "searchParameter": {
            "orderType": ["Online"],
            "fulfillmentType": ["Pickup", "Delivery"],
            "dateRange": {
                "startDate": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "endDate": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            },
        },
    }

    status, data = client.post(f"{BASE_URL}/api/user/orders", json_body=body)
    if status != 200:
        return []
    return data.get("data", [])


def get_products_by_sku(client: CurlClient, skus: list[str]) -> list[dict]:
    """Get full product details by SKU list."""
    status, data = client.post(
        f"{BASE_URL}/api/product/get-products",
        json_body={"skus": skus},
    )
    if status != 200:
        return []
    return data if isinstance(data, list) else []
