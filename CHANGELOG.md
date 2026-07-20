# 变更记录

## 0.3.0

### 破坏性变更

**`CodexProcessRunner.run()` 新增 `workdir` 参数。**

```python
# 0.2.0
def run(self, cmd, *, prompt, timeout, max_duration) -> ProcessOutcome: ...

# 0.3.0
def run(self, cmd, *, prompt, workdir=None, timeout, max_duration) -> ProcessOutcome: ...
```

`run_review()` 现在无条件传入 `workdir=`，因此**未声明该参数的自定义 adapter 会抛
`TypeError`**。这是有意为之，不提供兼容层：

在 0.2.0 里 `workdir` 是 `PopenCodexRunner` 的实例字段，`run_review` 靠
`isinstance(runner, PopenCodexRunner)` 认出具体 adapter 后直接赋值。后果是任何其它
adapter 都**静默**收不到子进程 cwd —— 而 cwd 正是 Windows 非 ASCII 路径下 ASCII
目录联接生效的关键。签名探测式的兼容层会把这个静默失效原样保留，所以这里选择在
seam 处响亮失败。

**迁移**：给自定义 runner 的 `run()` 加上 `workdir: Path | str | None = None` 关键字参数。

**`PopenCodexRunner(workdir=...)` 构造参数已移除。** workdir 现在只经 `run()` 传入，
避免同一语义存在构造期与调用期两个来源。`is_terminal_line` 仍为构造参数。

### 版本口径

本次只升 Python 包版本（`pyproject.toml` / `__init__.py`）到 `0.3.0`。
`.claude-plugin/plugin.json` **有意保留 `0.2.0`** —— 它版本化的是 cc-review skill
（提示词与文档），本次改动未触及 `skills/` 与 `.claude-plugin/`，跟着升版会是假信号。
二者从此按各自内容独立版本化，不锁步。

### 内部重构

`errors.py`（1035 行）按关切拆分，公开的 MCP 工具契约（`codex` 工具的 15 个参数与
返回字典）未变：

| 模块 | 职责 |
|------|------|
| `errors` | 异常类型、`ErrorKind`、`build_error_detail` |
| `classify` | 错误种类判定（文本特征、Windows errno、可重试性） |
| `redact` | 行流事件脱敏 |
| `paths` | 工作目录归一与 CLI 路径格式化 |
| `winsec` | Windows 安全原语 + `WinSecurity` seam |
| `winlink` | ASCII 目录联接 |

若此前从 `codex_mcp_cyber.errors` 导入过 `normalize_workdir`、`format_cli_path`、
`path_has_non_ascii`、`prefer_codex_workdir`、`redact_tool_result_event`、
`filter_last_lines`、`is_auth_error`、`looks_like_invalid_path_error`、
`is_retryable_error`，请改为从上表对应模块导入。

`ErrorKind.JSON_DECODE` 已移除（全仓无一处赋值）。
`CommandTimeoutError` 的 `partial_lines` / `raw_output_lines` 构造参数已移除（无人读取）。

### 安全

`_create_windows_junction` 此前用裸 `mkdir` 创建最终交给 Codex 的联接目录，绕开了
`create_private_dir_atomic` 的 owner 校验与 protected DACL。现改为与缓存树同一套策略；
父目录缺失时 fail-closed，不再 `mkdir(parents=True)` 顺手创建未经校验的缓存根。
