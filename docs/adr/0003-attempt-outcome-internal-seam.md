# 3. AttemptOutcome 内部 seam

## Status

Accepted

## Context

ADR-0001 / 0002 已立住行流 seam 与 `run_review → ReviewResult → to_wire`。
`run_review` 的 while 循环仍用十余个 `attempt_*` 局部变量 + `last_error` dict 五键手写拷贝维持「超时不得泄漏上一轮 exit_code」等不变量；已有回归锁住该类 bug，但实现 locality 差。

ADR-0002 将本项排除在范围外，未禁止。

## Decision

- **`AttemptOutcome`**：一次行流尝试的内部结局（成功 / 普通失败 / 超时同型）；**不**导出为包公共 interface，**不**写入 CONTEXT 词表。
- **`_run_attempt(...)`**：执行 `runner.run` + 归约，返回 `AttemptOutcome`。
  - 超时：`CommandTimeoutError` 在 `_run_attempt` 内转为失败结局；partial 只 `reduce_codex_stream`，**不** `finalize`（保持 timeout kind 不被 protocol_missing 盖住）。
  - `CommandNotFoundError`：**不**收进 AttemptOutcome；向上抛出，由 `run_review` 直接终局（不重试）。
- **重试循环**：只保留 `last: AttemptOutcome | None`；失败终局从 `last` 建 `ReviewResult`。删除 `last_error` dict 与 attempt_* 变量云。
- **对外不变**：`run_review` / `ReviewResult` / `to_wire` / MCP wire 键与语义不变。

## Consequences

- 泄漏类状态机 bug 的修改面收在 `_run_attempt` 与循环读 `AttemptOutcome` 两处。
- 测试仍以 `run_review` 为 interface（含超时不泄漏回归）；不强制单测 `_run_attempt`。
- 与 0001/0002 互补，不改 wire、不新建子包。
