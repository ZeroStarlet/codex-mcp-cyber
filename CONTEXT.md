# codex-mcp-cyber

Claude 写代码、Codex 只读终审的 MCP 协作上下文。本文件只收领域词，不含实现细节。

## Language

**审核（Review）**：
对一组代码改动做只读质量判断，产出通过 / 优化建议 / 必须修改三类结论之一。
_Avoid_: 审查会话里的闲聊、让 Codex 直接改文件

**初审（Initial review）**：
对某组改动的第一轮审核；会话标识为空，不携带上一轮意见。
_Avoid_: 复审、终审（终审指角色，不是「第一轮」）

**复审（Re-review）**：
修复后的再次审核；复用同一会话标识，以便对照初审意见。
_Avoid_: 初审、开新会话却假装连续

**会话标识（Session id）**：
Codex 侧一次连续对话的标识；初审为空，复审携带上一轮返回值。
_Avoid_: thread、conversation（对外契约与文档统一称会话标识 / SESSION_ID）

**工作目录（Workdir）**：
Codex 进程运行时的仓库根路径；必须是真实存在的目录，且路径字符串不含字面引号。
_Avoid_: cwd 与业务「当前 shell 目录」混用（以传入的工作目录为准）

**行流（Line stream）**：
Codex 一次执行过程中，stdout 上按行产出的文本序列（JSONL 事件或纯文本致命错误）。
_Avoid_: 原始字节流、半包 IO（本上下文不把半包当作一等概念）

**归约（Reduce）**：
把一行流折叠成结构化审核结局（正文、会话标识、错误种类等）。
_Avoid_: 解析（parse）——归约包含分类与优先级，不只是 JSON 解码

**审核请求（ReviewRequest）**：
发起一次审核执行所需的领域输入：任务描述、工作目录、会话标识、沙箱策略及超时/重试等执行选项。
_Avoid_: MCP 参数名本身（PROMPT、SESSION_ID 等 wire 名）、把 wire 字典当请求

**审核结局（ReviewResult）**：
一次审核执行结束后的领域结果（单一结构）：是否成功、正文（text）、会话标识（session_id）、错误种类与原始错误信息、耗时（毫秒）、以及按请求附带的指标与完整消息；与冻结的 MCP 返回字典分离，经映射后才成为 wire。
_Avoid_: 直接把 MCP 返回 dict 当作领域结局、把 JSONL 单行事件叫结局、在领域结局上使用 wire 键名（SESSION_ID、result）
