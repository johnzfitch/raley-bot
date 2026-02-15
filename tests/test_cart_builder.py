"""Tests for raley_assistant.cart_builder — grocery list parsing."""

from raley_assistant.cart_builder import parse_grocery_list


def test_simple_items():
    items = parse_grocery_list("eggs\nmilk\nbread")
    assert len(items) == 3
    assert items[0] == ("eggs", 1)
    assert items[1] == ("milk", 1)
    assert items[2] == ("bread", 1)


def test_quantities_with_number_prefix():
    items = parse_grocery_list("2 chicken breast\n5 sweet potatoes")
    assert items[0] == ("chicken breast", 2)
    assert items[1] == ("sweet potatoes", 5)


def test_comma_separated():
    items = parse_grocery_list("eggs, milk, bread")
    assert len(items) == 3


def test_mixed_separators():
    items = parse_grocery_list("eggs\n2 milk, 3 bread")
    assert len(items) == 3


def test_empty_lines_skipped():
    items = parse_grocery_list("eggs\n\n\nmilk\n\n")
    assert len(items) == 2


def test_empty_string():
    items = parse_grocery_list("")
    assert items == []


def test_quantity_units():
    items = parse_grocery_list("2 bags spinach\n3 bunches cilantro")
    assert items[0][1] == 2
    assert items[1][1] == 3


def test_whitespace_normalization():
    items = parse_grocery_list("  2   ground   chicken  ")
    assert len(items) == 1
    name, qty = items[0]
    assert qty == 2
    assert "  " not in name  # Internal whitespace collapsed


def test_dash_quantity():
    items = parse_grocery_list("chicken breast - 3")
    assert len(items) == 1
    assert items[0][1] == 3
    assert "chicken" in items[0][0].lower()
