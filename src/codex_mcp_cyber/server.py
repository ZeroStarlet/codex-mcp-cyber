"""codex-mcp-cyber MCP 服务器

提供 codex 工具，实现 Claude + Codex 代码审查协作——Claude 写代码，Codex 独立审核 + 复审。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from codex_mcp_cyber.tools.codex import codex_tool

mcp = FastMCP("codex-mcp-cyber")


@mcp.tool(
    name="codex",
    description="调用 Codex CLI 进行代码审核，给出 ✅通过/⚠️优化/❌修改 结论。默认 sandbox: read-only。",
)
async def codex(
    PROMPT: Annotated[str, "审核任务描述"],
    cd: Annotated[Path, "工作目录"],
    sandbox: Annotated[
        Literal["read-only", "workspace-write", "danger-full-access"],
        Field(description="沙箱策略，审核场景必须 read-only"),
    ] = "read-only",
    SESSION_ID: Annotated[str, "会话 ID"] = "",
    skip_git_repo_check: Annotated[
        bool,
        "允许非 Git 仓库",
    ] = True,
    return_all_messages: Annotated[bool, "返回完整消息"] = False,
    return_metrics: Annotated[bool, "返回指标数据"] = False,
    image: Annotated[
        Optional[List[Path]],
        Field(description="附加图片路径"),
    ] = None,
    model: Annotated[
        str,
        Field(description="指定模型"),
    ] = "",
    yolo: Annotated[
        bool,
        Field(description="跳过沙箱审批（慎用）"),
    ] = False,
    profile: Annotated[
        str,
        "配置文件名称",
    ] = "",
    timeout: Annotated[int, "空闲超时秒数"] = 300,
    max_duration: Annotated[int, "总时长上限秒数，0=无限"] = 1800,
    max_retries: Annotated[int, "最大重试次数"] = 1,
    log_metrics: Annotated[bool, "输出指标到 stderr"] = False,
) -> Dict[str, Any]:
    """执行 Codex 代码审核——独立审核 + 修复后复审，循环至通过。"""
    return await codex_tool(
        PROMPT=PROMPT,
        cd=cd,
        sandbox=sandbox,
        SESSION_ID=SESSION_ID,
        skip_git_repo_check=skip_git_repo_check,
        return_all_messages=return_all_messages,
        return_metrics=return_metrics,
        image=image,
        model=model,
        yolo=yolo,
        profile=profile,
        timeout=timeout,
        max_duration=max_duration,
        max_retries=max_retries,
        log_metrics=log_metrics,
    )


def run() -> None:
    """启动 MCP 服务器"""
    mcp.run(transport="stdio")
