"""User preferences loader for Raley.

Loads preferences.json from the project root or ~/.config/raley-assistant/
and provides typed access to shopping preferences. Used by the reasoning
engine to inform product selection (organic preference, brand preferences,
budget targets).

Example preferences.json:
{
    "milk": {"brand": "Clover", "type": "whole", "size": "gallon"},
    "general": {
        "prefer_local": true,
        "organic_preference": "indifferent",
        "budget_target": 200
    }
}
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


CONFIG_DIR = Path.home() / ".config" / "raley-assistant"
PREFERENCES_FILENAME = "preferences.json"

# Search order for preferences file
SEARCH_PATHS = [
    CONFIG_DIR / PREFERENCES_FILENAME,
    Path.cwd() / PREFERENCES_FILENAME,
]


@dataclass
class GeneralPrefs:
    """General shopping preferences."""

    prefer_local: bool = True
    organic_preference: str = "indifferent"  # "prefer", "avoid", "indifferent"
    budget_target: Optional[float] = None

    @property
    def prefer_organic(self) -> bool:
        return self.organic_preference == "prefer"


@dataclass
class ProductPref:
    """Preference for a specific product category."""

    brand: Optional[str] = None
    type: Optional[str] = None
    organic: Optional[bool] = None
    size: Optional[str] = None


@dataclass
class Preferences:
    """Complete user preferences."""

    general: GeneralPrefs = field(default_factory=GeneralPrefs)
    product_prefs: dict[str, ProductPref] = field(default_factory=dict)

    def get_product_pref(self, category: str) -> Optional[ProductPref]:
        """Get preference for a product category (case-insensitive)."""
        return self.product_prefs.get(category.lower())

    def preferred_brand(self, category: str) -> Optional[str]:
        """Get preferred brand for a category, if any."""
        pref = self.get_product_pref(category)
        return pref.brand if pref else None


def load_preferences() -> Preferences:
    """Load preferences from disk, returning defaults if not found.

    Searches in order:
        1. ~/.config/raley-assistant/preferences.json
        2. ./preferences.json (current working directory)
    """
    for path in SEARCH_PATHS:
        if path.exists():
            return _parse_preferences(path)

    return Preferences()


def _parse_preferences(path: Path) -> Preferences:
    """Parse preferences file into typed objects."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return Preferences()

    if not isinstance(data, dict):
        return Preferences()

    # Parse general preferences
    general_data = data.get("general", {})
    general = GeneralPrefs(
        prefer_local=general_data.get("prefer_local", True),
        organic_preference=general_data.get("organic_preference", "indifferent"),
        budget_target=general_data.get("budget_target"),
    )

    # Parse product-specific preferences
    product_prefs = {}
    for key, value in data.items():
        if key == "general" or not isinstance(value, dict):
            continue
        product_prefs[key.lower()] = ProductPref(
            brand=value.get("brand"),
            type=value.get("type"),
            organic=value.get("organic"),
            size=value.get("size"),
        )

    return Preferences(general=general, product_prefs=product_prefs)


def save_preferences(prefs: Preferences, path: Optional[Path] = None) -> None:
    """Save preferences to disk."""
    target = path or SEARCH_PATHS[0]
    target.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {
        "general": {
            "prefer_local": prefs.general.prefer_local,
            "organic_preference": prefs.general.organic_preference,
        }
    }
    if prefs.general.budget_target is not None:
        data["general"]["budget_target"] = prefs.general.budget_target

    for category, pref in prefs.product_prefs.items():
        entry: dict = {}
        if pref.brand:
            entry["brand"] = pref.brand
        if pref.type:
            entry["type"] = pref.type
        if pref.organic is not None:
            entry["organic"] = pref.organic
        if pref.size:
            entry["size"] = pref.size
        if entry:
            data[category] = entry

    with open(target, "w") as f:
        json.dump(data, f, indent=2)
