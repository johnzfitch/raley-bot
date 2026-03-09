"""Browser-based authentication for Raley."""

import json
import time
from pathlib import Path
from datetime import datetime, timezone

try:
    import helium

    HELIUM_AVAILABLE = True
except ImportError:
    HELIUM_AVAILABLE = False

RALEYS_LOGIN_URL = "https://www.raleys.com/account/signin"


def _is_raleys_domain(domain: str) -> bool:
    """Check if domain is exactly raleys.com or a subdomain of it."""
    domain = domain.lstrip(".").lower()
    return domain == "raleys.com" or domain.endswith(".raleys.com")


REQUIRED_COOKIES = ["FLDR.Auth", "FLDR.Session", "FLDR.User", "FLDR.CSRF", "FLDR.RememberMe"]
COOKIES_PATH = Path.home() / ".config" / "raley-assistant" / "cookies.json"


def interactive_login(timeout: int = 300) -> tuple[bool, str]:
    """Launch browser for interactive login.

    Returns (success, message).
    """
    if not HELIUM_AVAILABLE:
        return False, "Helium not installed. Run: uv pip install helium"

    try:
        helium.start_chrome(headless=False)
        helium.go_to(RALEYS_LOGIN_URL)

        start_time = time.time()
        while time.time() - start_time < timeout:
            driver = helium.get_driver()
            cookies = driver.get_cookies()

            if any(c["name"] == "FLDR.Auth" for c in cookies):
                save_cookies_from_selenium(cookies)
                helium.kill_browser()
                return True, "Login successful! Session saved."

            time.sleep(0.5)

        helium.kill_browser()
        return False, "Login timed out. Please try again."

    except Exception as e:
        try:
            helium.kill_browser()
        except Exception:
            pass
        return False, "Error during login. Check that Chrome is installed and try again."


def save_cookies_from_selenium(cookies: list[dict]) -> None:
    """Save cookies from Selenium format to config with restricted permissions."""
    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)

    formatted = [
        {
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c["path"],
            "secure": c.get("secure", False),
            "httpOnly": c.get("httpOnly", False),
            "sameSite": c.get("sameSite", "None"),
            "expires": c.get("expiry"),
        }
        for c in cookies
        if _is_raleys_domain(c.get("domain", ""))
    ]

    # Write with restrictive permissions
    import os
    fd = os.open(str(COOKIES_PATH), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"cookies": formatted}, f, indent=2)


def check_auth_status() -> dict:
    """Check authentication status without exposing cookie values."""
    if not COOKIES_PATH.exists():
        return {
            "authenticated": False,
            "cookies_found": 0,
            "path": str(COOKIES_PATH),
            "message": "No session found. Run 'raley-bot login' to authenticate.",
        }

    try:
        with open(COOKIES_PATH) as f:
            data = json.load(f)
            if isinstance(data, list):
                cookies = data
            elif isinstance(data, dict):
                cookies = data.get("cookies", [])
            else:
                cookies = []

            cookie_names = {c.get("name", "") for c in cookies}
            has_required = all(name in cookie_names for name in REQUIRED_COOKIES)

            now = datetime.now(timezone.utc).timestamp()
            seven_days = 7 * 24 * 60 * 60
            expired = []
            expiring_soon = []

            for cookie in cookies:
                name = cookie.get("name", "")
                expiry = (
                    cookie.get("expires")
                    or cookie.get("expirationDate")
                    or cookie.get("expiry")
                )

                if expiry:
                    if isinstance(expiry, str):
                        try:
                            dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                            expiry = dt.timestamp()
                        except ValueError:
                            try:
                                expiry = float(expiry)
                            except ValueError:
                                continue

                    # Only flag required cookies as expired
                    if name in REQUIRED_COOKIES:
                        if expiry < now:
                            expired.append(name)
                        elif expiry < (now + seven_days):
                            expiring_soon.append(name)

            if expired:
                message = f"Session expired ({len(expired)} cookies expired)"
            elif not has_required:
                message = "Session incomplete (missing required cookies)"
            elif expiring_soon:
                message = f"Session valid (but {len(expiring_soon)} cookies expiring soon)"
            else:
                message = "Valid session found"

            result = {
                "authenticated": has_required and len(expired) == 0,
                "cookies_found": len(cookies),
                "path": str(COOKIES_PATH),
                "message": message,
            }

            if expired:
                result["expired_cookies"] = expired
            if expiring_soon:
                result["expiring_soon"] = expiring_soon

            return result

    except Exception as e:
        return {
            "authenticated": False,
            "cookies_found": 0,
            "path": str(COOKIES_PATH),
            "message": "Error reading cookies. Try 'raley-bot login' to re-authenticate.",
        }
