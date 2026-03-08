"""Cookie management for Raley.

Handles import from browser DevTools exports and persistent storage.
The rnet-based cookie jar has been removed — the CurlClient in api.py
builds its own cookie string directly from the JSON list.
"""

import json
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

COOKIES_DIR = Path.home() / ".config" / "raley-assistant"
COOKIES_FILE = COOKIES_DIR / "cookies.json"

# Required cookies for API access
REQUIRED_COOKIES = [
    "FLDR.Auth",       # Main auth token
    "FLDR.Session",    # Session ID
    "FLDR.User",       # User preferences
    "FLDR.CSRF",       # CSRF token
    "FLDR.RememberMe",
]


def load_cookies_from_devtools(json_path: Path | str) -> list[dict[str, Any]]:
    """Load cookies from DevTools JSON export.

    Supports both array format [...] and object format {"cookies": [...]}.
    """
    path = Path(json_path)
    with path.open() as f:
        data = json.load(f)

    if isinstance(data, list):
        return data
    elif isinstance(data, dict) and "cookies" in data:
        return data["cookies"]
    else:
        raise ValueError("Unrecognized cookie format. Expected array or {cookies: [...]}.")


def validate_cookies(cookies: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Check if all required cookies are present.

    Returns (valid, missing_cookies).
    """
    present = {c.get("name") for c in cookies}
    missing = [name for name in REQUIRED_COOKIES if name not in present]
    return len(missing) == 0, missing


def check_cookie_expiry(cookies: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Check cookie expiration status.

    Returns (expired_cookies, expiring_soon_cookies).
    Expiring soon = within 7 days.
    """
    now = datetime.now(timezone.utc).timestamp()
    seven_days = 7 * 24 * 60 * 60

    expired = []
    expiring_soon = []

    for cookie in cookies:
        name = cookie.get("name", "")
        expiry = cookie.get("expirationDate") or cookie.get("expires") or cookie.get("expiry")

        if not expiry:
            continue

        if isinstance(expiry, str):
            try:
                dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                expiry = dt.timestamp()
            except ValueError:
                try:
                    expiry = float(expiry)
                except ValueError:
                    continue

        if expiry < now:
            expired.append(name)
        elif expiry < (now + seven_days):
            expiring_soon.append(name)

    return expired, expiring_soon


def save_cookies(cookies: list[dict[str, Any]]) -> None:
    """Save cookies to config directory with restricted permissions."""
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)

    # Write with restrictive permissions (owner-only read/write)
    import os
    fd = os.open(str(COOKIES_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cookies, f, indent=2)


def load_saved_cookies() -> list[dict[str, Any]] | None:
    """Load previously saved cookies."""
    if COOKIES_FILE.exists():
        with COOKIES_FILE.open() as f:
            return json.load(f)
    return None


def import_and_save(json_path: Path | str) -> tuple[list[dict], list[str]]:
    """Import cookies from DevTools export and save for future use.

    Returns (cookies_list, warnings).
    """
    cookies = load_cookies_from_devtools(json_path)
    valid, missing = validate_cookies(cookies)

    warnings = []
    if not valid:
        warnings.append(f"Missing cookies: {', '.join(missing)}")

    expired, expiring_soon = check_cookie_expiry(cookies)
    if expired:
        warnings.append(f"Expired cookies: {', '.join(expired)}")
    if expiring_soon:
        warnings.append(f"Expiring within 7 days: {', '.join(expiring_soon)}")

    save_cookies(cookies)
    return cookies, warnings
