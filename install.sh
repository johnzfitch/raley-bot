#!/usr/bin/env bash
set -euo pipefail

REPO="https://github.com/johnzfitch/raley-bot.git"
INSTALL_DIR="${RALEY_DIR:-$HOME/.raley-bot}"
BIN_DIR="${HOME}/.local/bin"

info()  { printf '\033[0;36m%s\033[0m\n' "$*"; }
warn()  { printf '\033[0;33m%s\033[0m\n' "$*"; }
err()   { printf '\033[0;31m%s\033[0m\n' "$*" >&2; }

# Check dependencies
for cmd in git curl; do
  if ! command -v "$cmd" &>/dev/null; then
    err "Missing required command: $cmd"
    exit 1
  fi
done

if ! command -v uv &>/dev/null; then
  info "Installing uv..."
  info "Note: downloading installer from https://astral.sh/uv/install.sh"
  info "For manual install, see: https://docs.astral.sh/uv/getting-started/installation/"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# Clone or update
if [ -d "$INSTALL_DIR" ]; then
  info "Updating existing installation..."
  git -C "$INSTALL_DIR" pull -q --ff-only
else
  info "Cloning raley-bot..."
  git clone -q "$REPO" "$INSTALL_DIR"
fi

# Create venv and install
info "Setting up environment..."
cd "$INSTALL_DIR"
uv venv -q
uv pip install -e . --quiet

# Ensure bin directory exists
mkdir -p "$BIN_DIR"

# Symlink commands
for cmd in raley raley-bot raley-mcp; do
  target="$INSTALL_DIR/.venv/bin/$cmd"
  link="$BIN_DIR/$cmd"
  if [ -d "$link" ] && [ ! -L "$link" ]; then
    warn "Skipping $link: is a directory, not removing"
    continue
  fi
  if [ -L "$link" ] || [ -e "$link" ]; then
    rm -f "$link"
  fi
  ln -s "$target" "$link"
done

# Check PATH
if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
  echo ""
  warn "$BIN_DIR is not on your PATH."
  warn "Add this to your shell config (~/.bashrc, ~/.zshrc, etc.):"
  warn "  export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo ""
fi

# Configure MCP server for Claude Desktop
MCP_CMD="$INSTALL_DIR/.venv/bin/raley-mcp"
MCP_ENTRY="\"raley-bot\":{\"command\":\"$MCP_CMD\",\"args\":[]}"

configure_mcp() {
  local config_file="$1"
  local config_dir
  config_dir="$(dirname "$config_file")"

  # Already configured
  if [ -f "$config_file" ] && grep -q '"raley-bot"' "$config_file" 2>/dev/null; then
    info "MCP already configured in $(basename "$config_dir")/$(basename "$config_file")"
    return
  fi

  mkdir -p "$config_dir"

  if [ -f "$config_file" ]; then
    # File exists — use Python for safe JSON manipulation instead of sed
    python3 -c "
import json, sys
with open('$config_file') as f:
    cfg = json.load(f)
cfg.setdefault('mcpServers', {})
cfg['mcpServers']['raley-bot'] = {'command': '$MCP_CMD', 'args': []}
with open('$config_file', 'w') as f:
    json.dump(cfg, f, indent=2)
    f.write('\n')
" 2>/dev/null || {
      warn "Could not update $config_file automatically."
      warn "Add raley-bot MCP config manually."
    }
  else
    # Create fresh config
    printf '{\n  "mcpServers": {\n    "raley-bot": {\n      "command": "%s",\n      "args": []\n    }\n  }\n}\n' "$MCP_CMD" > "$config_file"
  fi

  info "MCP server configured in $config_file"
}

# Claude Desktop (Linux + macOS paths)
if [ "$(uname)" = "Darwin" ]; then
  CLAUDE_DESKTOP_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
else
  CLAUDE_DESKTOP_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/Claude/claude_desktop_config.json"
fi
configure_mcp "$CLAUDE_DESKTOP_CONFIG"

info ""
info "Installed successfully!"
info ""
info "Commands available:"
info "  raley-bot   - CLI interface"
info "  raley       - CLI interface (alias)"
info "  raley-mcp   - MCP server for Claude Desktop"
info ""
info "Get started:"
info "  raley-bot login"
