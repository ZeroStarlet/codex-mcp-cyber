# 变更记录

## 0.6.0

### 契约：失败结局携带会话标识（wire 新增键）

**结论先行**：失败 wire 现在恒含 `SESSION_ID`（未建会话为 null）。
0.5.1 实战教训：初审失败丢会话 → 复审被迫 `""` 重建并手工附摘要。
已建会话的失败（如 upstream_error）如今返回其会话标识，复审直接
resume；技能文档「会话丢失」降为 null 时的边缘预案。成功 wire 不变；
失败键集 6 → 7（test_review_run 键集钉与 codex-guide 示例同步）。

### 架构深化（2026-07-21 走查四项；interface 均不破坏 wire）

- **行流证据通道类型化**：错误文本统一经 `_note_error_text` 进展示通道并
  即时抽取 123 分类证据（`saw_invalid_path_text`）；finalize 只读字段，
  不再对自己拼的 error_message 正则回扫——0.5.1 事故的机制性根除。
  新增行为钉：fail 事件携 123 且无会话 → invalid_path。
- **workdir→cwd 单一来源**：`run_review` 用 format_cli_path 一次算出
  `cli_workdir`，同一字符串既进 argv `--cd` 也穿 runner seam；生产
  adapter 原样用作 Popen cwd（不再私自二次格式化）。`CodexProcessRunner`
  interface 升为成品字符串契约，并补写超时 / 异常模式（此前只在实现里
  可见）。新增钉：seam 收到的 workdir 与 `--cd` 逐字同串；Popen cwd 与
  传入 workdir 逐字相等。`build_codex_argv` 增必填 `cli_workdir`
  （仓库内 API）。
- **诊断窗口单一算法**：`redact.tail_window` 成为保尾 50 行的唯一实现，
  stream 折叠期与 filter_last_lines 均调用之（此前两套机制只共享常量）。
- **删除 `path_has_non_ascii`**：零生产调用的残留探针（绑定 0.5.0 已移除
  机制）连同其断言删除；repro 脚本改内联判断。
- **Codex 初审采纳（⚠ 两项）**：`tail_window` 非正窗口按零窗口返回空列表
  （`[-0:]` 切片陷阱防护）并补边界钉（>50 行保尾、畸形 break 排尾、
  非正窗口）；`CodexProcessRunner` 文档补 0.6.0 第三方 adapter 迁移影响，
  测试 runner 注解同步收窄为 `str | None`。
- **runner 深生命周期可测化**（第二轮走查 Top 1）：`PopenCodexRunner`
  增内部时钟 seam（``clock`` 注入，默认真实时钟；生产行为零改动），
  配可抛 TimeoutExpired / 无视 terminate 的测试进程——墙钟超时、空闲
  超时、``wait`` 超时 terminate→kill 升级、``_cleanup`` kill 升级四簇
  「出错即杀进程」分支首次有回归网。另补 metrics 层
  ``raw_output_lines`` / ``json_decode_errors`` 实值断言（第二轮走查
  Top 2：关掉散标量漏传的静默错数据窗口）。

### 版本口径

Python 包 0.5.1 → 0.6.0（wire 失败分支新增键 + 仓库内 API 变更）；
`plugin.json` 0.2.3 → 0.2.4（技能文档随 SESSION_ID 语义更新）。

## 0.5.1

### 修复：子工具 123 噪音不再盖掉成功审查

**结论先行**：`invalid_path` 现在只可能出现在**未取得会话标识**的运行上。
生产事故（2026-07-20）：审查实际完成（thread_id + agent_message +
turn.completed），但 Codex 子工具（rg 通配）向合流 stdout 吐了含
「os error 123」的纯文本行——行流折叠期按文本特征立即定罪 invalid_path，
失败终局不可重试，wire 丢 SESSION_ID 与整份结论。

- `stream.py`：JSON 解码失败行不再在折叠期定罪；123 文本特征的
  invalid_path 判定收敛到 finalize，且仅当行流**最终无会话标识**时成立。
  已建会话的运行里，123 文本只计入诊断（json_decode_errors / last_lines）。
