#!/bin/bash
# SCCG One-Click Setup Script for macOS/Linux
set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Suppress uv hardlink warnings (cross-filesystem fallback)
export UV_LINK_MODE=copy

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
# Step 1: Check dependencies
# ==============================================================================
write_step "Step 1: Checking dependencies..."

# Check and install uv
if command -v uv &> /dev/null; then
    write_success "uv is installed"
else
    write_warning "uv is not installed, installing automatically..."
    if curl -LsSf https://astral.sh/uv/install.sh | sh; then
        export PATH="$HOME/.local/bin:$PATH"
        write_success "uv installed successfully"
    else
        write_error "Failed to install uv automatically"
        echo "Please install uv manually: https://github.com/astral-sh/uv"
        exit 1
    fi
fi

# Check claude CLI
if command -v claude &> /dev/null; then
    write_success "claude CLI is installed"
else
    write_error "claude CLI is not installed"
    echo "Please install Claude Code CLI first: https://docs.anthropic.com/en/docs/claude-code"
    exit 1
fi

# ==============================================================================
# Step 2: Install project dependencies
# ==============================================================================
write_step "Step 2: Installing project dependencies..."

cd "$SCRIPT_DIR"
if uv sync; then
    write_success "Project dependencies installed"
else
    write_error "Failed to install dependencies"
    exit 1
fi

# ==============================================================================
# Step 3: Register MCP server
# ==============================================================================
write_step "Step 3: Registering MCP server..."

# Try to remove existing sccg MCP server if it exists
claude mcp remove sccg --scope user </dev/null 2>/dev/null && write_warning "Removed existing sccg MCP server" || true

# Ask user for installation method
echo ""
echo "Select installation method:"
echo "  1) Remote install (recommended) - Auto-fetches latest version from GitHub"
echo "  2) Local install - Uses current project directory (for development)"
read -p "Enter choice [1]: " INSTALL_METHOD
INSTALL_METHOD="${INSTALL_METHOD:-1}"

if [ "$INSTALL_METHOD" = "2" ]; then
    # Local install: use uv run from the project directory
    set +e
    LOCAL_OUTPUT=$(claude mcp add sccg --scope user --transport stdio -- uv run --directory "$SCRIPT_DIR" sccg-mcp </dev/null 2>&1)
    LOCAL_EXIT_CODE=$?
    set -e

    if [ $LOCAL_EXIT_CODE -eq 0 ]; then
        write_success "MCP server registered (local install from $SCRIPT_DIR)"
    else
        write_error "Failed to register MCP server (local install)"
        echo "Error details: $LOCAL_OUTPUT"
        exit 1
    fi
