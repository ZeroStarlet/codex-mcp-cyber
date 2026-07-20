"""codex-mcp-cyber MCP 服务器

提供 codex 工具，实现 Claude + Codex 代码审查协作——Claude 写代码，Codex 独立审核 + 复审。
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from codex_mcp_cyber.tools.codex import codex_tool

mcp = FastMCP("codex-mcp-cyber")

# 唯一 wire 壳 = codex_tool；server 只注册，不重复声明参数。
# 理由：15 个参数的 wire 契约若在 server 与 tools/codex 各写一份，
# 两份签名会各自漂移（默认值、Literal 取值、Annotated 描述），
# 而 MCP 客户端只看得到 server 注册的那份 —— 分歧只会在运行时暴露。
mcp.tool(
    name="codex",
    description=(
        "调用 Codex CLI 进行代码审核，给出 ✅通过/⚠️优化/❌修改 结论。"
        "默认 sandbox: read-only。"
    ),
)(codex_tool)


def run() -> None:
    """启动 MCP 服务器"""
    mcp.run(transport="stdio")
