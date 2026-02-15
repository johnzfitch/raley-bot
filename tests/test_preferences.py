"""Tests for raley_assistant.preferences — config loading."""

import json
import tempfile
from pathlib import Path
from raley_assistant.preferences import (
    _parse_preferences,
    Preferences,
    GeneralPrefs,
    ProductPref,
)


def _write_prefs(data: dict) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return Path(f.name)


def test_default_preferences():
    p = Preferences()
    assert p.general.prefer_organic is False
    assert p.general.organic_preference == "indifferent"
    assert p.general.budget_target is None
    assert p.product_prefs == {}


def test_parse_general():
    path = _write_prefs({
        "general": {
            "prefer_local": False,
            "organic_preference": "prefer",
            "budget_target": 150,
        }
    })
    p = _parse_preferences(path)
    assert p.general.prefer_local is False
    assert p.general.prefer_organic is True
    assert p.general.budget_target == 150
    path.unlink()


def test_parse_product_prefs():
    path = _write_prefs({
        "milk": {"brand": "Clover", "type": "whole", "size": "gallon"},
        "eggs": {"organic": True},
        "general": {"organic_preference": "indifferent"},
    })
    p = _parse_preferences(path)
    assert p.preferred_brand("milk") == "Clover"
    assert p.preferred_brand("eggs") is None
    milk = p.get_product_pref("milk")
    assert milk is not None
    assert milk.type == "whole"
    path.unlink()


def test_case_insensitive_lookup():
    path = _write_prefs({"Milk": {"brand": "Clover"}})
    p = _parse_preferences(path)
    assert p.preferred_brand("milk") == "Clover"
    assert p.preferred_brand("MILK") == "Clover"
    path.unlink()


def test_invalid_json_returns_defaults():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    f.write("not json{{{")
    f.close()
    p = _parse_preferences(Path(f.name))
    assert p.general.prefer_organic is False
    Path(f.name).unlink()


def test_non_dict_returns_defaults():
    path = _write_prefs([1, 2, 3])
    p = _parse_preferences(path)
    assert p.general.prefer_organic is False
    path.unlink()
