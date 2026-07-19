# 使用案例

> **前提**：代码由 Claude 写并自测；Codex **只审不改**（`sandbox="read-only"`）。
> **PROMPT 全文**只在 [review-checklist.md](review-checklist.md)。下方只写各场景的**差异字段**与调用要点。
> **`cd`**：裸路径，如 `C:/Users/you/repo` 或 `/project`。

---

## 1. 新功能

**场景**：用户管理 REST API。
**要点**：可独立验证单元完成后初审；验收写进「改动目的」。

| 字段 | 值 |
|------|-----|
| 改动文件 | `src/api/users.py`, `src/api/schemas.py`, `tests/test_users.py` |
| 改动目的 | 新增 GET/POST /users、GET /users/{id}，分页与过滤 |
| 本次重点 | 分页边界（空结果 / 越界页码）、输入校验、权限 |
| SESSION_ID | `""`（初审） |

---

## 2. Bug 修复 + 复审

**场景**：token 过期未刷新导致被踢。
**要点**：补回归测试；复审复用 SESSION_ID。

**初审**

| 字段 | 值 |
|------|-----|
| 改动文件 | `src/auth/login.py`, `src/auth/token.py`, `tests/test_login.py` |
| 改动目的 | 修复过期未刷新；补过期场景回归 |
| 本次重点 | 并发刷新、刷新失败回退、时钟偏移、断言有效性 |
| SESSION_ID | `""` |

**复审**（初审返回 `SESSION_ID=abc-123` 且 ❌ 后）

| 字段 | 值 |
|------|-----|
| 改动文件 | `src/auth/token.py` |
| 改动目的 | 按初审修并发刷新竞态；互斥锁 + 失败回退 |
| 本次重点 | 锁在异常路径是否释放；回退是否覆盖全部失败分支 |
| SESSION_ID | `abc-123` |

---

## 3. 强制送审（支付回调）

**场景**：支付回调写订单状态（不可逆 + 级联）。
**要点**：再小也必须送审；重点写高风险面。

| 字段 | 值 |
|------|-----|
| 改动文件 | `src/payment/callback.py`, `src/payment/models.py` |
| 改动目的 | 回调新增退款状态字段，更新订单写入 |
| 本次重点 | 不可逆写入、幂等（重复回调）、退款金额校验、级联库存 |
| SESSION_ID | `""` |

原则表见 [critical-modules.md](critical-modules.md)。

---

## 4. 重构

**场景**：单体 service 拆 query / command / validation，行为不变。

| 字段 | 值 |
|------|-----|
| 改动文件 | `src/users/service.py` → `services/query.py`, `command.py`, `validation.py` |
| 改动目的 | 纯重构，行为应完全不变 |
| 本次重点 | 语义漂移、循环依赖、导入路径 |
| SESSION_ID | `""` |

重构前后同一套测试须全绿。

---

## 5. 不认同 Codex 的 ❌

**场景**：Codex 要求 nil 检查；Claude 有上游永非 nil 证据。

在标准 PROMPT 上**追加**：

```text
**与初审的分歧**：
初审认为 order.LineItems 需 nil 检查。
LineItems 由 NewOrder() 初始化为 make([]LineItem, 0)（order.go:42），永不为 nil。
加检查是死代码，与「不为不可能场景加错误处理」相悖。
同意另一条（超时未设置），已在本次 diff 修复。
```

| 字段 | 值 |
|------|-----|
| SESSION_ID | 复用初审 |
| 处理 | 最多 3 轮；仍僵持 → 人工裁决（见 [scenarios.md](scenarios.md) E） |

---

## 6. 工具不可用

**`command_not_found` / `auth_required`**：不重试；提示安装或 `codex login`。
**其他错误**：工具重试 1 次；仍失败则写 `docs/pending-review-<date>.md`（diff + 意图），恢复后补审。

```markdown
# 待补审：用户管理 API
- 日期：YYYY-MM-DD
- 改动文件：…
- 改动目的：…
- 状态：Codex 不可用，待补审
## Diff
（粘贴 git diff）
```
