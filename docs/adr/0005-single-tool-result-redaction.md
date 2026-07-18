# 5. 单一 tool_result 脱敏

## Status

Accepted

## Context

`tool_result` 大内容截断在两处重复实现：
- `stream.reduce_codex_stream` 收集 `all_messages` 时 deepcopy 后改 `item.content`
- `errors.filter_last_lines` 为 `error_detail.last_lines` 再 json.loads 后同样截断

规则相同（`item.type == "tool_result"` → `content = "[truncated]"`），但 locality 分裂；改策略易漏一侧，且无测试锁定。

ADR-0004 将本项列为后续、未禁止。

## Decision

- **`redact_tool_result_event(event: dict) -> dict`** 住在 `errors.py`：对已解析 JSON 事件做 deepcopy，按既有规则截断；非 tool_result 返回 deepcopy 的安全副本（或原语义：仅 tool_result 改 content）。
- **调用方**：
  - `stream` 在 `collect_messages` 路径调用该 helper 后 append；
  - `filter_last_lines`：对每行尝试 `json.loads`；**仅当**解析为 dict 且 `item.type == "tool_result"` 时，才 `redact` 再 `dumps`；非 tool_result 的 JSON 行与非 JSON 行**原样保留字符串**（不做 re-dump），最后 `[-max_lines:]`。
- **规则不变**：仅 `item.type == "tool_result"` 且 `content` 键存在时替换为 `"[truncated]"`。
- **测试**：helper 单元（含非 tool_result 深拷贝隔离）+ filter 原样保留非 tool_result 行 + all_messages / error_detail 两通道。
- **不写 CONTEXT**；不改 wire 键名。

## Consequences

- 脱敏规则一处修改、两通道生效。
- stream 增加对 errors 的 import（已有 ErrorKind 等依赖）。
- 外部若依赖两份实现细节的细微差异，需对齐到 helper（仓库内无差异测试）。
