# 场景分支

主路径见 [SKILL.md](SKILL.md) 标准闭环。此处只列各分支的**额外动作**；PROMPT 正文始终来自 [review-checklist.md](review-checklist.md)。

## A. 新功能 / 一组改动

- 先拆需求：影响文件 + 验收标准。
- 完成一个可独立验证的单元后再送审（`SESSION_ID=""`）。
- 验收标准写入 PROMPT「改动目的」，让 Codex 按标准核验。

## B. Bug 修复

- 先定位根因，别急着改。
- 修复时**补回归测试**。
- 「本次重点」标注：根因是否真消除、有无新边界、断言是否有效。
- 复审复用初审 `SESSION_ID`。

## C. 强制送审

见 [critical-modules.md](critical-modules.md)。

## D. 重构

- 前提：行为不变。先备好可对照的测试或冒烟，重构前后同绿。
- 「改动目的」写明纯重构；「本次重点」盯语义漂移。
- 只改目标范围，不借机扩功能。

## E. 不认同 Codex 的 ❌ / ⚠️

- **不盲从**：用代码 / 测试 / 规格证据反驳，写进复审 PROMPT。
- ❌：同一问题最多 3 轮往返，僵持则抛人工裁决（附原始意见、反驳依据、分歧摘要）；用户明确授权「继续审到 PASS」时可超闸续审。
- ⚠️：不阻塞合入；Claude 认为不适用则在复审 PROMPT 说明理由即可，不强制多轮；仍 ⚠️ 可直接合入。

## F. 工具不可用

| 错误类型 | 处理 |
|---------|------|
| `command_not_found` / `auth_required` / `invalid_path` | 提示用户安装 / `codex login` / 检查 `cd` 路径，**不重试** |
| 其他（`timeout` / `upstream_error` 等） | 工具默认重试 1 次 |
| 仍然失败 | 将 diff 与审查意图写入 `docs/pending-review-<date>.md`，待恢复后补审 |

工具层重试（单次调用）与流程层 3 轮闸互不冲突。
