"""codex-mcp-cyber MCP 服务器

提供 codex 工具，实现 Claude + Codex 代码审查协作——Claude 写代码，Codex 独立审核 + 复审。
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from codex_mcp_cyber.tools.codex import codex_tool

mcp = FastMCP("codex-mcp-cyber")

# 唯一 wire 壳 = codex_tool；server 只注册（ADR-0004）
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
