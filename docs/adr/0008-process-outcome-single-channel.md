# 8. 行流 seam 单通道 ProcessOutcome

## Status

Accepted

## Context

ADR-0007 加深了 `PopenCodexRunner`，但 Protocol 纸面仍是 `→ ProcessOutcome`，真实 interface
却含 `raise CommandTimeoutError(partial_lines=…)`。`SequenceRunner` 必须会「抛」，
`_run_attempt` 两套归约（timeout 只 reduce / 正常 reduce+finalize）。
ADR-0007 Consequences 已 defer 本项。

## Decision

- **扩展 `ProcessOutcome`**：`terminal: "completed" | "timeout" | "idle_timeout"`，
  及 `error_message`。超时以 outcome 返回，**不** raise `CommandTimeoutError`。
- **`CommandNotFoundError` 仍 raise**（启动失败，未形成行流）。
- **`ScriptedLinesRunner` / `SequenceRunner`** 只吐 `ProcessOutcome`；
  Sequence 的 `steps` 类型收为 `list[ProcessOutcome]`。
- **`_run_attempt`**：读 `outcome.terminal`；timeout/idle 只 `reduce`、不 `finalize`（保留 ADR-0003）。
- **谓词抛 `CommandTimeoutError`**：Popen 收成 `terminal=idle_timeout|timeout` 的 outcome。
- **不改** wire / ReviewResult / MCP 壳。

## Consequences

- Protocol 与真实 interface 合拢；测试 adapter 不再演异常通道。
- `CommandTimeoutError` 仍存在（predicate 兼容、历史 import），但生产 `run` 路径不再向外抛它作为终态。
- 与 ADR-0003 互补：AttemptOutcome 仍是内部 seam。
