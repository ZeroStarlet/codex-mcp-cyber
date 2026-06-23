# codex-mcp-cyber

<div align="center">

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.12+-blue.svg)
![MCP](https://img.shields.io/badge/MCP-1.20.0+-green.svg)
![Status](https://img.shields.io/badge/status-beta-orange.svg)

[中文文档](README.md)

**Claude + Codex Code Review MCP Server**

Claude writes code. Codex reviews — independently, with re-review loops until pass.

[Quick Start](#-quick-start) • [Features](#-core-features) • [Architecture](#-architecture) • [Tool](#️-tool) • [Install](#-install)

</div>

---

## 🌟 Core Features

| Dimension | Description |
| :--- | :--- |
| **🔍 Independent Review** | Codex as the sole reviewer, providing objective third-party code review |
| **🔄 Re-review Loop** | Review → Fix → Re-review → Loop, every issue must be closed |
| **🛡️ Read-only Safety** | Codex defaults to `read-only`, never modifies code |
| **⚡ Zero Config** | No backend API configuration needed — Codex uses its own auth |
| **📊 Observability** | Metrics support (timing, token usage) with JSONL output |

## 🤖 Architecture

```
Claude (Opus)  →  Write code, decompose tasks, make decisions
Codex (OpenAI) →  Independent review + re-review (read-only)
```

Claude writes all code directly — no sub-agent delegation. Codex is the **sole reviewer**: both initial review and all re-reviews after fixes are done by Codex alone. Claude does not perform preliminary review.

## 🛠️ Tool

### codex

Invokes Codex CLI for code review.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| PROMPT | string | ✅ | - | Review task description, should include git diff |
| cd | Path | ✅ | - | Working directory |
| sandbox | string | - | `read-only` | Must be read-only |
| SESSION_ID | string | - | `""` | Session reuse |
| skip_git_repo_check | bool | - | `true` | Allow non-Git repos |
| timeout | int | - | `300` | Idle timeout seconds |
| max_duration | int | - | `1800` | Max total duration seconds |
| max_retries | int | - | `1` | Max retries |
| model | string | - | `""` | Model override |

**Return value:**

```json
// ✅ Pass
{
  "success": true,
  "tool": "codex",
  "SESSION_ID": "uuid-string",
  "result": "Review conclusion"
}

// ❌ Needs changes
{
  "success": false,
  "error": "Error summary",
  "error_kind": "timeout | command_not_found | upstream_error | ...",
  "error_detail": {
    "message": "Brief description",
    "exit_code": 1,
    "last_lines": ["Last 20 lines..."],
    "retries": 1
  }
}
```

**error_kind values:**

| Value | Meaning |
|-------|---------|
| `idle_timeout` | Idle timeout (no output) |
| `timeout` | Total duration timeout |
| `command_not_found` | codex CLI not installed |
| `upstream_error` | CLI returned error |
| `json_decode` | JSON parse failure |
| `empty_result` | No response content |
| `unexpected_exception` | Unexpected exception |

### Retry Strategy

- Default **1 automatic retry** (read-only, no side effects)
- Timeouts and network errors retry automatically
- `command_not_found` does not retry (requires user to install codex CLI)
- Exponential backoff: 0.5s → 1s → 2s

## 📋 Workflow

1. Claude analyzes requirements and writes code
2. Claude captures changes:

   ```bash
   git diff --no-color
   ```

3. Claude calls the `codex` tool, embedding the diff in PROMPT
4. **Codex independently reviews**, returns: ✅ Pass / ⚠️ Suggestions / ❌ Needs changes
5. ❌ / ⚠️ → Claude fixes → **Codex re-reviews** → loop until ✅
6. ✅ → Merge / Commit

> Detailed workflow: [`skills/cc-review/SKILL.md`](skills/cc-review/SKILL.md)

## 🔧 Install

### Prerequisites

- **Python 3.12+**
- **uv** (Python package manager) — `pip install uv`
- **Claude Code CLI** — `npm install -g @anthropic-ai/claude-code`
- **Codex CLI** — `npm install -g @openai/codex`

### One-Click Setup

**Windows:**

```powershell
git clone https://github.com/ZeroStarlet/codex-mcp-cyber.git
cd codex-mcp-cyber
.\setup.bat
```

**macOS / Linux:**

```bash
git clone https://github.com/ZeroStarlet/codex-mcp-cyber.git
cd codex-mcp-cyber
chmod +x setup.sh && ./setup.sh
```

### Manual Install

```bash
# 1. Clone
git clone https://github.com/ZeroStarlet/codex-mcp-cyber.git
cd codex-mcp-cyber

# 2. Install dependencies
uv sync

# 3. Register MCP server (remote install, auto-fetches latest)
claude mcp add codex-mcp-cyber --scope user --transport stdio -- uvx --from git+https://github.com/ZeroStarlet/codex-mcp-cyber.git codex-mcp-cyber

# Or local install (for development)
claude mcp add codex-mcp-cyber --scope user --transport stdio -- uv run --directory . codex-mcp-cyber
```

### Uninstall

**Windows:** `.\uninstall.bat`

**macOS / Linux:** `chmod +x uninstall.sh && ./uninstall.sh`

Or manually:

```bash
claude mcp remove codex-mcp-cyber --scope user
```

## 🧑‍💻 Development

```bash
git clone https://github.com/ZeroStarlet/codex-mcp-cyber.git
cd codex-mcp-cyber

# Install dependencies
uv sync

# Run locally
uv run codex-mcp-cyber
```

## 📚 References

- **Claude Code**: [Documentation](https://docs.anthropic.com/en/docs/claude-code)
- **Codex CLI**: [Documentation](https://developers.openai.com/codex/quickstart)
- **FastMCP**: [GitHub](https://github.com/jlowin/fastmcp) - MCP framework

## 📄 License

MIT

---

> Refactored from SCCG (Sisyphus-Coder-Codex-Gemini) · 2026-06-23
> Removed Coder and Gemini, focused on Claude + Codex review collaboration
