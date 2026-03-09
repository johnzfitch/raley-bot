"""Extended user memory for Raley assistant.

Manages two layers of persistent state:
  1. Structured T1D preferences (carb targets, GI ceiling, insulin ratio)
  2. Free-form shopping notes (liked recipes, brand reactions, trip insights)

Stored at ~/.config/raley-assistant/memory.json (mode 0o600).
Structured prefs in `preferences.json` remain separate and unchanged.

The MCP `memory` tool calls load_memory() / save_memory() / add_note().
Claude can read and update this via the tool — the agent file instructs it
to update memory as it learns the user's patterns.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MEMORY_PATH = Path.home() / ".config" / "raley-assistant" / "memory.json"

_CURRENT_SEASON = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "fall", 10: "fall", 11: "fall",
}


def current_season() -> str:
    return _CURRENT_SEASON[datetime.now().month]


@dataclass
class T1DConfig:
    """Type 1 diabetes management preferences."""

    carb_target_per_meal: int = 45          # grams per meal
    gi_ceiling: int = 55                    # max GI before flagging
    insulin_to_carb_ratio: str = ""         # e.g. "1:15" (1u per 15g carbs)
    correction_factor: str = ""             # e.g. "1:50" (1u per 50 mg/dL over target)
    target_bg: str = "80-130"              # mg/dL target range
    avoid_high_gi: bool = True
    prefer_low_carb: bool = False           # keto/low-carb mode
    avoid_items: list[str] = field(default_factory=list)   # items that spike BG
    safe_snacks: list[str] = field(default_factory=list)   # confirmed safe snacks
    favorite_proteins: list[str] = field(default_factory=list)
    favorite_recipes: list[str] = field(default_factory=list)  # recipe names that worked


@dataclass
class ShoppingConfig:
    """Shopping behavior preferences."""

    weekly_budget: float | None = None
    prefer_store_brand: bool = False
    max_unit_price_oz: float | None = None  # $/oz ceiling
    staples: list[str] = field(default_factory=list)       # always-buy items
    avoid_brands: list[str] = field(default_factory=list)  # brands to skip
    preferred_store_section: list[str] = field(default_factory=list)


@dataclass
class ShoppingMemory:
    """Complete user memory state."""

    t1d: T1DConfig = field(default_factory=T1DConfig)
    shopping: ShoppingConfig = field(default_factory=ShoppingConfig)
    notes: dict[str, str] = field(default_factory=dict)    # key → freeform note
    last_updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "t1d": asdict(self.t1d),
            "shopping": asdict(self.shopping),
            "notes": self.notes,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShoppingMemory":
        t1d_data = data.get("t1d", {})
        shopping_data = data.get("shopping", {})

        # Tolerate missing or extra fields
        t1d_fields = {f for f in T1DConfig.__dataclass_fields__}
        shopping_fields = {f for f in ShoppingConfig.__dataclass_fields__}

        t1d = T1DConfig(**{k: v for k, v in t1d_data.items() if k in t1d_fields})
        shopping = ShoppingConfig(**{k: v for k, v in shopping_data.items() if k in shopping_fields})

        return cls(
            t1d=t1d,
            shopping=shopping,
            notes=data.get("notes", {}),
            last_updated=data.get("last_updated", ""),
        )


def load_memory() -> ShoppingMemory:
    """Load memory from disk. Returns defaults if file missing or corrupt."""
    if not MEMORY_PATH.exists():
        return ShoppingMemory()
    try:
        with open(MEMORY_PATH) as f:
            data = json.load(f)
        return ShoppingMemory.from_dict(data)
    except (json.JSONDecodeError, OSError, TypeError):
        return ShoppingMemory()


def save_memory(mem: ShoppingMemory) -> None:
    """Save memory to disk with owner-only permissions."""
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    mem.last_updated = datetime.now(timezone.utc).isoformat()

    data = mem.to_dict()
    fd = os.open(str(MEMORY_PATH), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)


def add_note(key: str, value: str) -> None:
    """Add or update a free-form note in memory."""
    mem = load_memory()
    mem.notes[key] = value
    save_memory(mem)


def set_field(section: str, key: str, value: Any) -> tuple[bool, str]:
    """Set a structured field in memory.

    Args:
        section: "t1d" or "shopping"
        key: field name
        value: new value (must match field type)

    Returns (success, message).
    """
    mem = load_memory()

    if section == "t1d":
        target = mem.t1d
        valid_fields = set(T1DConfig.__dataclass_fields__)
    elif section == "shopping":
        target = mem.shopping
        valid_fields = set(ShoppingConfig.__dataclass_fields__)
    else:
        return False, f"Unknown section '{section}'. Use 't1d' or 'shopping'."

    if key not in valid_fields:
        return False, f"Unknown field '{key}' in {section}. Valid: {sorted(valid_fields)}"

    current = getattr(target, key)

    # Coerce type to match the field
    try:
        if isinstance(current, bool):
            if isinstance(value, str):
                value = value.lower() in ("true", "1", "yes")
            else:
                value = bool(value)
        elif isinstance(current, int) or (current is None and key in ("carb_target_per_meal", "gi_ceiling")):
            value = int(value)
        elif isinstance(current, float) or current is None:
            value = float(value) if value not in (None, "") else None
        elif isinstance(current, list):
            if isinstance(value, str):
                # Accept comma-separated string → list
                value = [v.strip() for v in value.split(",") if v.strip()]
    except (ValueError, TypeError) as e:
        return False, f"Type error setting {section}.{key}: {e}"

    setattr(target, key, value)
    save_memory(mem)
    return True, f"Set {section}.{key} = {value!r}"


def get_summary(mem: ShoppingMemory) -> dict[str, Any]:
    """Get a compact summary for MCP responses."""
    t = mem.t1d
    s = mem.shopping

    # Always include core T1D fields so the agent knows the active config
    # even before the user has changed anything from defaults.
    summary: dict[str, Any] = {
        "carb_target_per_meal_g": t.carb_target_per_meal,
        "gi_ceiling": t.gi_ceiling,
        "bg_target": t.target_bg,
        "avoid_high_gi": t.avoid_high_gi,
    }

    if t.insulin_to_carb_ratio:
        summary["icr"] = t.insulin_to_carb_ratio
    if t.avoid_items:
        summary["avoid_items"] = t.avoid_items
    if t.safe_snacks:
        summary["safe_snacks"] = t.safe_snacks
    if t.favorite_recipes:
        summary["favorite_recipes"] = t.favorite_recipes
    if t.favorite_proteins:
        summary["preferred_proteins"] = t.favorite_proteins
    if s.weekly_budget:
        summary["weekly_budget"] = f"${s.weekly_budget:.2f}"
    if s.staples:
        summary["always_buy"] = s.staples
    if s.avoid_brands:
        summary["avoid_brands"] = s.avoid_brands
    if mem.notes:
        summary["notes"] = mem.notes
    if mem.last_updated:
        summary["last_updated"] = mem.last_updated[:10]

    return summary
