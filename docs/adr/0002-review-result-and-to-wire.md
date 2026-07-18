# 2. ReviewResult 领域结局与 to_wire 出口

## Status

Accepted

## Context

ADR-0001 将审核执行加深为 `run_review(ReviewRequest, runner=None)`，并规定「出口再映射为旧 JSON」。实现里映射埋在 `run_review` 体内，函数直接返回冻结 wire dict；测试与调用方必须背下 `SESSION_ID` / `result` 等 wire 键。CONTEXT 已定义审核结局（ReviewResult），代码中却不存在对应类型。

可选方案：内部仍建结局但函数继续返回 wire（加深不彻底）；或 `run_review` 返回 `ReviewResult`，由壳层/`to_wire` 映射。

## Decision

- **`run_review` → `ReviewResult`**：单一 dataclass（成败同一类型）；领域字段用 `session_id` / `text` / `duration_ms` / `error_message`，不使用 wire 键名。
- **`to_wire(ReviewResult) → dict`**：住在 review 模块内；写入冻结 wire（含 `tool`、`SESSION_ID`、`result`、人类错误长文案）。
- **MCP 壳**（`tools/codex.py`）：`return to_wire(await run_review(req))`；不传 runner。
- **人类错误文案**（AUTH / INVALID_PATH 等）仅在 `to_wire` 生成；结局保留原始/归约消息。
- **metrics / all_messages**：按请求可选挂在结局上，再由 `to_wire` 输出。
- **测试**：主断言 `ReviewResult`；`to_wire` 锁成功 / 普通失败 / `command_not_found` 的精确键集合，以及 metrics、all_messages 可选键。
- **`command_not_found` 的 duration**：旧实现该分支 wire 无 `duration`（与其它失败路径不一致）。本决策将失败路径统一为始终含 `duration`（含 `command_not_found`），视为有意规范化，不是静默扩键。
- **本决策不包含**：AttemptOutcome 重试收口、双层 MCP 壳合并、改写既有成功/失败主键名（`success`/`tool`/`SESSION_ID`/`result`/`error`/`error_kind`/`error_detail`）。

## Consequences

- 领域回归不再绑定 wire 拼写；wire 变更面收在 `to_wire`。
- 直接调用 `run_review` 的代码（若有）需改为消费 `ReviewResult` 或再 `to_wire`。
- 依赖「`command_not_found` 无 `duration`」的客户端需容忍新增字段（宽松 JSON 通常无感）。
- 与 ADR-0001 互补：0001 定行流 seam 与 wire 冻结；本决策落实「出口映射」的真实 seam，并统一失败路径的 `duration`。
