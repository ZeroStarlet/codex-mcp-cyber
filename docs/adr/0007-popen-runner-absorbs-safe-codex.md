# 7. PopenCodexRunner 吸收 safe_codex_command

## Status

Accepted

## Context

ADR-0001 立下行流 seam：`CodexProcessRunner.run → ProcessOutcome`。生产路径却叠两层——
`safe_codex_command`（contextmanager，吐 `Generator[str]`）与 `PopenCodexRunner.run`
（再 `next` drain 成 list）。Popen 壳浅：interface 几乎等于「倒空 generator」；读者还要懂
`StopIteration.value` 打包 `(exit_code, raw)`。

仓库内无任何调用方 import `safe_codex_command`（仅 process 自身与 ADR-0006 文档提及）。
测试生产路径一律打 `PopenCodexRunner.run`（含 FakeProcess）。

ADR-0006 将谓词注入点写在 `safe_codex_command` 与 `PopenCodexRunner` 两处；吸收后注入点只剩后者。

## Decision

- **删除** `safe_codex_command`（模块级公开符号消失）。
- **加深** `PopenCodexRunner`：唯一对外 interface 仍是
  `run(cmd, *, prompt, timeout, max_duration) -> ProcessOutcome`，以及
  `__init__(is_terminal_line=None)`（ADR-0006 注入点）。
- **implementation 私有方法**（非 seam，不进 Protocol）：
  `_resolve_terminal_predicate`、`_resolve_codex_path`、`_spawn`、`_drain`、`_cleanup`。
- **直接灌 list**：不再经内部 generator / `StopIteration.value`。
- **行为冻结**：`GRACEFUL_SHUTDOWN_DELAY = 0.3`；谓词异常显式抛出；
  超时 → `CommandTimeoutError(partial_lines=…)`；`CommandNotFoundError` 文案；
  `raw_output_lines` = 非空行计数。
- **ScriptedLinesRunner / SequenceRunner / Protocol 不变**。
- **不改** wire、review、stream 归约、errors。
- **测试**：仍以 `PopenCodexRunner.run` 为 interface；不单测私有方法。

## Consequences

- 生产路径一眼是一个 deep adapter；AI/人类不再跳 contextmanager 双壳。
- ADR-0006 的注入点叙述收束为仅 `PopenCodexRunner(is_terminal_line=…)`。
- 若外部曾依赖 `safe_codex_command`（本仓库无），需改走 `PopenCodexRunner`。
- 超时单通道（ProcessOutcome 携带 terminal kind）未纳入；见 architecture review 候选 #2。
