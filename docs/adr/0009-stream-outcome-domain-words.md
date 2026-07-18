# 9. StreamOutcome 领域词与事件解码收口

## Status

Accepted

## Context

归约出口字段仍叫协议名（`agent_messages` / `thread_id` / `err_message`），
`_run_attempt` 手写映射到 `text` / `session_id` / `error_message`——浅翻译层。
同时 `reduce_codex_stream` 内十余处类型守卫与领域 fold 缠在一起。

## Decision

- **`StreamOutcome` 字段对齐 CONTEXT**：`text`、`session_id`、`error_message`（及既有
  `had_error` / `error_kind` / `last_lines` / `all_messages` / `json_decode_errors`）。
- **删除** `agent_messages` / `thread_id` / `err_message` 公共名。
- **内部解码**：`_decode_line` → `_OkEvent | _JsonDecode | _Malformed`；
  `_fold_ok_event` 只处理 Ok；畸形 → `unexpected_exception`（行为冻结，含 `text is None`）。
- **`finalize_stream_outcome`** 改读新字段名；成功判定语义不变。
- **不导出** 解码类型；不写 CONTEXT 新词。

## Consequences

- 归约与 ReviewResult / Attempt 同词，零翻译。
- 协议形状变更集中在 `_decode_line` / `_fold_ok_event`。
