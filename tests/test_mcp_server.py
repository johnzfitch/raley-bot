"""Tests for raley_assistant.mcp_server — MCP tool handlers.

All handlers are async. API calls and DB are fully mocked.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

from raley_assistant.api import Product, Offer


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


def _fake_offer(id="offer1", headline="Save $1", clipped=False):
    return Offer(
        id=id, code="CODE1", headline=headline,
        description="Description", category="Dairy",
        discount_amount=1.0, end_date="2025-12-31",
        is_clipped=clipped, image_url=None, max_apply=1,
        offer_type="SomethingExtra", badge_type="SomethingExtra",
        product_skus=["SKU1"],
    )


# ── handle_search ─────────────────────────────────────────────────

@patch("raley_assistant.mcp_server.get_connection")
@patch("raley_assistant.mcp_server.search_products")
@patch("raley_assistant.mcp_server.get_api_client")
async def test_handle_search_returns_fields(mock_client, mock_search, mock_conn):
    from raley_assistant.mcp_server import handle_search

    mock_client.return_value = MagicMock()
    mock_search.return_value = [
        _fake_product("Clover Milk", "A1", 599, "Clover"),
        _fake_product("Store Milk", "A2", 499, "Store"),
    ]
    mock_db = MagicMock()
    mock_conn.return_value = mock_db

    result = json.loads(await handle_search({"q": "milk"}))

    assert "sku" in result
    assert "name" in result
    assert "price" in result
    assert "cents" in result


@patch("raley_assistant.mcp_server.search_products")
@patch("raley_assistant.mcp_server.get_api_client")
async def test_handle_search_no_results(mock_client, mock_search):
    from raley_assistant.mcp_server import handle_search

    mock_client.return_value = MagicMock()
    mock_search.return_value = []

    result = json.loads(await handle_search({"q": "nonexistent"}))
    assert "error" in result


@patch("raley_assistant.mcp_server.get_connection")
@patch("raley_assistant.mcp_server.search_products")
@patch("raley_assistant.mcp_server.get_api_client")
async def test_handle_search_sale_flag(mock_client, mock_search, mock_conn):
    from raley_assistant.mcp_server import handle_search

    mock_client.return_value = MagicMock()
    mock_search.return_value = [
        _fake_product("Sale Milk", "A1", 599, "Store", sale_cents=499),
    ]
    mock_conn.return_value = MagicMock()

    result = json.loads(await handle_search({"q": "milk"}))
    assert result.get("sale") is True


# ── handle_add ────────────────────────────────────────────────────

@patch("raley_assistant.mcp_server.get_products_by_sku")
@patch("raley_assistant.mcp_server.api_add_to_cart")
@patch("raley_assistant.mcp_server.get_api_client")
async def test_handle_add_success(mock_client, mock_add, mock_get_sku):
    from raley_assistant.mcp_server import handle_add

    mock_client.return_value = MagicMock()
    mock_add.return_value = True
    mock_get_sku.return_value = []

    result = json.loads(await handle_add({"sku": "SKU1", "cents": 499}))
    assert result["ok"] is True
    assert result["sku"] == "SKU1"


@patch("raley_assistant.mcp_server.api_add_to_cart")
@patch("raley_assistant.mcp_server.get_api_client")
async def test_handle_add_failure(mock_client, mock_add):
    from raley_assistant.mcp_server import handle_add

    mock_client.return_value = MagicMock()
    mock_add.return_value = False

    result = json.loads(await handle_add({"sku": "SKU1", "cents": 499}))
    assert result["ok"] is False


# ── handle_build_list (plan) ──────────────────────────────────────

@patch("raley_assistant.mcp_server.get_connection")
@patch("raley_assistant.mcp_server.find_best_product")
@patch("raley_assistant.mcp_server.get_api_client")
async def test_handle_build_list_multi_item(mock_client, mock_find, mock_conn):
    from raley_assistant.mcp_server import handle_build_list

    mock_client.return_value = MagicMock()
    mock_db = MagicMock()
    mock_conn.return_value = mock_db

    # Return product dicts for each search (find_best_product returns list[dict])
    mock_find.return_value = [
        {
            "name": "Whole Milk", "sku": "A1", "price": 4.99,
            "brand": "Clover", "on_sale": False, "oz": 64,
            "price_per_oz": 0.078,
        },
    ]

    result = json.loads(await handle_build_list({"items": "milk, eggs"}))

    assert "items" in result
    assert "total" in result
    assert len(result["items"]) == 2


@patch("raley_assistant.mcp_server.find_best_product")
@patch("raley_assistant.mcp_server.get_api_client")
async def test_handle_build_list_not_found(mock_client, mock_find):
    from raley_assistant.mcp_server import handle_build_list

    mock_client.return_value = MagicMock()
    mock_find.return_value = []

    result = json.loads(await handle_build_list({"items": "unicorn steaks"}))
    assert "not_found" in result
    assert "unicorn steaks" in result["not_found"]


# ── handle_offers ─────────────────────────────────────────────────

@patch("raley_assistant.mcp_server.get_offers")
@patch("raley_assistant.mcp_server.get_api_client")
async def test_handle_offers_list(mock_client, mock_offers):
    from raley_assistant.mcp_server import handle_offers

    mock_client.return_value = MagicMock()
    mock_offers.return_value = [
        _fake_offer("o1", "Save $1"),
        _fake_offer("o2", "Save $2"),
    ]

    result = json.loads(await handle_offers({"action": "list"}))
    assert "offers" in result
    assert result["count"] == 2


@patch("raley_assistant.mcp_server.clip_all_offers")
@patch("raley_assistant.mcp_server.get_api_client")
async def test_handle_offers_clip_all(mock_client, mock_clip_all):
    from raley_assistant.mcp_server import handle_offers

    mock_client.return_value = MagicMock()
    mock_clip_all.return_value = (5, 1, ["error sample"])

    result = json.loads(await handle_offers({"action": "clip_all"}))
    assert result["clipped"] == 5
    assert result["failed"] == 1


# ── handle_auth ───────────────────────────────────────────────────

@patch("raley_assistant.mcp_server.check_auth_status")
async def test_handle_auth(mock_auth):
    from raley_assistant.mcp_server import handle_auth

    mock_auth.return_value = {
        "authenticated": True,
        "cookies_found": 5,
        "message": "Valid session found",
    }

    result = json.loads(await handle_auth({}))
    assert result["authenticated"] is True


# ── Error handling ────────────────────────────────────────────────

async def test_missing_cookies_raises_error():
    from raley_assistant.mcp_server import get_api_client

    with patch("raley_assistant.mcp_server.COOKIES_PATH") as mock_path:
        mock_path.exists.return_value = False
        mock_path.__str__ = lambda self: "/fake/path"
        with pytest.raises(RuntimeError, match="Cookies not found"):
            get_api_client()
