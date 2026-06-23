# Codex 工具详细规范

## 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| PROMPT | string | ✅ | 审核任务描述，必须包含 git diff 与完整审查清单 |
| cd | Path | ✅ | 工作目录 |
| sandbox | string | | **必须** `read-only` |
| SESSION_ID | string | | 会话 ID——**初审必须 `""`（空），复审必须携带上一轮返回值** |
| return_all_messages | boolean | | 返回完整消息历史，默认 False（仅用于调试） |
| return_metrics | boolean | | 返回值中包含指标数据，默认 False |
| image | List[Path] | | 附加图片 |
| model | string | | 指定模型 |
| timeout | int | | 空闲超时（秒），默认 300，无输出超过此时间触发 |
| max_duration | int | | 总时长硬上限（秒），默认 1800（30 分钟），0 表示无限制 |
| max_retries | int | | 最大重试次数，默认 1（可安全重试） |
| log_metrics | boolean | | 将指标输出到 stderr |
| skip_git_repo_check | bool | | 允许在非 Git 仓库中运行，默认 True |
| yolo | bool | | 无需审批运行所有命令（跳过沙箱），默认 False。**慎用** |
| profile | string | | 从 `~/.codex/config.toml` 加载的配置文件名称，默认 `""` |

### SESSION_ID 规则（强制）

| 场景 | SESSION_ID | 原因 |
|------|-----------|------|
| **初审**（初次送审） | `""` 或不传 | 干净上下文，无历史干扰 |
| **复审**（修复后再次送审） | 上一轮返回的 `SESSION_ID` | 保留初审意见，复审更连贯 |
| **新功能 / 无关改动** | `""` | 不应混入不相干的审查上下文 |
| **会话丢失**（崩溃 / 重启 / 上下文溢出） | `""` 并附初审摘要 | 开新会话，在 PROMPT 中补充上一轮审查结论摘要作为上下文补偿 |

## 返回值

```json
// 成功
{
  "success": true,
  "tool": "codex",
  "SESSION_ID": "uuid-string",
  "result": "Codex 审核结论"
}

// 失败（结构化错误）
{
  "success": false,
  "tool": "codex",
  "error": "错误摘要信息",
  "error_kind": "idle_timeout | timeout | command_not_found | upstream_error | ...",
  "error_detail": {
    "message": "错误简述",
    "exit_code": 1,
    "last_lines": ["最后20行输出..."],
    "json_decode_errors": 0,
    "idle_timeout_s": 300,
    "max_duration_s": 1800,
    "retries": 1
  }
}
```

### error_kind 枚举

| 值 | 说明 |
|----|------|
| `idle_timeout` | 空闲超时（无输出） |
| `timeout` | 总时长超时 |
| `command_not_found` | codex CLI 未安装 |
| `auth_required` | 未登录或认证过期，需运行 `codex login` |
| `upstream_error` | CLI 返回错误 |
| `json_decode` | JSON 解析失败 |
| `protocol_missing_session` | 未获取 SESSION_ID |
| `empty_result` | 无响应内容 |
| `subprocess_error` | 进程退出码非零 |
| `unexpected_exception` | 未预期异常 |

## 送审 PROMPT 模板

调用前先取 diff 并嵌入 PROMPT，让 Codex 精准审变更而非自行探索文件（省 token、更准）：

```bash
git diff --no-color   # Claude 在调用 Codex 前执行
```

唯一模板。**审查清单必须完整携带**（Codex 工具无内置审查 system prompt，清单不可省略）。`**本次重点**` 字段用于标注该次审查应优先关注的高风险面。

> **发送前校验**：所有 `[方括号占位符]` 必须替换为实际内容再发送。切勿将字面占位符文本（如 `[文件列表]`、`[粘贴 git diff --no-color 输出]`）发送给 Codex——这会导致审查无实际内容、返回误报 PASS。

````
请 review 以下代码改动（只审不改）：

**改动文件**：[文件列表]
**改动目的**：[简要描述]
**本次重点**：[如：鉴权边界 / 并发竞态 / 输入校验 / 是否引入回归 —— 没有可省略]

**Git Diff**:
```diff
[粘贴 git diff --no-color 输出]
```

**请检查**：
1. 逻辑正确性
2. 边界条件（空值 / 越界 / 非法输入 / 溢出）
3. 安全风险（注入 / 越权 / 凭证泄漏 / 敏感信息泄露）
4. 测试缺口（缺少断言 / 未覆盖边界 / 回归缺失）
5. 可维护性（命名 / 结构 / 重复代码 / 过度抽象）

**请给出明确结论**：
- ✅ PASS：代码质量良好，可以合入
- ⚠️ OPTIMIZE：有优化建议但不阻塞合入，由 Claude 评估是否采纳
- ❌ CHANGE：必须修改，以下为具体问题清单
````

## 使用规范

1. **严格边界**：必须 `sandbox="read-only"`，Codex 严禁修改代码
2. **SESSION_ID 纪律**：初审传 `""`，复审传上一轮返回值；新功能开新会话
3. 检查 `success` 字段判断审核是否成功
4. 从 `result` 字段获取审核结论（✅ PASS / ⚠️ OPTIMIZE / ❌ CHANGE）
5. 失败时检查 `error_kind` 了解失败原因

## 重试策略

Codex 默认允许 **1 次自动重试**（只读操作无副作用）：
- 超时、网络错误等会自动重试（最多 1 次，退避间隔约 0.5s）
- `command_not_found` / `auth_required` 不会重试（需用户干预）

工具层面重试与流程层面重试（3 轮往返上限）互不冲突：
- 工具重试：单次 `codex` 调用因网络 / 超时自动重试 1 次，对 Claude 透明
- 流程重试：Codex 返回 ❌ CHANGE → Claude 修复 → 再次调用 codex 复审，最多 3 轮
