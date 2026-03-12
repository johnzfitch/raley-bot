"""Test configuration — mock external deps that may not be installed."""

import sys
from unittest.mock import MagicMock

# Mock the mcp package if not installed (network may be unavailable)
if "mcp" not in sys.modules:
    sys.modules["mcp"] = MagicMock()
    sys.modules["mcp.server"] = MagicMock()
    sys.modules["mcp.server.models"] = MagicMock()
    sys.modules["mcp.server.stdio"] = MagicMock()
    sys.modules["mcp.types"] = MagicMock()
