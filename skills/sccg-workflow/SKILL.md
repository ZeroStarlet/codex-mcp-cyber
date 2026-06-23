---
name: sccg-workflow
description: |
  SCCG (Sisyphus-Coder-Codex-Gemini) collaboration for code and document tasks.
  Use when: writing/modifying code, editing documents, implementing features, fixing bugs, refactoring, or code review.
  协调 Coder 执行代码/文档改动，Codex 审核代码质量。
---

# SCCG 工作流协作流程

## 角色分工

- **Claude**：项目规划者 + 项目快检者 + 项目最终决策者（**禁止直接修改或改动代码/文档**）
- **Coder**：项目执行者（代码/文档改动）
- **Codex**：项目审核者 + 项目高级代码顾问（**每次修改完成后必须立即审核，✅ 通过后方可继续**）
- **Gemini**：项目高阶顾问（按需） → 详见 `/gemini-collaboration`

## 任务拆分原则（分发给 Coder）

> ⚠️ **一次调用，一个目标**。禁止向 Coder 堆砌多个不相关需求。

- **精准 Prompt**：目标明确、上下文充分、验收标准清晰
- **按模块拆分**：相关改动可合并，独立模块分开
- **每模块必审**：每模块完成 Claude 快检后，**必须立即主动**调用 Codex 审核，**所有问题关闭后**才能继续

## 核心流程（四步闭环）

### 1. Claude 规划

分析需求、拆分任务、准备精准 Prompt。

调用前（复杂任务推荐）：
- 搜索受影响的文件/符号
- 在 PROMPT 中列出修改清单
- **复杂问题可先与 Codex 沟通**：架构设计或复杂方案可先咨询后再委托 Coder 执行

### 2. Coder 执行

所有代码、文档等内容改动或修改任务，**直接委托 Coder 执行**。

### 3. Claude 快检

Coder 执行完毕后，Claude 快速读取验收：
- **无误** → 继续下一步（调用 Codex 审核）
- **有误** → 委托 Coder 修复（**严禁禁止 Claude 自行写代码，无论简单与否都禁止  Claude 自行写代码**）

### 4. Codex 审核

每次 Coder 执行完成并通过 Claude 快检后，**必须立即主动调用 Codex 审核**：
- 检查代码质量、潜在 Bug
- 结论：✅ 通过 / ⚠️ 优化 / ❌ 修改

**强制审查**：每次修改完成后必须**立即主动**委托 Codex 执行代码审查，**得到 ✅ 通过 信号后方可合并 / 提交 / 推送**。未通过禁止继续下一步。

**问题闭环**：审查（Codex 或人工）登记的每一个问题项，必须在**当前修改轮次内**完成修复，修复后**必须反馈至审查方**由其确认关闭，形成 `审查 → 修复 → 复审 → 关闭` 闭环。

**零遗留**：所有问题在**项目跟踪记录**（审查回复 / TaskList 任务状态 / issue tracker 工单，任一载体均可）中必须显式到达"关闭"状态。❌ **绝对禁止**遗留任何未解决或被忽略的问题项；❌ 禁止以"次要"、"后续处理"、"非阻塞"等理由跳过修复。

**审核结果处理**：
- ✅ 通过 → 继续下一步任务
- ⚠️/❌ 有问题 → 委托 Coder 修复 → 再次审核 → 循环直至所有问题关闭

## 工具参考

| 工具 | 用途 | sandbox | 重试 |
|------|------|---------|------|
| Coder | 执行文件改动 | workspace-write | 默认不重试 |
| Codex | 代码文件审核或审查 | read-only | 默认 1 次 |
| Gemini | 顾问/执行 | workspace-write (yolo) | 默认 1 次 |

> 💡 **Gemini 详细指南**：如需了解 Gemini 的具体调用方式和触发场景，请执行 `/gemini-collaboration` 技能。

**会话复用**：保存 `SESSION_ID` 保持上下文。

## 独立决策

Coder/Codex/Gemini 的意见仅供参考。你（Claude）是项目最终决策者，需批判性思考，做出最优决策。

详细参数：[coder-guide.md](coder-guide.md) | [codex-guide.md](codex-guide.md) | [examples.md](examples.md)
特定说明：[KarpathyGuidelines.md](KarpathyGuidelines.md)
