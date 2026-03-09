# Raley

> Named after someone who made grocery day feel less like a hostage negotiation with your own pantry.

Automated grocery shopping assistant with intelligent unit pricing, automatic coupon clipping, and an MCP server so your AI can argue with produce departments on your behalf.

## What This Does

You tell Raley what you need. Raley finds the best deal, calculates unit pricing across every bizarre measurement the grocery industry has invented (per oz, per lb, per "each" — what even is an "each"), clips relevant coupons, and manages your cart. All through a CLI or Claude Desktop.

The grocery store API uses F5 bot detection, so Raley shells out to `curl` for TLS fingerprint evasion instead of using Python HTTP libraries that get immediately flagged. Yes, we're using `subprocess.run(["curl", ...])` in 2026. Sometimes the unglamorous solution is the one that actually works.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/johnzfitch/raley-bot/main/install.sh | bash
```

This clones the repo, sets up a Python venv, installs dependencies, and symlinks `raley-bot` to `~/.local/bin`. Requires `git` and `curl` (installs `uv` automatically if missing).

Then log in:

```bash
raley-bot login
```

### Manual Install

```bash
git clone https://github.com/johnzfitch/raley-bot.git
cd raley-bot

uv venv && source .venv/bin/activate
uv pip install -e ".[login]"

raley-bot login
```

## CLI

```bash
raley search "organic spinach"    # Find products with unit pricing
raley history                     # Previously purchased items
raley offers                      # Available coupons
raley clip-all                    # Clip everything. All of it.
raley status                      # Session health check
raley orders                      # Order history
raley points                      # Something Extra balance
```

## MCP Server (Claude Desktop / Claude Code)

**Claude Desktop** — add to `~/.config/Claude/claude_desktop_config.json` (Linux) or `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "raley-bot": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/raley-bot", "raley-mcp"]
    }
  }
}
```

**Claude Code** — add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "raley-bot": {
      "command": "/path/to/raley-bot/.venv/bin/raley-mcp",
      "args": []
    }
  }
}
```

The install script configures these automatically.

Then talk to Claude like a normal person:

```
"Find me avocados, show me the best deal"
"Add organic spinach to my cart"
"What's in my cart?"
"Plan a grocery run: milk, eggs, bread, chicken thighs"
```

### MCP Tools

| Tool | What It Does |
|------|-------------|
| `search` | Find products with unit pricing and value analysis |
| `add` | Add items to cart (returns product name for confirmation) |
| `remove` | Remove items by SKU |
| `cart` | View cart (supports `summary_only`, `save_to_file`, `limit`) |
| `offers` | List, clip all, or sync coupons to local DB |
| `plan` | Parse freeform grocery lists, find best matches (does not auto-add) |
| `price` | Check price history from local tracking DB |
| `orders` | Order history with date range and spend totals |
| `auth` | Session status (no cookie values exposed) |

## Unit Pricing

Raley normalizes prices across every unit the grocery industry throws at you. A 6CT avocado bag at $4.99 becomes $0.83/unit. A 2lb chicken breast at $8.99 becomes $0.28/oz. Weight-based items show per-oz or per-lb depending on size. Liquids get per-ml. Count items get per-unit.

```json
{
  "sku": "10101877",
  "name": "Hass Avocado, 6CT Bag",
  "price": "$4.99",
  "cents": 499,
  "per_unit": "$0.83"
}
```

## Architecture

```
raley_assistant/
├── api.py           # Curl-based HTTP client (F5 evasion)
├── cli.py           # Click CLI with Rich tables
├── mcp_server.py    # MCP tools for Claude Desktop
├── unit_pricing.py  # Normalize $/oz, $/lb, $/unit, $/ml
├── cart_builder.py  # Freeform grocery list parsing
├── reasoning.py     # Purchase frequency analysis
├── db.py            # SQLite price history tracking
├── auth.py          # Browser-based login (Helium/Selenium)
└── cookies.py       # Session persistence
```

### How the HTTP Client Works

The store's API sits behind F5 BIG-IP with browser fingerprinting. Python's `requests` and `httpx` get blocked because their TLS handshakes don't match real browsers. Rather than importing a Rust-based TLS emulation library (the previous approach using `rnet`, which created a phantom dependency that was never wired in), the client shells out to system `curl` which naturally produces a browser-like TLS fingerprint. Ugly, effective, zero exotic dependencies.

## Data Storage

| What | Where |
|------|-------|
| Session cookies | `~/.config/raley-assistant/cookies.json` (mode 0600) |
| Price history DB | `~/.local/share/raley-assistant/raley.db` |
| Saved results | `~/.local/share/raley-assistant/*.json` (mode 0600) |

## Security Notes

Session cookies are written with `0600` permissions (owner read/write only). Cookie values are sanitized for header injection characters before being passed to curl. No credentials, tokens, or cookie values are ever logged, committed, or included in MCP tool responses. The `auth` tool explicitly returns session status without exposing cookie contents.

The `search` tool is read-only — it does not silently clip coupons or modify cart state. Write operations (`add`, `remove`, `offers clip_all`) are always explicit.

Rate limiting is applied to bulk operations (200ms between requests, 500ms every 10th request) to avoid triggering bot detection.

## Preferences

Copy the example and edit to taste:

```bash
cp preferences.example.json ~/.config/raley-assistant/preferences.json
```

Format:

```json
{
  "milk": {
    "brand": "Clover",
    "type": "whole",
    "size": "gallon"
  },
  "general": {
    "prefer_local": true,
    "organic_preference": "indifferent",
    "budget_target": 200
  }
}
```

## Troubleshooting

**Session expired?** → `raley-bot login`

**Browser login not working?** → Export cookies manually with a browser extension ("Cookie-Editor" or "EditThisCookie"), save as JSON, then `raley login --file cookies.json`

**Database corrupted?** → `rm ~/.local/share/raley-assistant/raley.db` (rebuilds on next search)

**Coupon clip limit?** → Some manufacturer coupons have daily claim limits. This is server-side, not a bug.

## Development

```bash
uv pip install -e ".[dev]"
pytest
```

## Disclaimer

This is an unofficial tool not affiliated with or endorsed by the grocery chain. Use responsibly and in accordance with their Terms of Service. This project is named after a person, not a brand.

## License

Unlicense
