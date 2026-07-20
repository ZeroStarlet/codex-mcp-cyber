<div align="center">

# codex-mcp-cyber

**Claude 写码 · Codex 只读终审**

审核 → 修复 → 复审，直到通过。

<br/>

[![License](https://img.shields.io/badge/license-MIT-0B5FFF?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.20.0+-10B981?style=flat-square)](https://modelcontextprotocol.io/)
[![Status](https://img.shields.io/badge/status-beta-F59E0B?style=flat-square)](#)

<br/>

[English](README_EN.md)
&nbsp;·&nbsp;
[快速开始](#-快速开始)
&nbsp;·&nbsp;
[协作流程](#-协作流程)
&nbsp;·&nbsp;
[工具](#️-工具-codex)
&nbsp;·&nbsp;
[安装](#-安装)

</div>

---

## 为什么需要它

| 角色 | 职责 | 边界 |
|:----:|:-----|:-----|
| **Claude** | 需求分析 · 写码 · 自测 · 按意见修复 | 工程主体 |
| **Codex** | 独立 Code Review，产出 ✅ / ⚠️ / ❌ | **只审不改**（`read-only`） |

```mermaid
flowchart LR
  A[Claude 写码自测] --> B[git diff]
  B --> C[Codex 只读终审]
  C -->|❌ CHANGE| D[Claude 逐条修复]
  D --> E[复审 · 复用 SESSION_ID]
  E -->|仍 ❌| D
  E -->|✅ PASS| F[合入 / 提交]
  C -->|✅ / ⚠️| F
```

> 复审必须复用同一 `SESSION_ID`；同一改动最多 **3 轮**，否则抛人工裁决。

### 两件套

| 组件 | 作用 | 怎么装 |
|:-----|:-----|:-------|
| 🔌 **插件** `codex-mcp-cyber@zerostarlet` | `cc-review` skill（协作闭环） | Claude Code plugin |
| 🧰 **MCP** `codex-mcp-cyber` | `codex` 工具（调本机 Codex CLI） | setup / `claude mcp add` |

本机还需 **Codex CLI**：

```bash
npm i -g @openai/codex && codex login
```

---

## 🚀 快速开始

### ① 安装 skill 插件

```bash
claude plugin marketplace add ZeroStarlet/codex-mcp-cyber
claude plugin install codex-mcp-cyber@zerostarlet
```

<details>
<summary><b>本地仓库调试插件</b></summary>

```bash
claude plugin marketplace add /path/to/codex-mcp-cyber
claude plugin install codex-mcp-cyber@zerostarlet
```

</details>

### ② 安装 MCP

<table>
<tr>
<td width="50%">

**Windows**

```powershell
git clone https://github.com/ZeroStarlet/codex-mcp-cyber.git
cd codex-mcp-cyber
.\setup.bat
```

</td>
<td width="50%">

**macOS / Linux**

```bash
git clone https://github.com/ZeroStarlet/codex-mcp-cyber.git
cd codex-mcp-cyber
chmod +x setup.sh && ./setup.sh
```

</td>
</tr>
</table>

### ③ 验证

1. 重启 **Claude Code**
2. `claude mcp list` → 出现 `codex-mcp-cyber`
3. skill 列表 → 出现 **`cc-review`**

---

## 📋 协作流程

推荐使用插件 skill **`cc-review`**：

| 步骤 | 动作 |
|:----:|:-----|
| **1** | Claude 拆需求、写码、自测 |
| **2** | `git diff --no-color`，按 [审查清单](skills/cc-review/review-checklist.md) 组 PROMPT |
| **3** | 调用 `codex` · `sandbox=read-only` · 初审 `SESSION_ID=""` |
| **4** | Codex → ✅ 通过 · ⚠️ 可合入建议 · ❌ 必须改 |
| **5** | ❌ → 逐条修 → **复审复用 `SESSION_ID`** → 最多 3 轮 |
| **6** | ✅ → 合入 / 提交 |

完整规则（含强制送审）：[`skills/cc-review/SKILL.md`](skills/cc-review/SKILL.md)

---

## 🛠️ 工具 `codex`

调用本机 Codex CLI，**默认且建议始终只读**。

### 参数

| 参数 | 类型 | 必填 | 默认 | 说明 |
|:-----|:-----|:----:|:-----|:-----|
| `PROMPT` | string | ✅ | — | 须含 **diff + 完整审查清单** |
| `cd` | string | ✅ | — | 工作目录 **裸路径**（勿加引号） |
| `sandbox` | string | | `read-only` | 审核场景必须只读 |
| `SESSION_ID` | string | | `""` | 初审空；复审传上一轮返回值 |
| `timeout` | int | | `300` | 空闲超时（秒） |
| `max_duration` | int | | `1800` | 总时长上限（秒） |
| `max_retries` | int | | `1` | 工具层自动重试 |

### 返回

| 成功 | 失败 |
|:-----|:-----|
| `success` · `SESSION_ID` · `result` | `error` · `error_kind` · `error_detail` |

`error_kind` 常见值：`auth_required` · `invalid_path` · `command_not_found` · `timeout` / `idle_timeout` · `upstream_error`

完整契约 → [`codex-guide.md`](skills/cc-review/codex-guide.md)

> **Windows**  
> `cd` 不要包字面引号。中文 / 非 ASCII 路径下，MCP 会尝试建立 ASCII 目录联接，降低 Codex 内部 `os error 123`。

---

## 📦 安装

### 前置条件

| 依赖 | 说明 |
|:-----|:-----|
| Python | **3.12+** |
| uv | [astral.sh/uv](https://github.com/astral-sh/uv) |
| Claude Code CLI | `npm i -g @anthropic-ai/claude-code` |
| Codex CLI | `npm i -g @openai/codex` → `codex login` |

<details>
<summary><b>MCP 手动安装（远程 / 本地开发）</b></summary>

```bash
git clone https://github.com/ZeroStarlet/codex-mcp-cyber.git
cd codex-mcp-cyber
uv sync

# 远程（自动拉最新；--refresh 与 setup 脚本一致，避免 uvx 用旧缓存）
claude mcp add codex-mcp-cyber --scope user --transport stdio -- \
  uvx --refresh --from git+https://github.com/ZeroStarlet/codex-mcp-cyber.git codex-mcp-cyber

# 本地开发
claude mcp add codex-mcp-cyber --scope user --transport stdio -- \
  uv run --directory . codex-mcp-cyber
```

</details>

<details>
<summary><b>卸载</b></summary>

| 组件 | 命令 |
|:-----|:-----|
| MCP · Windows | `.\uninstall.bat` |
| MCP · Unix | `./uninstall.sh` |
| MCP · 手动 | `claude mcp remove codex-mcp-cyber --scope user` |
| 插件 | `claude plugin uninstall codex-mcp-cyber@zerostarlet` |

skill **只通过插件**分发，不要拷到 `~/.claude/skills/cc-review`（会与插件双载）。

</details>

---

## 🧑‍💻 开发

```bash
git clone https://github.com/ZeroStarlet/codex-mcp-cyber.git
cd codex-mcp-cyber
uv sync
uv run codex-mcp-cyber
```

| 文档 | 内容 |
|:-----|:-----|
| [`CONTEXT.md`](CONTEXT.md) | 领域词 |
| [`skills/cc-review/`](skills/cc-review/) | 协作 skill 源码 |

---

<div align="center">

### 参考

[Claude Code](https://docs.anthropic.com/en/docs/claude-code)
&nbsp;·&nbsp;
[Codex CLI](https://developers.openai.com/codex/quickstart)
&nbsp;·&nbsp;
[FastMCP](https://github.com/jlowin/fastmcp)

<br/>

**MIT License**

</div>
