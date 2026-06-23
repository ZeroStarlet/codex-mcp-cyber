# codex-mcp-cyber

<div align="center">

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.12+-blue.svg)
![MCP](https://img.shields.io/badge/MCP-1.20.0+-green.svg)
![Status](https://img.shields.io/badge/status-beta-orange.svg)

[English](README_EN.md)

**Claude + Codex 代码审查 MCP 服务器**

Claude 写代码，Codex 独立审核 + 复审，循环至通过。

[快速开始](#-快速开始) • [核心特性](#-核心特性) • [架构](#-架构) • [工具详解](#️-工具详解) • [安装](#-安装)

</div>

---

## 🌟 核心特性

| 维度 | 说明 |
| :--- | :--- |
| **🔍 独立审核** | Codex 作为唯一审核者，提供客观的第三方代码审查 |
| **🔄 复审闭环** | 审核 → 修复 → 复审 → 循环，所有问题必须关闭 |
| **🛡️ 安全只读** | Codex 默认 `read-only`，绝不修改代码 |
| **⚡ 零配置** | 无需配置后端 API——Codex 使用自身认证体系 |
| **📊 可观察性** | 支持指标采集（耗时、token 用量），JSONL 格式输出 |

## 🤖 架构

```
Claude (Opus)  →  写代码、拆任务、做决策
Codex (OpenAI) →  独立审核 + 复审（read-only）
```

Claude 自己写代码，不再委托子代理。Codex 是唯一审核者——初次审核和修复后复审全由 Codex 独立完成，Claude 不做初审。

## 🛠️ 工具详解

### codex

调用 Codex CLI 进行代码审核。

**参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| PROMPT | string | ✅ | - | 审核任务描述，建议包含 git diff |
| cd | Path | ✅ | - | 工作目录 |
| sandbox | string | - | `read-only` | 必须 read-only |
| SESSION_ID | string | - | `""` | 会话复用 |
| skip_git_repo_check | bool | - | `true` | 允许非 Git 仓库 |
| timeout | int | - | `300` | 空闲超时秒数 |
| max_duration | int | - | `1800` | 总时长上限秒数 |
| max_retries | int | - | `1` | 最大重试次数 |
| model | string | - | `""` | 指定模型 |

**返回值：**

```json
// ✅ 通过
{
  "success": true,
  "tool": "codex",
  "SESSION_ID": "uuid-string",
  "result": "Codex 审核结论"
}

// ❌ 需要修改
{
  "success": false,
  "error": "错误摘要",
  "error_kind": "timeout | command_not_found | upstream_error | ...",
  "error_detail": {
    "message": "错误简述",
    "exit_code": 1,
    "last_lines": ["最后20行输出..."],
    "retries": 1
  }
}
```

**error_kind 枚举：**

| 值 | 说明 |
|----|------|
| `idle_timeout` | 空闲超时（无输出） |
| `timeout` | 总时长超时 |
| `command_not_found` | codex CLI 未安装 |
| `upstream_error` | CLI 返回错误 |
| `json_decode` | JSON 解析失败 |
| `empty_result` | 无响应内容 |
| `unexpected_exception` | 未预期异常 |

### 重试策略

- 默认 **1 次自动重试**（只读操作无副作用）
- 超时、网络错误会自动重试
- `command_not_found` 不重试（需用户安装 codex CLI）
- 指数退避：0.5s → 1s → 2s

## 📋 协作流程

1. Claude 分析需求，执行代码改动
2. 改动完成后 Claude 获取变更摘要：

   ```bash
   git diff --no-color
   ```

3. Claude 调用 `codex` 工具，将 diff 嵌入 PROMPT
4. **Codex 独立审核**，返回结论：✅ 通过 / ⚠️ 建议优化 / ❌ 需要修改
5. ❌ / ⚠️ → Claude 修复问题 → **Codex 复审** → 循环直到 ✅
6. ✅ → 合入 / 提交

> 详细工作流见 [`skills/cc-review/SKILL.md`](skills/cc-review/SKILL.md)

## 🔧 安装

### 前置条件

- **Python 3.12+**
- **uv**（Python 包管理器）— `pip install uv`
- **Claude Code CLI** — `npm install -g @anthropic-ai/claude-code`
- **Codex CLI** — `npm install -g @openai/codex`

### 一键安装

**Windows：**

```powershell
git clone https://github.com/ZeroStarlet/codex-mcp-cyber.git
cd codex-mcp-cyber
.\setup.bat
```

**macOS / Linux：**

```bash
git clone https://github.com/ZeroStarlet/codex-mcp-cyber.git
cd codex-mcp-cyber
chmod +x setup.sh && ./setup.sh
```

### 手动安装

```bash
# 1. 克隆
git clone https://github.com/ZeroStarlet/codex-mcp-cyber.git
cd codex-mcp-cyber

# 2. 安装依赖
uv sync

# 3. 注册 MCP 服务器（远程安装，自动获取最新版）
claude mcp add codex-mcp-cyber --scope user --transport stdio -- uvx --from git+https://github.com/ZeroStarlet/codex-mcp-cyber.git codex-mcp-cyber

# 或者本地安装（开发调试用）
claude mcp add codex-mcp-cyber --scope user --transport stdio -- uv run --directory . codex-mcp-cyber
```

### 卸载

**Windows：** `.\uninstall.bat`

**macOS / Linux：** `chmod +x uninstall.sh && ./uninstall.sh`

或手动执行：

```bash
claude mcp remove codex-mcp-cyber --scope user
```

## 🧑‍💻 开发

```bash
git clone https://github.com/ZeroStarlet/codex-mcp-cyber.git
cd codex-mcp-cyber

# 安装依赖
uv sync

# 本地调试运行
uv run codex-mcp-cyber
```

## 📚 参考资源

- **Claude Code**: [Documentation](https://docs.anthropic.com/en/docs/claude-code)
- **Codex CLI**: [Documentation](https://developers.openai.com/codex/quickstart)
- **FastMCP**: [GitHub](https://github.com/jlowin/fastmcp) - MCP 框架

## 📄 License

MIT

---