else
    # Remote install: existing logic with --refresh detection
    MCP_REGISTERED=false
    LAST_ERROR=""
    USE_REFRESH=false
    UV_VERSION_KNOWN=false

    UV_VERSION_OUTPUT=$(uv --version 2>&1) || true
    if [[ "$UV_VERSION_OUTPUT" =~ uv\ ([0-9]+)\.([0-9]+)\.([0-9]+) ]]; then
        UV_VERSION_KNOWN=true
        MAJOR="${BASH_REMATCH[1]}"
        MINOR="${BASH_REMATCH[2]}"
        # --refresh requires uv >= 0.4.0
        if [ "$MAJOR" -gt 0 ] || ([ "$MAJOR" -eq 0 ] && [ "$MINOR" -ge 4 ]); then
            USE_REFRESH=true
        fi
    fi

    if [ "$USE_REFRESH" = true ]; then
        # Try with --refresh first (disable set -e for this block)
        set +e
        REFRESH_OUTPUT=$(claude mcp add sccg --scope user --transport stdio -- uvx --refresh --from git+https://github.com/ZeroStarlet/Sisyphus-Coder-Codex-Gemini.git sccg-mcp </dev/null 2>&1)
        REFRESH_EXIT_CODE=$?
        set -e

        if [ $REFRESH_EXIT_CODE -eq 0 ]; then
            MCP_REGISTERED=true
            write_success "MCP server registered (with --refresh)"
        elif echo "$REFRESH_OUTPUT" | grep -qiE "(unknown|unrecognized|unexpected|invalid).*(option|flag|argument).*--refresh|--refresh.*(unknown|unrecognized|unexpected|invalid)"; then
            # Fallback: --refresh was rejected (covers various CLI error message formats), try without it
            write_warning "--refresh option was rejected, falling back to installation without --refresh..."
            set +e
            FALLBACK_OUTPUT=$(claude mcp add sccg --scope user --transport stdio -- uvx --from git+https://github.com/ZeroStarlet/Sisyphus-Coder-Codex-Gemini.git sccg-mcp </dev/null 2>&1)
            FALLBACK_EXIT_CODE=$?
            set -e
            if [ $FALLBACK_EXIT_CODE -eq 0 ]; then
                MCP_REGISTERED=true
                write_success "MCP server registered (without --refresh)"
            else
                LAST_ERROR="$FALLBACK_OUTPUT"
            fi
        else
            LAST_ERROR="$REFRESH_OUTPUT"
        fi
    else
        # uv version too old or unknown, skip --refresh
        if [ "$UV_VERSION_KNOWN" = true ]; then
            write_warning "Your uv version does not support --refresh option (requires uv >= 0.4.0)"
        else
            write_warning "Could not determine uv version, skipping --refresh option"
        fi
        write_warning "Installing without --refresh..."
        write_warning "Consider upgrading uv: curl -LsSf https://astral.sh/uv/install.sh | sh"

        set +e
        FALLBACK_OUTPUT=$(claude mcp add sccg --scope user --transport stdio -- uvx --from git+https://github.com/ZeroStarlet/Sisyphus-Coder-Codex-Gemini.git sccg-mcp </dev/null 2>&1)
        FALLBACK_EXIT_CODE=$?
        set -e
        if [ $FALLBACK_EXIT_CODE -eq 0 ]; then
            MCP_REGISTERED=true
            write_success "MCP server registered (without --refresh)"
        else
            LAST_ERROR="$FALLBACK_OUTPUT"
        fi
    fi

    if [ "$MCP_REGISTERED" = false ]; then
        write_error "Failed to register MCP server"
        echo "Error details: $LAST_ERROR"
        exit 1
    fi
fi

# ==============================================================================
# Step 4: Install Skills
# ==============================================================================
write_step "Step 4: Installing Skills..."

SKILLS_DIR="$HOME/.claude/skills"
SCCG_WORKFLOW_SOURCE="$SCRIPT_DIR/skills/sccg-workflow"
GEMINI_COLLAB_SOURCE="$SCRIPT_DIR/skills/gemini-collaboration"

# Create skills directory if it doesn't exist
if [ ! -d "$SKILLS_DIR" ]; then
    mkdir -p "$SKILLS_DIR"
    write_success "Created skills directory: $SKILLS_DIR"
fi

# Copy sccg-workflow skill
if [ -d "$SCCG_WORKFLOW_SOURCE" ]; then
    DEST="$SKILLS_DIR/sccg-workflow"
    rm -rf "$DEST"
    cp -r "$SCCG_WORKFLOW_SOURCE" "$DEST"
    write_success "Installed sccg-workflow skill"
else
    write_warning "sccg-workflow skill not found, skipping"
fi

# Copy gemini-collaboration skill
if [ -d "$GEMINI_COLLAB_SOURCE" ]; then
    DEST="$SKILLS_DIR/gemini-collaboration"
    rm -rf "$DEST"
    cp -r "$GEMINI_COLLAB_SOURCE" "$DEST"
    write_success "Installed gemini-collaboration skill"
else
    write_warning "gemini-collaboration skill not found, skipping"
fi

# ==============================================================================
# Step 5: Configure global CLAUDE.md
# ==============================================================================
write_step "Step 5: Configuring global CLAUDE.md..."

CLAUDE_MD_PATH="$HOME/.claude/CLAUDE.md"
SCCG_MARKER="# SCCG Configuration"
SCCG_CONFIG_PATH="$SCRIPT_DIR/templates/sccg-global-prompt.md"

# Create .claude directory if it doesn't exist
mkdir -p "$HOME/.claude"

if [ ! -f "$CLAUDE_MD_PATH" ]; then
    # Create new file with SCCG config
    if [ -f "$SCCG_CONFIG_PATH" ]; then
        cp "$SCCG_CONFIG_PATH" "$CLAUDE_MD_PATH"
        write_success "Created global CLAUDE.md"
    else
        write_warning "SCCG global prompt template not found at $SCCG_CONFIG_PATH"
        write_warning "Please manually copy the SCCG configuration to $CLAUDE_MD_PATH"
    fi
else
    # Check if SCCG config already exists
    if grep -qF "$SCCG_MARKER" "$CLAUDE_MD_PATH"; then
        write_warning "SCCG configuration already exists in CLAUDE.md, skipping"
    else
        # Append SCCG config
        if [ -f "$SCCG_CONFIG_PATH" ]; then
            echo "" >> "$CLAUDE_MD_PATH"
            cat "$SCCG_CONFIG_PATH" >> "$CLAUDE_MD_PATH"
            write_success "Appended SCCG configuration to CLAUDE.md"
        else
            write_warning "SCCG global prompt template not found at $SCCG_CONFIG_PATH"
            write_warning "Please manually copy the SCCG configuration to $CLAUDE_MD_PATH"
        fi
    fi
fi

# ==============================================================================
# Step 6: Configure Coder
# ==============================================================================
write_step "Step 6: Configuring Coder..."

CONFIG_DIR="$HOME/.sccg-mcp"
CONFIG_PATH="$CONFIG_DIR/config.toml"

# Create config directory if it doesn't exist
mkdir -p "$CONFIG_DIR"

# Check if config already exists
if [ -f "$CONFIG_PATH" ]; then
    write_warning "Config file already exists at $CONFIG_PATH"
    read -p "Overwrite? (y/N): " OVERWRITE
    if [ "$OVERWRITE" != "y" ] && [ "$OVERWRITE" != "Y" ]; then
        write_warning "Skipping Coder configuration"
        # Jump to Done
        echo ""
        echo -e "${GREEN}============================================================${NC}"
        write_success "SCCG setup completed successfully!"
        echo -e "${GREEN}============================================================${NC}"
        echo ""
        echo "Next steps:"
        echo "  1. Restart Claude Code CLI"
        echo "  2. Verify MCP server: claude mcp list"
        echo "  3. Check available skills: /sccg-workflow"
        echo ""
        exit 0
    fi
fi

# Prompt for API Token (hidden input)
read -s -p "Enter your API Token: " API_TOKEN
echo
if [ -z "$API_TOKEN" ]; then
    write_error "API Token is required"
    exit 1
fi

# Prompt for Base URL (optional)
read -p "Enter Base URL (default: https://open.bigmodel.cn/api/anthropic): " BASE_URL
if [ -z "$BASE_URL" ]; then
    BASE_URL="https://open.bigmodel.cn/api/anthropic"
fi

# Prompt for Model (optional)
read -p "Enter Model (default: glm-4.7): " MODEL
if [ -z "$MODEL" ]; then
    MODEL="glm-4.7"
fi

# Escape special characters for TOML string values (backslash and double quote)
SAFE_API_TOKEN=$(printf '%s' "$API_TOKEN" | sed 's/\\/\\\\/g; s/"/\\"/g')
SAFE_BASE_URL=$(printf '%s' "$BASE_URL" | sed 's/\\/\\\\/g; s/"/\\"/g')
SAFE_MODEL=$(printf '%s' "$MODEL" | sed 's/\\/\\\\/g; s/"/\\"/g')

# Generate config.toml
cat > "$CONFIG_PATH" << EOF
[coder]
api_token = "$SAFE_API_TOKEN"
base_url = "$SAFE_BASE_URL"
model = "$SAFE_MODEL"

[coder.env]
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"
EOF

# Set file permissions - only current user can read/write
chmod 600 "$CONFIG_PATH"

write_success "Coder configuration saved to $CONFIG_PATH"

# ==============================================================================
# Done!
# ==============================================================================
echo ""
echo -e "${GREEN}============================================================${NC}"
write_success "SCCG setup completed successfully!"
echo -e "${GREEN}============================================================${NC}"
echo ""

echo "Next steps:"
echo "  1. Restart Claude Code CLI"
echo "  2. Verify MCP server: claude mcp list"
echo "  3. Check available skills: /sccg-workflow"
echo ""
