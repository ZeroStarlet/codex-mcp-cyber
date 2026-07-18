# 10. Metrics 派生于 ReviewResult；重试 sleep 注入

## Status

Accepted

## Context

`MetricsCollector` 与 `ReviewResult` 双轨记账：`run_review` 四条 return 各抄
`finish` / `log` / 挂 metrics。同时整段 `async` 几乎只为指数退避 `asyncio.sleep`，
backoff 曲线难测。

## Decision

- **删除** `MetricsCollector` 可变状态机。
- **`_metrics_from(req, ts_start, result, …)`** 纯函数从结局派生 metrics dict。
- **`_finish(...)`** 统一写 `duration_ms`、按请求挂 metrics、可选 stderr log。
- **`run_review(..., sleep=None)`**：internal seam，默认 `asyncio.sleep`；
  测试可注入即时 awaitable 并记录 delay。
- **对外** `run_review(req, runner=None)` 行为不变；`sleep` 为 keyword-only 可选。
- **不改** wire metrics 键集合语义（字段名与数值含义保持）。

## Consequences

- 指标 locality 跟结局；无漏 finish。
- backoff 可测；async 税仍在（MCP 壳 async），但时钟可替换。
