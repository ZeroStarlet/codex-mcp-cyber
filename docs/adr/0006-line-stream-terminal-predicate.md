# 6. 行流 early-stop 谓词注入

## Status

Accepted

## Context

生产 reader 在 `process._is_turn_completed` 内 `json.loads` 并判断 `type == "turn.completed"` 后 early-stop。
行流 seam（`CodexProcessRunner` / `ProcessOutcome`）叙事为「stdout 行 + exit」，但生产 adapter 嵌入 Codex 协议事件名。
stream 归约已拥有事件 type 知识；测试 ScriptedLines 永远不走 early-stop。

## Decision

- **默认谓词**放在 `stream.is_turn_completed_line(line: str) -> bool`（协议事件名只出现在 stream）。
- **注入点**：`PopenCodexRunner(is_terminal_line=None)`；
  默认 `None` 时绑定 `stream.is_turn_completed_line`。process 本体不再硬编码事件名。
  （历史曾同时挂在已删除的 `safe_codex_command` 上；ADR-0007 吸收后只保留本注入点。）
- **`CodexProcessRunner` Protocol 不变**；Scripted/Sequence 无感。
- **GRACEFUL_SHUTDOWN_DELAY = 0.3s** 行为保留。
- **谓词异常**：不得被 reader 的 I/O `except` 吞掉；经 queue 传回消费端并显式抛出，避免「截断却 success」。
- **测试**：谓词单测 + 默认绑定/注入的**运行路径**测（fake Popen）+ 谓词异常传播。
- **不写 CONTEXT**；不改 wire。

## Consequences

- process → stream 的默认 import 为接线代价；避免循环依赖（stream 不 import process）。
- 换 early-stop 策略可构造 `PopenCodexRunner(is_terminal_line=...)` 而不改 Protocol。
- 自定义谓词若抛异常，调用方将收到该异常（不再静默截断）。
- 删 early-stop（drain-to-EOF）未采纳；若未来 Codex 行为变化可再议。
- 生产 drain 实现细节见 ADR-0007（`PopenCodexRunner` 吸收原 contextmanager）。
