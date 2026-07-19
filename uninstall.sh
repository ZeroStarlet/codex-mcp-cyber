#!/usr/bin/env bash
# codex-mcp-cyber Uninstall Script for Unix/macOS
# Removes MCP server registration and local venv

set -euo pipefail

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
# Step 1: Check claude CLI
# ======================================================================
step "Step 1: Checking claude CLI..."

if command -v claude &> /dev/null; then
    success "claude CLI is available"
else
    warn "claude CLI not available, skipping MCP removal"
fi

# ======================================================================
# Step 2: Remove MCP server
# ======================================================================
step "Step 2: Removing MCP server registration..."

if ! command -v claude &> /dev/null; then
    warn "Skipping: claude CLI not available"
else
    claude mcp remove codex-mcp-cyber --scope user 2>/dev/null && \
        success "MCP server 'codex-mcp-cyber' removed" || \
        warn "MCP server 'codex-mcp-cyber' was not registered (nothing to remove)"
fi

# ======================================================================
# Step 3: Remove local venv
# ======================================================================
step "Step 3: Removing local virtual environment..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
    warn "Virtual environment not found, skipping"
else
    read -r -p "Remove local virtual environment $VENV_DIR? (y/N) " confirm
    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        rm -rf "$VENV_DIR"
        success "Removed virtual environment"
    else
        warn "Skipping virtual environment removal"
    fi
fi
# ======================================================================
# Done
# ======================================================================
echo ""
echo "============================================================"
success "codex-mcp-cyber uninstall completed!"
echo "============================================================"
echo ""
