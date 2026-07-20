---
name: cc-review
description: >
  Claude 写码自测、Codex 只审不改的终审闭环。
  Use when: 功能/bug/重构完成后要独立 code review；
  合入/提交前把关，或用户说「送审 / 审一下 / 让 Codex 看看」；
  强制送审（不可逆/信任边界/静默失效/级联/密钥）；
  修复后复审需复用 SESSION_ID；或用户提到 Codex 终审 / cc-review。
---

# Codex 终审闭环

**主写 = Claude**（需求、实现、自测、按意见修复）。**终审 = Codex**（**只审不改**）。

领域词（初审 / 复审 / 会话标识）以仓库 `CONTEXT.md` 为准。

## 三条铁律

1. Codex 默认 **只审不改**（`sandbox="read-only"`）。
2. 所有修改只由 Claude 落地。
3. 写代码的模型不得自我放行——须 Codex 明确 ✅ PASS。

## 写码准则

写码 / 修复 / 重构遵守 [karpathy-guidelines.md](karpathy-guidelines.md) 的 leading words：**想清再写 · 简单优先 · 外科手术式 · 目标驱动**。Codex 按同一标准审。

## 标准闭环

每步须满足 **completion criterion** 再进入下一步：

1. **拆需求** → 影响文件与可验证验收标准已写出。
2. **写码并自测** → 相关测试 / 冒烟已绿；无命令则写明为何跳过。
3. **取 diff、组 PROMPT** → `git diff --no-color` 已嵌入（大 diff 改走文件引用，见清单）；无未替换的 `[方括号占位符]`；完整清单见 [review-checklist.md](review-checklist.md)。
4. **初审** → `SESSION_ID=""`，`sandbox="read-only"`。完成：结论含 ✅ / ⚠️ / ❌，并记下 `SESSION_ID`。
5. **修复** → 清单每条标注：已修 / 有证据反驳 / 本轮推迟（附因），处置表随修复 diff 进复审 PROMPT；只改问题相关代码（**外科手术式**）。
6. **复审** → 复用上一轮 `SESSION_ID`；丢失则 `""` + 初审摘要进 PROMPT。回到 5，直至 ✅ PASS 或触发 **3 轮闸**。

```text
拆需求 → 写码自测 → diff + 完整清单 → 初审 → 修复 → 复审 → ✅ / 人工裁决
```

## 何时送审

- 完成一个可独立验证的单元（PR 级）再送审，不要每改一行就审。
- **强制送审**：满足 [critical-modules.md](critical-modules.md) 任一原则 → 不得自我放行，复审到 ✅ PASS。
- 修复后必须复审；复审复用同一会话标识。

分支额外动作（新功能 / bug / 重构 / 分歧 / 工具失败）→ [scenarios.md](scenarios.md)。

## SESSION_ID

| 场景 | SESSION_ID |
|------|------------|
| 初审 / 新功能 / 无关改动 | `""` |
| 复审 | 上一轮返回值 |
| 会话丢失 | `""` + PROMPT 附初审摘要；「本次重点」注明会话已重建 |

## 结论处理

| 结论 | 处理 |
|------|------|
| ✅ PASS | 合入 / 提交 |
| ⚠️ OPTIMIZE | 可合入；记录采纳 / 不采纳理由；必要时修复后复审 |
| ❌ CHANGE | 必须修复 → 复审；触发 **3 轮闸** 则抛人工 |
| 无 ✅/⚠️/❌ 标记 | 按 ⚠️；乱码 / 截断按工具失败（[scenarios.md](scenarios.md) F） |

**3 轮闸**：同一改动最多 3 轮审查（初审 → 修 → 复审 → …）。第 3 次复审仍 ❌ → 停下，附原始意见、反驳依据、分歧摘要，抛人工；用户明确授权「继续审到 PASS」时可超闸续审。不得无限循环。Claude **不得自批 PASS**。

## 参考

- [review-checklist.md](review-checklist.md) — PROMPT 与五项检查（SSOT）
- [codex-guide.md](codex-guide.md) — 工具参数、返回值、error_kind
- [critical-modules.md](critical-modules.md) — 强制送审五原则
- [scenarios.md](scenarios.md) — 场景分支
- [examples.md](examples.md) — 填好的调用样例
- [karpathy-guidelines.md](karpathy-guidelines.md) — 写码准则全文
