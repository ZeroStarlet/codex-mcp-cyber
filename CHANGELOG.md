# 变更记录

## 0.4.0

### 破坏性变更（仓库内 API；wire 契约不变）

`codex` 工具的 15 个参数与返回字典（wire）**完全不变**；以下仅影响从包内模块导入的代码。

**行流归约收拢为终态感知单入口。**

```python
# 0.3.0：两段式 + 顺序约束（超时终局不得 finalize，靠调用方注释维持）
stream = reduce_codex_stream(lines, collect_messages=...)
stream = finalize_stream_outcome(stream, exit_code=...)

# 0.4.0：单入口，终态在实现内折叠
stream = reduce_codex_stream(process_outcome, collect_messages=...)
```

- `reduce_codex_stream` 改收 `ProcessOutcome`；`finalize_stream_outcome` 转为私有。
- 超时 → `ErrorKind` 的映射随之从 review 移入 stream；`review._AttemptOutcome`
  删除，`StreamOutcome` 增加 `exit_code` / `raw_output_lines`。
- `ProcessOutcome` / `Terminal` 定义移至 `stream`（行流词汇归行流模块）；
  `codex_mcp_cyber.process` 仍可导入同名对象（同一类型）。

**`cli.py` 删除。** `[project.scripts]` 直指 `codex_mcp_cyber.server:run`
（控制台命令名不变，重装后生效）。
**`tools/__init__.py` 不再再导出 `codex_tool`**（该导入路径全仓零消费者）；
请从 `codex_mcp_cyber.tools.codex` 导入。

**`review._build_cmd` → 公开接口 `review.build_codex_argv`。** 初审 / 复审的
argv 规则（复审 `resume <会话标识>` 缀于所有 flag 之后、`--image` 相对审核
别名等）是编码侧协议的单一来源，不再是私有函数。

### 命名（领域词对齐）

「工作目录」的两个所指分名：`ReviewResult.workdir` → `ReviewResult.real_workdir`
（真实仓库路径）；review 内部 `cd_path` / `codex_cd` → `real_workdir` /
`codex_workdir`（审核别名）。行流 seam 的 `workdir=` 参数名保持 0.3.0 契约不变。
CONTEXT.md 增补「真实仓库路径 / 审核别名」词条。

### 内部重构

- `errors.display_error`：错误人话文案（auth / invalid_path 修复指引）从 review
  移入 errors，与 `build_error_detail` 的 suggestion 同址 —— 「种类 → 人话」
  单一归属。
- `winsec.WinApiSecurity`：实现体并入方法、组合经 `self`（此前为模块函数 +
  纯转发方法对）。子类可按方法粒度替换叶子原语，测试不再 monkeypatch
  模块私有符号。
- `winlink.CACHE_ROOT_PREFIX`：ASCII 缓存根前缀提为具名常量，与文档互钉。
- `scripts/repro_os_error_123.py` 改用 `classify.looks_like_invalid_path_error`，
  不再自实现 123 特征判定。

### 文档与契约测试

- 修正 codex-guide.md / scenarios.md 的幽灵错误种类 `json_decode`（0.3.0 已删，
  文档未跟）；`last_lines` 行数说明 20 → 50；返回值示例补恒在的 `duration` 键；
  重试说明补 `invalid_path` 不重试。
- README / README_EN 手动安装命令补 `--refresh`，与 setup 脚本一致。
- 新增 `tests/test_contract_docs.py`：文档表述（参数表 / error_kind 表 /
  返回键 / 行数 / 缓存前缀 / `--refresh`）与代码单一来源互钉；版本号
  `pyproject` ↔ `__init__` 互钉 —— 漂移在 CI 变红，不在终审现场暴露。

### 版本口径

Python 包 0.3.0 → 0.4.0（包内 API 破坏性变更）；`plugin.json` 0.2.0 → 0.2.1
（本次触及 cc-review 技能文档）。

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
