# Codex 工具详细规范

PROMPT 正文与五项检查见 **[review-checklist.md](review-checklist.md)**（SSOT）。本文件只描述工具契约。

## 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| PROMPT | string | ✅ | 审核任务描述，必须包含 git diff 与完整审查清单 |
| cd | string | ✅ | 工作目录**裸路径字符串**（勿加引号；注解为 str 避免空串变成 Path('.')） |
| sandbox | string | | **必须** `read-only`（默认 `read-only`） |
| SESSION_ID | string | | **初审必须 `""`，复审必须携带上一轮返回值**（默认 `""`） |
| return_all_messages | boolean | | 返回完整消息历史，默认 False（仅调试） |
| return_metrics | boolean | | 返回值中包含指标，默认 False |
| image | List[Path] | | 附加图片，默认 `None` |
| model | string | | 指定模型，默认 `""`（用 Codex 自身配置） |
| timeout | int | | 空闲超时（秒），默认 300 |
| max_duration | int | | 总时长硬上限（秒），默认 1800；0 = 无限制 |
| max_retries | int | | 最大重试次数，默认 1 |
| log_metrics | boolean | | 指标输出到 stderr，默认 False |
| skip_git_repo_check | bool | | 允许非 Git 仓库，默认 True |
| yolo | bool | | 跳过沙箱审批，默认 False。**慎用** |
| profile | string | | `~/.codex/config.toml` 配置名，默认 `""` |

### SESSION_ID 规则

| 场景 | SESSION_ID |
|------|------------|
| 初审 | `""` 或不传 |
| 复审 | 上一轮返回值 |
| 新功能 / 无关改动 | `""` |
| 会话丢失 | `""` + PROMPT 附初审摘要 |

## 返回值

```json
// 成功
{
  "success": true,
  "tool": "codex",
  "SESSION_ID": "uuid-string",
  "result": "Codex 审核结论",
  "duration": "1m4s"
}

// 失败
{
  "success": false,
  "tool": "codex",
  "error": "错误摘要信息",
  "error_kind": "idle_timeout | timeout | command_not_found | upstream_error | ...",
  "error_detail": {
    "message": "错误简述",
    "exit_code": 1,
    "last_lines": ["最后50行输出..."],
    "json_decode_errors": 0,
    "idle_timeout_s": 300,
    "max_duration_s": 1800,
    "retries": 1
  },
  "duration": "0m12s"
}
```

### error_kind

| 值 | 说明 |
|----|------|
| `idle_timeout` | 空闲超时（无输出） |
| `timeout` | 总时长超时 |
| `command_not_found` | codex CLI 未安装 |
| `auth_required` | 未登录或认证过期 → `codex login` |
| `upstream_error` | CLI 返回错误 |
| `protocol_missing_session` | 未获取 SESSION_ID |
| `invalid_path` | 工作目录非法（常见：`cd` 带字面引号 → `os error 123`） |
| `empty_result` | 无响应内容 |
| `subprocess_error` | 进程退出码非零 |
| `unexpected_exception` | 未预期异常 |

### Windows 路径 / os error 123

历史上两类失败：

1. **MCP 入参 `cd` 带字面引号**
   `cd = "\"C:/Users/you/repo\""` → 旧版误报 `protocol_missing_session`。
   包装层会剥包裹引号 / `file://` / 弯引号；调用方仍应传裸路径。

2. **中文 / 非 ASCII 工作目录下，Codex 内部工具触发 123**
   即使 `--cd` 合法，子工具解析路径仍可能失败（本机 8.3 short path 常被禁用）。
   包装层在 Windows 上会为非 ASCII 目录自动建 **ASCII 目录联接**
   （每用户独立：`C:/codex-mcp-cyber-v3-<sidhash>/wd-junctions/<path-hash>/`，不经 cmd），并：
   - 把 `codex exec --cd` 指到联接路径
   - 把 Popen `cwd` 设为同一联接

   若联接创建失败则回退真实路径。仍 123 时，最稳妥是把仓库放到纯英文路径。

若报 `invalid_path`：检查目录是否存在、是否仍含引号、是否非 ASCII。

## 使用规范

1. **只审不改**：`sandbox="read-only"`
2. **SESSION_ID 纪律**：初审 `""`，复审复用；新功能开新会话
3. 查 `success`；从 `result` 取 ✅ / ⚠️ / ❌
4. 失败查 `error_kind`；处理见 [scenarios.md](scenarios.md) F

## 重试策略

- 工具默认 **1 次**自动重试（只读无副作用）：超时 / 网络等
- `command_not_found` / `auth_required` / `invalid_path` **不重试**
- 与流程 **3 轮闸** 互不冲突：工具重试是单次调用；流程重试是 ❌ → 修 → 再审
