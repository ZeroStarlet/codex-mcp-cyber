"""Codex 工具 wire 壳 — 唯一 15 参 interface → ReviewRequest → to_wire。

历史 import 路径：codex_mcp_cyber.tools.codex.codex_tool
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import Field

from codex_mcp_cyber.review import ReviewRequest, run_review, to_wire


async def codex_tool(
    PROMPT: Annotated[str, "审核任务描述"],
    cd: Annotated[Path, "工作目录"],
    sandbox: Annotated[
        Literal["read-only", "workspace-write", "danger-full-access"],
        Field(description="沙箱策略，默认只读"),
    ] = "read-only",
    SESSION_ID: Annotated[str, "会话 ID，用于多轮对话"] = "",
    skip_git_repo_check: Annotated[
        bool,
        "允许在非 Git 仓库中运行",
    ] = True,
    return_all_messages: Annotated[bool, "是否返回完整消息"] = False,
    return_metrics: Annotated[bool, "是否在返回值中包含指标数据"] = False,
    image: Annotated[
        Optional[List[Path]],
        Field(description="附加图片文件路径列表"),
    ] = None,
    model: Annotated[
        str,
        Field(description="指定模型，默认使用 Codex 自己的配置"),
    ] = "",
    yolo: Annotated[
        bool,
        Field(description="无需审批运行所有命令（跳过沙箱）"),
    ] = False,
    profile: Annotated[
        str,
        "从 ~/.codex/config.toml 加载的配置文件名称",
    ] = "",
    timeout: Annotated[
        int,
        Field(description="空闲超时（秒），无输出超过此时间触发超时，默认 300 秒"),
    ] = 300,
    max_duration: Annotated[
        int,
        Field(description="总时长硬上限（秒），默认 1800 秒（30 分钟），0 表示无限制"),
    ] = 1800,
    max_retries: Annotated[int, "最大重试次数，默认 1（Codex 只读可安全重试）"] = 1,
    log_metrics: Annotated[bool, "是否将指标输出到 stderr"] = False,
) -> Dict[str, Any]:
    """执行 Codex 代码审核（wire 兼容壳）。"""
    req = ReviewRequest(
        prompt=PROMPT,
        cd=cd,
        sandbox=sandbox,
        session_id=SESSION_ID,
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
    return to_wire(await run_review(req))
