# 4. 单一 MCP wire 壳

## Status

Accepted

## Context

审核 deep path 已由 ADR-0001–0003 立住。MCP 入口仍叠两层同构 15 参签名：
`server.codex` 纯转发 → `tools.codex_tool` 再映射 `ReviewRequest` + `to_wire`。
参数描述已漂移；`tools/codex` 另 re-export errors/process，扩大浅 interface。

## Decision

- **唯一 wire 壳**：`codex_mcp_cyber.tools.codex.codex_tool` 持有 15 参 Annotated 签名、
  `ReviewRequest` 构造与 `to_wire`。
- **server 只注册**：`mcp.tool(name="codex", description=<短版>)(codex_tool)`，
  不再抄写第二份参数表。
- **删除** tools/codex 上历史 `# noqa: F401` re-export（errors / process 符号）。
  调用方直接 import 对应 module；`__all__` 仅 `codex_tool`。
- **工具 description**：注册层使用原 server 短文案（强调审核场景默认 read-only）。
- **本决策不包含**：tool_result 脱敏合并、进程协议泄漏、改 wire 键名。

## Consequences

- 改默认值 / 参数注解只动一处。
- 历史 import `codex_mcp_cyber.tools.codex.codex_tool` 与 scripts 保持有效。
- 依赖从 `tools.codex` 取 ErrorKind 等 re-export 的外部代码需改 import（仓库内无此类用法）。
