# codex-mcp-cyber

> Claude + Codex 代码审查 MCP 服务器

## 项目定位

一个精简的 MCP 服务器——Claude 写代码，Codex 独立审核 + 复审。砍掉了 Coder 和 Gemini，只保留核心审查能力。

## 架构

```
Claude (Opus)  →  写代码、拆任务、做决策
Codex (OpenAI) →  独立审核 + 复审（read-only，唯一审核者）
```

- Claude 自己写代码，不再委托子代理执行
- Codex 是唯一审核者：初次审核和修复后复审全由 Codex 完成。Claude 不做初审
- 审查 → 修复 → 复审 → 循环至所有问题关闭

## 项目结构

```
codex-mcp-cyber/
├── src/codex_mcp_cyber/       # 源代码
│   ├── __init__.py
│   ├── cli.py                 # 入口点
│   ├── server.py              # MCP 服务器主体
│   └── tools/
│       ├── __init__.py
│       └── codex.py           # Codex 工具
├── skills/                    # Skills 工作流指导
│   └── cc-review/             # Claude + Codex 审查协作流程
├── pyproject.toml
├── setup.sh                   # Unix/macOS 安装脚本
├── setup.ps1                  # Windows PowerShell 安装脚本
├── setup.bat                  # Windows 批处理入口
├── README.md                  # 项目说明（中文）
├── README_EN.md               # 项目说明（英文）
└── CLAUDE.md                  # 本文件
```

## MCP 工具

### codex

调用 Codex CLI 进行代码审核。默认 `sandbox="read-only"`。

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
| return_all_messages | bool | - | `false` | 返回完整消息 |
| return_metrics | bool | - | `false` | 返回指标数据 |
| image | List[Path] | - | `null` | 附加图片 |
| yolo | bool | - | `false` | 跳过沙箱审批（慎用） |
| profile | string | - | `""` | 配置文件名称 |
| log_metrics | bool | - | `false` | 输出指标到 stderr |

**返回值：**

```json
// 成功
{
  "success": true,
  "tool": "codex",
  "SESSION_ID": "uuid-string",
  "result": "审核结论"
}

// 失败
{
  "success": false,
  "error": "错误摘要",
  "error_kind": "timeout | command_not_found | upstream_error | ...",
  "error_detail": { ... }
}
```

### 重试策略

- Codex 默认 1 次重试（只读操作无副作用）
- `command_not_found` 不重试（需用户安装 codex CLI）

## 协作流程

1. Claude 分析需求，执行代码改动
2. 改动完成后，Claude 获取 `git diff --no-color`
3. Claude 调用 codex 工具，将 diff 嵌入 PROMPT
4. Codex 独立审核，返回 ✅ / ⚠️ / ❌
5. ❌ / ⚠️ → Claude 修复 → Codex 复审 → 循环直到 ✅
6. ✅ → 合入 / 提交

详见 `skills/cc-review/SKILL.md`。

## 安装

```bash
# Unix/macOS
chmod +x setup.sh && ./setup.sh

# Windows
.\setup.bat
```

## 开发

```bash
# 克隆仓库
git clone https://github.com/ZeroStarlet/codex-mcp-cyber.git
cd codex-mcp-cyber

# 安装依赖
uv sync

# 本地调试
uv run codex-mcp-cyber
```

## 参考资源

- **Claude Code**: [Documentation](https://docs.anthropic.com/en/docs/claude-code)
- **Codex CLI**: [Documentation](https://developers.openai.com/codex/quickstart)
- **FastMCP**: [GitHub](https://github.com/jlowin/fastmcp) - MCP 框架

---

> 从 SCCG (Sisyphus-Coder-Codex-Gemini) 重构而来 · 2026-06-23
