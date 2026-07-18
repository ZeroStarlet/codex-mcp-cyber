# 1. Wire freeze and line-stream seam

## Status

Accepted

## Context

Codex 审核执行原集中在 `tools/codex.py` 单一宽 interface（15 参 + Dict 返回），进程 IO 与协议归约焊死，Windows 路径事故只能靠真 CLI 复现。可选方案包括：改 MCP wire、字节级 IO seam、或整次调用 mock。

## Decision

- **Wire 冻结**：MCP 参数名与返回 dict key 保持不变；内部加深为 `run_review(ReviewRequest, runner=None)`，出口再映射为旧 JSON。
- **行流 seam**：`CodexProcessRunner.run(...) -> ProcessOutcome(lines, exit_code)`；生产用 Popen adapter，测试用 ScriptedLines adapter。
- **默认参数注入 runner**；MCP 永不传 runner。
- **克制多文件**：`errors` / `process` / `stream` / `review`，不建子包；`tools/codex.py` 仅作兼容薄壳。

## Consequences

- 协议与错误分类可在无 Codex CLI 下单测锁定（含 os error 123 → invalid_path）。
- 调用方 skill / 历史会话写法不受影响。
- 将来若要改 wire，需单独决策并更新 skill 文档。