- 回归钉子：stream 层
  （`test_reduce_subtool_123_noise_with_session_stays_success`）与
  review→wire 层（`test_subtool_123_noise_does_not_mask_successful_review`）。
- 会话证据强化（Codex 初审意见）：`thread_id` 仅认**非空白字符串**——空串 /
  纯空白视同未建会话（复审无法 resume），非字符串按畸形事件归
  unexpected_exception；
  另钉住「已建会话 + turn.failed 携 123 → upstream_error 可重试」契约
  （stream 层 + review 层重试用例）。
- 真实 workdir 非法（无会话）的判定与不可重试语义不变。

### 技能：cc-review 实战摩擦点制度化

六轮实战沉淀进文档（均为增量，六步闭环结构不变）：

- **大 diff 走文件**：超约 2 万 token 的 diff 写入 `.scratch/review-<日期>.diff`，
  PROMPT 给路径并要求 Codex 与实时 diff 交叉核对（checklist + examples 场景 6）。
- **自测证据**成为标准 PROMPT 字段：Codex 只读沙箱通常起不了全量测试，
  测试证据由 Claude 提供；需其亲验时给定向单测命令（guide 使用规范 5）。
- **复审收敛**：逐条处置表（已修 / 反驳 / 推迟）随修复 diff 进复审 PROMPT；
  可引用 Codex 上轮自设标准。
- **3 轮闸**：明确用户授权「继续审到 PASS」可超闸续审（SKILL / scenarios /
  examples / 双份 README 全同步，并新增契约钉：凡写 3 轮上限须同行带授权例外）。
- description 触发面加宽（送审 / 审一下 / 合入前把关）；SESSION_ID 场景表
  收敛为 SKILL.md 单一来源；guide 补充「invalid_path 仅见于未建会话运行」。

### 版本口径

Python 包 0.5.0 → 0.5.1（行为修复，wire 契约不变）；`plugin.json`
0.2.2 → 0.2.3（技能文档增强）。

## 0.5.0

### 移除：ASCII 目录联接层（winlink / winsec）

**结论先行**：os error 123 的元凶是 `cd` 带**字面引号**（早已由
normalize_workdir 修复，见 59bf80e）。「中文路径下 Codex 内部工具必然 123」
未能在当前环境复现——2026-07-20 直通实验：`codex exec --cd
C:\Users\Starlet\Desktop\审查\codex-mcp-cyber`（裸中文路径、目录无 8.3 别名、
不经联接），rg 与文件读取全部 exit 0，回合正常完成。据此移除整个联接防御层：

- 删除 `winlink.py` / `winsec.py` / `tests/winsec_fake.py` 及全部联接 / ACL
  测试（约 1100 行）；不再在系统盘根创建 `C:\codex-mcp-cyber-v3-<sidhash>`
  缓存树。
- `run_review` 把归一后的工作目录**原样**交给 runner（argv `--cd` 与 Popen
  cwd 同一路径）；报错与领域结局恢复真实路径，不再出现别名。
- 命名收敛：0.4.0 的 `ReviewResult.real_workdir` → `workdir`（「审核别名」
  概念随机制一并消亡，CONTEXT.md 词条同步收敛）；`errors.display_error`
  参数同名调整；invalid_path 文案删除联接指引。
- 新增回归钉子：裸中文路径归一直通
  （`test_normalize_non_ascii_workdir_passes_through`）、中文 workdir 原样
  穿过 runner seam（`test_non_ascii_workdir_reaches_seam_unaliased`）；
  契约测试改为断言技能文档**不再**引用联接层。
- 行流 seam 的 `workdir=` 参数与 MCP wire 契约（15 参数 / 返回字典）不变。

**遗留清理**：旧版创建过的 `C:\codex-mcp-cyber-v3-*` 目录可手动删除。
**兜底**：若个别环境仍遇 123，把仓库放到纯英文路径即可绕过；旧实现见
0.4.0 git 历史。

### 版本口径

Python 包 0.4.0 → 0.5.0（删除公开模块，破坏性）；`plugin.json` 0.2.1 → 0.2.2
（技能文档随移除更新）。

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
