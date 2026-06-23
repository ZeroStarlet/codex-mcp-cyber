#!/bin/bash
# SCCG Uninstall Script for macOS/Linux
# This script removes all components installed by setup.sh

# Do NOT use set -e — steps are independent, failures should not cascade

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Helper functions
write_step() {
    echo -e "\n${CYAN}[*] $1${NC}"
}

write_success() {
    echo -e "${GREEN}[OK] $1${NC}"
}

write_error() {
    echo -e "${RED}[ERROR] $1${NC}"
}

write_warning() {
    echo -e "${YELLOW}[WARN] $1${NC}"
}

# ==============================================================================
# Step 1: Check claude CLI
# ==============================================================================
write_step "Step 1: Checking claude CLI..."

CLAUDE_AVAILABLE=false
if command -v claude &> /dev/null; then
    CLAUDE_AVAILABLE=true
    write_success "claude CLI is available"
else
    write_warning "claude CLI is not available, will skip MCP server removal"
fi

# ==============================================================================
# Step 2: Remove MCP server
# ==============================================================================
write_step "Step 2: Removing MCP server registration..."

if [ "$CLAUDE_AVAILABLE" = false ]; then
    write_warning "Skipping: claude CLI not available"
else
    REMOVE_OUTPUT=$(claude mcp remove sccg --scope user </dev/null 2>&1)
    REMOVE_EXIT=$?
    if [ $REMOVE_EXIT -eq 0 ]; then
        write_success "MCP server 'sccg' removed"
    else
        write_warning "MCP server 'sccg' was not registered (nothing to remove)"
    fi
fi

# ==============================================================================
# Step 3: Remove Skills
# ==============================================================================
write_step "Step 3: Removing Skills..."

SKILLS_DIR="$HOME/.claude/skills"

for SKILL in sccg-workflow gemini-collaboration; do
    SKILL_PATH="$SKILLS_DIR/$SKILL"
    if [ -d "$SKILL_PATH" ]; then
        rm -rf "$SKILL_PATH"
        write_success "Removed skill: $SKILL"
    else
        write_warning "Skill not found, skipping: $SKILL"
    fi
done

# ==============================================================================
# Step 4: Clean CLAUDE.md
# ==============================================================================
write_step "Step 4: Cleaning global CLAUDE.md..."

CLAUDE_MD_PATH="$HOME/.claude/CLAUDE.md"
SCCG_MARKER="# SCCG Configuration"

if [ ! -f "$CLAUDE_MD_PATH" ]; then
    write_warning "CLAUDE.md not found, skipping"
elif ! grep -qF "$SCCG_MARKER" "$CLAUDE_MD_PATH"; then
    write_warning "No SCCG configuration found in CLAUDE.md, skipping"
else
    # Get line number of the marker
    MARKER_LINE=$(grep -nF "$SCCG_MARKER" "$CLAUDE_MD_PATH" | head -1 | cut -d: -f1)

    # Check if there's any non-whitespace content before the marker
    if [ "$MARKER_LINE" -gt 1 ]; then
        BEFORE_CONTENT=$(head -n $((MARKER_LINE - 1)) "$CLAUDE_MD_PATH" | tr -d '[:space:]')
    else
        BEFORE_CONTENT=""
    fi

    if [ -z "$BEFORE_CONTENT" ]; then
        # No meaningful content before marker -> delete file
        rm -f "$CLAUDE_MD_PATH"
        write_success "Deleted CLAUDE.md (contained only SCCG configuration)"
    else
        # Content exists before marker -> truncate at marker
        head -n $((MARKER_LINE - 1)) "$CLAUDE_MD_PATH" > "$CLAUDE_MD_PATH.tmp"
        # Remove trailing blank lines and add single newline
        sed -e :a -e '/^[[:space:]]*$/{ $d; N; ba; }' "$CLAUDE_MD_PATH.tmp" > "$CLAUDE_MD_PATH"
        # Ensure file ends with newline
        [ -n "$(tail -c 1 "$CLAUDE_MD_PATH")" ] && echo "" >> "$CLAUDE_MD_PATH"
        rm -f "$CLAUDE_MD_PATH.tmp"
        write_success "Removed SCCG configuration from CLAUDE.md"
    fi
fi

# ==============================================================================
# Step 5: Remove Coder config
# ==============================================================================
write_step "Step 5: Removing Coder configuration..."

CONFIG_DIR="$HOME/.sccg-mcp"

if [ ! -d "$CONFIG_DIR" ]; then
    write_warning "Config directory not found ($CONFIG_DIR), skipping"
else
    read -p "Remove Coder config directory $CONFIG_DIR (contains API token)? (y/N): " CONFIRM
    if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
        rm -rf "$CONFIG_DIR"
        write_success "Removed Coder config directory: $CONFIG_DIR"
    else
        write_warning "Skipping Coder config removal"
    fi
fi

# ==============================================================================
# Step 6: Remove local venv
# ==============================================================================
write_step "Step 6: Removing local virtual environment..."

VENV_DIR="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
    write_warning "Virtual environment not found ($VENV_DIR), skipping"
else
    read -p "Remove local virtual environment $VENV_DIR? (y/N): " CONFIRM
    if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
        rm -rf "$VENV_DIR"
        write_success "Removed virtual environment: $VENV_DIR"
    else
        write_warning "Skipping virtual environment removal"
    fi
fi

# ==============================================================================
# Done!
# ==============================================================================
echo ""
echo -e "${GREEN}============================================================${NC}"
write_success "SCCG uninstall completed!"
echo -e "${GREEN}============================================================${NC}"
echo ""

echo "The following shared tools were NOT removed:"
echo "  - uv (package manager)"
echo "  - claude CLI"
echo ""
echo "To reinstall SCCG, run:"
echo "  ./setup.sh"
echo ""
