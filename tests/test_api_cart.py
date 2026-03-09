"""Tests for raley_assistant.api — cart operations."""

from unittest.mock import MagicMock
from raley_assistant.api import (
    add_to_cart,
    remove_from_cart,
    update_cart_quantity,
    _find_cart_item,
    CartItem,
)


def _mock_client(cart_response: dict, post_status: int = 200):
    """Create a mock CurlClient with predefined responses."""
    client = MagicMock()
    client.get.return_value = (200, cart_response)
    client.post.return_value = (post_status, {})
    return client


# ── _find_cart_item ────────────────────────────────────────────────────


def test_find_cart_item_found():
    cart = {
        "lineItems": [
            {"id": "line-123", "quantity": 2, "variant": {"sku": "SKU1"}},
            {"id": "line-456", "quantity": 1, "variant": {"sku": "SKU2"}},
        ]
    }
    line_id, qty = _find_cart_item(cart, "SKU1")
    assert line_id == "line-123"
    assert qty == 2


def test_find_cart_item_not_found():
    cart = {"lineItems": [{"id": "line-123", "variant": {"sku": "SKU1"}}]}
    line_id, qty = _find_cart_item(cart, "NONEXISTENT")
    assert line_id is None
    assert qty == 0


def test_find_cart_item_empty_cart():
    line_id, qty = _find_cart_item({}, "SKU1")
    assert line_id is None
    assert qty == 0


# ── update_cart_quantity ───────────────────────────────────────────────


def test_update_cart_quantity_success():
    cart = {"lineItems": [{"id": "line-123", "quantity": 2, "variant": {"sku": "SKU1"}}]}
    client = _mock_client(cart)

    result = update_cart_quantity(client, "SKU1", 5)

    assert result is True
    client.post.assert_called_once()
    call_args = client.post.call_args
    assert "/api/cart/item/update" in call_args[0][0]
    assert call_args[1]["json_body"] == [{"lineItemId": "line-123", "quantity": 5}]


def test_update_cart_quantity_not_in_cart():
    cart = {"lineItems": []}
    client = _mock_client(cart)

    result = update_cart_quantity(client, "SKU1", 5)

    assert result is False
    client.post.assert_not_called()


# ── add_to_cart ────────────────────────────────────────────────────────


def test_add_to_cart_new_item():
    cart = {"lineItems": []}
    client = _mock_client(cart)

    items = [CartItem(sku="SKU1", quantity=2, price_cents=499)]
    result = add_to_cart(client, items)

    assert result is True
    # Should call add endpoint, not update
    call_args = client.post.call_args
    assert "/api/cart/item/add" in call_args[0][0]


def test_add_to_cart_increments_existing():
    cart = {"lineItems": [{"id": "line-123", "quantity": 2, "variant": {"sku": "SKU1"}}]}
    client = _mock_client(cart)

    items = [CartItem(sku="SKU1", quantity=3, price_cents=499)]
    result = add_to_cart(client, items)

    assert result is True
    # Should call update endpoint with incremented quantity
    call_args = client.post.call_args
    assert "/api/cart/item/update" in call_args[0][0]
    assert call_args[1]["json_body"] == [{"lineItemId": "line-123", "quantity": 5}]


def test_add_to_cart_mixed_new_and_existing():
    cart = {"lineItems": [{"id": "line-123", "quantity": 1, "variant": {"sku": "SKU1"}}]}
    client = _mock_client(cart)

    items = [
        CartItem(sku="SKU1", quantity=2, price_cents=499),  # existing
        CartItem(sku="SKU2", quantity=1, price_cents=299),  # new
    ]
    result = add_to_cart(client, items)

    assert result is True
    # Should have called both update and add
    assert client.post.call_count == 2


def test_add_to_cart_empty_list():
    client = _mock_client({})
    result = add_to_cart(client, [])
    assert result is True
    client.get.assert_not_called()


def test_add_to_cart_dedupes_same_sku_in_batch():
    """Same SKU twice in one call should accumulate quantities."""
    cart = {"lineItems": []}
    client = _mock_client(cart)

    items = [
        CartItem(sku="SKU1", quantity=2, price_cents=499),
        CartItem(sku="SKU1", quantity=3, price_cents=499),  # Same SKU
    ]
    result = add_to_cart(client, items)

    assert result is True
    # Should call add once with combined quantity
    call_args = client.post.call_args
    assert "/api/cart/item/add" in call_args[0][0]
    added_items = call_args[1]["json_body"]
    assert len(added_items) == 1
    assert added_items[0]["quantity"] == 5  # 2 + 3


def test_add_to_cart_dedupes_existing_same_sku_in_batch():
    """Same SKU twice in batch, already in cart - should combine then update."""
    cart = {"lineItems": [{"id": "line-123", "quantity": 1, "variant": {"sku": "SKU1"}}]}
    client = _mock_client(cart)

    items = [
        CartItem(sku="SKU1", quantity=2, price_cents=499),
        CartItem(sku="SKU1", quantity=3, price_cents=499),
    ]
    result = add_to_cart(client, items)

    assert result is True
    # Should update with 1 (existing) + 5 (combined) = 6
    call_args = client.post.call_args
    assert "/api/cart/item/update" in call_args[0][0]
    assert call_args[1]["json_body"] == [{"lineItemId": "line-123", "quantity": 6}]


# ── remove_from_cart ───────────────────────────────────────────────────


def test_remove_from_cart_success():
    cart = {"lineItems": [{"id": "line-123", "quantity": 2, "variant": {"sku": "SKU1"}}]}
    client = _mock_client(cart)

    result = remove_from_cart(client, "SKU1")

    assert result is True
    call_args = client.post.call_args
    assert "/api/cart/item/remove" in call_args[0][0]
    assert call_args[1]["json_body"] == {"lineItemId": "line-123"}


def test_remove_from_cart_not_found():
    cart = {"lineItems": []}
    client = _mock_client(cart)

    result = remove_from_cart(client, "SKU1")

    assert result is False
    client.post.assert_not_called()
