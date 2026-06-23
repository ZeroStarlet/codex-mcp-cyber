---
name: cc-review
description: Claude + Codex code review collaboration. Claude writes code, Codex reviews it independently with re-review loop until pass.
---

# CC Review — Claude + Codex 代码审查协作

**Claude 写代码，Codex 独立审核 + 复审，循环至通过。**

## 角色

| 角色 | 职责 | 权限 |
|------|------|------|
| **Claude** | 写代码、拆任务、做决策 | 完全读写 |
| **Codex** | 独立审核 + 复审（唯一审核者） | read-only |

## 核心规则

- Codex 是**唯一审核者**：初次审核 + 修复后复审，全由 Codex 独立完成。Claude 不做初审。
- 每次 Claude 完成代码改动后，**必须立即**调用 Codex 审核
- Codex 必须 `sandbox="read-only"`——Codex 绝不修改代码
- 审查登记的每个问题必须在当前轮次内修复并经 Codex 复审确认关闭
- 零遗留：所有问题必须到达"关闭"状态

## 工作流

1. Claude 分析需求，执行代码改动
2. Claude 获取变更摘要：
   ```bash
   git diff --no-color
   ```
3. Claude 调用 `mcp__codex_mcp_cyber__codex` 工具，将 diff 嵌入 PROMPT：
   ````
   请 review 以下代码改动：

   **改动目的**：[简要描述]

   **Git Diff**:
   ```diff
   [粘贴 git diff 输出]
   ```

   **请检查**：
   1. 代码质量（可读性、可维护性）
   2. 潜在 Bug 或边界情况
   3. 需求完成度
   4. 安全问题
   5. 最佳实践

   **请给出明确结论**：
   - ✅ 通过：代码质量良好，可以合入
   - ⚠️ 建议优化：[具体建议]
   - ❌ 需要修改：[具体问题]
   ````
4. **Codex 独立审核**，返回结论
5. ❌ / ⚠️ → Claude 修复问题 → 回到步骤 2，**Codex 复审** → 循环直到 ✅
6. ✅ → 合入 / 提交 / 推送

## Codex 工具参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| PROMPT | string | ✅ | - | 审核任务描述，含 git diff |
| cd | Path | ✅ | - | 工作目录 |
| sandbox | string | - | `read-only` | **必须** read-only |
| SESSION_ID | string | - | `""` | 会话复用 |
| skip_git_repo_check | bool | - | `true` | 允许非 Git 仓库 |
| timeout | int | - | `300` | 空闲超时秒数 |
| max_duration | int | - | `1800` | 总时长上限秒数 |
| max_retries | int | - | `1` | 最大重试次数 |
| model | string | - | `""` | 指定模型 |

详细参数说明：[codex-guide.md](codex-guide.md)
