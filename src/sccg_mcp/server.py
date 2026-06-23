"""SCCG-MCP 服务器主体

提供 coder、codex 和 gemini 三个 MCP 工具，实现多方协作。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from sccg_mcp.tools.coder import coder_tool
from sccg_mcp.tools.codex import codex_tool
from sccg_mcp.tools.gemini import gemini_tool

# 创建 MCP 服务器实例
mcp = FastMCP("SCCG-MCP Server")


@mcp.tool(
    name="coder",
    description="调用可配置后端模型执行代码生成/修改任务。默认 sandbox: workspace-write。",
)
async def coder(
    PROMPT: Annotated[str, "发送给 Coder 的任务指令"],
    cd: Annotated[Path, "工作目录"],
    sandbox: Annotated[
        Literal["read-only", "workspace-write", "danger-full-access"],
        Field(description="沙箱策略"),
    ] = "workspace-write",
    SESSION_ID: Annotated[str, "会话 ID"] = "",
    return_all_messages: Annotated[bool, "返回完整消息"] = False,
    return_metrics: Annotated[bool, "返回指标数据"] = False,
    timeout: Annotated[int, "空闲超时秒数"] = 300,
    max_duration: Annotated[int, "总时长上限秒数，0=无限"] = 1800,
    max_retries: Annotated[int, "最大重试次数（有写入副作用）"] = 0,
    log_metrics: Annotated[bool, "输出指标到 stderr"] = False,
) -> Dict[str, Any]:
    """执行 Coder 代码任务"""
    return await coder_tool(
        PROMPT=PROMPT,
        cd=cd,
        sandbox=sandbox,
        SESSION_ID=SESSION_ID,
        return_all_messages=return_all_messages,
        return_metrics=return_metrics,
        timeout=timeout,
        max_duration=max_duration,
        max_retries=max_retries,
        log_metrics=log_metrics,
    )


@mcp.tool(
    name="codex",
    description="调用 Codex 进行代码审核，给出 ✅通过/⚠️优化/❌修改 结论。默认 sandbox: read-only。",
)
async def codex(
    PROMPT: Annotated[str, "审核任务描述"],
    cd: Annotated[Path, "工作目录"],
    sandbox: Annotated[
        Literal["read-only", "workspace-write", "danger-full-access"],
        Field(description="沙箱策略"),
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
    """执行 Codex 代码审核"""
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


@mcp.tool(
    name="gemini",
    description="调用 Gemini CLI 进行专家咨询、代码审核或代码执行。默认 yolo=true。",
)
async def gemini(
    PROMPT: Annotated[str, "任务指令"],
    cd: Annotated[Path, "工作目录"],
    sandbox: Annotated[
        Literal["read-only", "workspace-write", "danger-full-access"],
        Field(description="沙箱策略"),
    ] = "workspace-write",
    yolo: Annotated[
        bool,
        Field(description="跳过审批"),
    ] = True,
    SESSION_ID: Annotated[str, "会话 ID"] = "",
    return_all_messages: Annotated[bool, "返回完整消息"] = False,
    return_metrics: Annotated[bool, "返回指标数据"] = False,
    model: Annotated[
        str,
        Field(description="指定模型"),
    ] = "",
    timeout: Annotated[int, "空闲超时秒数"] = 300,
    max_duration: Annotated[int, "总时长上限秒数，0=无限"] = 1800,
    max_retries: Annotated[int, "最大重试次数"] = 1,
    log_metrics: Annotated[bool, "输出指标到 stderr"] = False,
) -> Dict[str, Any]:
    """执行 Gemini 任务"""
    return await gemini_tool(
        PROMPT=PROMPT,
        cd=cd,
        sandbox=sandbox,
        yolo=yolo,
        SESSION_ID=SESSION_ID,
        return_all_messages=return_all_messages,
        return_metrics=return_metrics,
        model=model,
        timeout=timeout,
        max_duration=max_duration,
        max_retries=max_retries,
        log_metrics=log_metrics,
    )


def run() -> None:
    """启动 MCP 服务器"""
    mcp.run(transport="stdio")
