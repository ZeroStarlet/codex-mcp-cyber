#!/usr/bin/env bash
# codex-mcp-cyber Setup Script for Unix/macOS
# Automates MCP server installation and registration

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

step() { echo -e "\n${CYAN}[*]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# ======================================================================
# Step 1: Check dependencies
# ======================================================================
step "Step 1: Checking dependencies..."

if ! command -v uv &> /dev/null; then
    warn "uv is not installed, installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv &> /dev/null; then
        error "Failed to install uv. Install manually: https://github.com/astral-sh/uv"
        exit 1
    fi
fi
success "uv is installed"

if ! command -v claude &> /dev/null; then
    error "claude CLI is not installed"
    echo "Install: npm install -g @anthropic-ai/claude-code"
    exit 1
fi
success "claude CLI is installed"

# ======================================================================
# Step 2: Install project dependencies
# ======================================================================
step "Step 2: Installing project dependencies..."
cd "$SCRIPT_DIR"
uv sync
success "Project dependencies installed"

# ======================================================================
# Step 3: Register MCP server
# ======================================================================
step "Step 3: Registering MCP server..."

# Clean up old registrations
claude mcp remove codex-mcp-cyber --scope user 2>/dev/null || true

echo ""
echo "Select installation method:"
echo "  1) Remote install (recommended) - Auto-fetches latest version from GitHub"
echo "  2) Local install - Uses current project directory (for development)"
read -r -p "Enter choice [1]: " install_method
install_method=${install_method:-1}

if [ "$install_method" = "2" ]; then
    claude mcp add codex-mcp-cyber --scope user --transport stdio -- uv run --directory "$SCRIPT_DIR" codex-mcp-cyber
    success "MCP server registered (local install)"
else
    claude mcp add codex-mcp-cyber --scope user --transport stdio -- uvx --refresh --from git+https://github.com/ZeroStarlet/codex-mcp-cyber.git codex-mcp-cyber
    success "MCP server registered (remote install)"
fi

# ======================================================================
# Done
# ======================================================================
echo ""
echo "============================================================"
success "codex-mcp-cyber MCP setup completed!"
echo "============================================================"
echo ""
echo -e "${CYAN}Next steps:${NC}"
echo "  1. Restart Claude Code CLI"
echo "  2. Verify MCP: claude mcp list"
echo "  3. Install the cc-review skill as a plugin (if not already):"
echo "       claude plugin marketplace add ZeroStarlet/codex-mcp-cyber"
echo "       claude plugin install codex-mcp-cyber@zerostarlet"
echo ""
