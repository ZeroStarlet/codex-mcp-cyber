"""深 module：审核执行 — run_review(ReviewRequest) → ReviewResult；to_wire 映射冻结 wire。

编排职责：归一工作目录、构造 argv、驱动 runner seam、重试、metrics、终局装配。
行流折叠与终态判类在 stream；错误人话文案在 errors.display_error。
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from codex_mcp_cyber.classify import classify_spawn_oserror, is_retryable_error
from codex_mcp_cyber.errors import (
    CommandNotFoundError,
    ErrorKind,
    build_error_detail,
    display_error,
)
from codex_mcp_cyber.paths import (
    InvalidWorkdirError,
    format_cli_path,
    normalize_workdir,
)
from codex_mcp_cyber.process import CodexProcessRunner, PopenCodexRunner
from codex_mcp_cyber.stream import StreamOutcome, reduce_codex_stream

SleepFn = Callable[[float], Awaitable[None]]


@dataclass
class ReviewRequest:
    prompt: str
    cd: Path | str
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] = "read-only"
    session_id: str = ""
    skip_git_repo_check: bool = True
    return_all_messages: bool = False
    return_metrics: bool = False
    image: Optional[List[Path]] = None
    model: str = ""
    yolo: bool = False
    profile: str = ""
    timeout: int = 300
    max_duration: int = 1800
    max_retries: int = 1
    log_metrics: bool = False


@dataclass
class ReviewResult:
    """一次审核执行的领域结局（非 wire dict）。"""

    success: bool
    text: str = ""
    session_id: Optional[str] = None
    error_kind: Optional[str] = None
    error_message: str = ""
    error_detail: Optional[Dict[str, Any]] = None
    duration_ms: int = 0
    workdir: Optional[Path] = None
    metrics: Optional[Dict[str, Any]] = None
    all_messages: Optional[list[dict[str, Any]]] = None


def format_duration_ms(duration_ms: int) -> str:
    total_seconds = duration_ms // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}m{seconds}s"


def _metrics_from(
    *,
    req: ReviewRequest,
    ts_start: datetime,
    ts_end: datetime,
    duration_ms: int,
    result: ReviewResult,
    result_text: str = "",
    retries: int = 0,
    exit_code: Optional[int] = None,
    raw_output_lines: int = 0,
    json_decode_errors: int = 0,
) -> Dict[str, Any]:
    """从结局 + 显式 result_text 派生 metrics（失败 partial 只进 metrics，不进 ReviewResult.text）。"""
    text = result_text
    return {
        "ts_start": ts_start.isoformat(),
        "ts_end": ts_end.isoformat(),
        "duration_ms": duration_ms,
        "tool": "codex",
        "sandbox": req.sandbox,
        "success": result.success,
        "error_kind": result.error_kind,
        "retries": retries,
        "exit_code": exit_code,
        "prompt_chars": len(req.prompt),
        "prompt_lines": req.prompt.count("\n") + 1,
        "result_chars": len(text),
        "result_lines": text.count("\n") + 1 if text else 0,
        "raw_output_lines": raw_output_lines,
        "json_decode_errors": json_decode_errors,
    }


def _log_metrics(metrics: Dict[str, Any]) -> None:
    payload = {k: v for k, v in metrics.items() if v is not None}
    try:
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    except Exception:
        pass


def _finish(
    req: ReviewRequest,
    ts_start: datetime,
    result: ReviewResult,
    *,
    result_text: str | None = None,
    retries: int = 0,
    exit_code: Optional[int] = None,
    raw_output_lines: int = 0,
    json_decode_errors: int = 0,
) -> ReviewResult:
    """挂 duration；按请求附 metrics / log。

    result_text：metrics 用的正文（默认 result.text）。失败终局可传入 last.text
    而保持 ReviewResult.text 为空串（行为冻结）。
    """
    ts_end = datetime.now(timezone.utc)
    result.duration_ms = int((ts_end - ts_start).total_seconds() * 1000)
    metrics_text = result.text if result_text is None else result_text
    if req.return_metrics or req.log_metrics:
        metrics = _metrics_from(
            req=req,
            ts_start=ts_start,
            ts_end=ts_end,
            duration_ms=result.duration_ms,
            result=result,
            result_text=metrics_text,
            retries=retries,
            exit_code=exit_code,
            raw_output_lines=raw_output_lines,
            json_decode_errors=json_decode_errors,
        )
        if req.log_metrics:
            _log_metrics(metrics)
        if req.return_metrics:
            result.metrics = metrics
    return result


def to_wire(result: ReviewResult) -> Dict[str, Any]:
    """ReviewResult → 冻结 MCP wire dict。

    0.6.0：失败分支同样携带 SESSION_ID（未建会话为 None）——已建会话的
    失败可直接复审 resume，不再被迫重建会话。
    """
    duration = format_duration_ms(result.duration_ms)
    if result.success:
        out: Dict[str, Any] = {
            "success": True,
            "tool": "codex",
            "SESSION_ID": result.session_id,
            "result": result.text,
            "duration": duration,
        }
    else:
        out = {
            "success": False,
            "tool": "codex",
            "SESSION_ID": result.session_id,
            "error": display_error(
                error_kind=result.error_kind,
                error_message=result.error_message,
                workdir=result.workdir,
            ),
            "error_kind": result.error_kind,
            "error_detail": result.error_detail
            or build_error_detail(result.error_message or "未知错误"),
            "duration": duration,
        }
    if result.all_messages is not None:
        out["all_messages"] = result.all_messages
    if result.metrics is not None:
        out["metrics"] = result.metrics
    return out


def build_codex_argv(
    req: ReviewRequest, workdir: Path, *, cli_workdir: str
) -> list[str]:
    """审核请求 → Codex CLI argv（编码侧协议的单一来源）。

    初审（会话标识为空）：不追加 resume。
    复审（会话标识非空）：``resume <会话标识>`` 缀在**所有** flag 之后。
    ``cli_workdir``：run_review 用 format_cli_path 对归一工作目录**一次**
    计算的成品字符串 —— 同一字符串既进 ``--cd`` 也穿 runner seam 当 cwd，
    两处不得各自格式化（同源不变量）。
    路径一律裸串（list argv 不包引号）；``--image`` 相对工作目录（Path）
    解析，不落到 MCP 服务 cwd。argv[0] 是命令名 codex，可执行体绝对路径
    由生产 adapter 解析改写。
    """
    cmd = [
        "codex",
        "exec",
        "--sandbox",
        req.sandbox,
        "--cd",
        cli_workdir,
        "--json",
    ]
    image_list = (
        req.image
        if isinstance(req.image, list)
        else ([req.image] if isinstance(req.image, (str, Path)) else [])
    )
    if image_list:
        cmd.extend(
            [
                "--image",
                ",".join(
                    format_cli_path(Path(p), base=workdir) for p in image_list
                ),
            ]
        )
    if req.model:
        cmd.extend(["--model", req.model])
    if req.profile:
        cmd.extend(["--profile", req.profile])
    if req.yolo:
        cmd.append("--yolo")
    if req.skip_git_repo_check:
        cmd.append("--skip-git-repo-check")
    if req.session_id:
        cmd.extend(["resume", str(req.session_id)])
    return cmd


def _maybe_messages(
    req: ReviewRequest, all_messages: list[dict[str, Any]]
) -> Optional[list[dict[str, Any]]]:
    return all_messages if req.return_all_messages else None


async def run_review(
    req: ReviewRequest,
    runner: CodexProcessRunner | None = None,
    *,
    sleep: SleepFn | None = None,
) -> ReviewResult:
    """执行审核。返回领域结局 ReviewResult（wire 由 to_wire 映射）。

    sleep：internal seam，默认 asyncio.sleep；测试可注入即时返回。
    """
    ts_start = datetime.now(timezone.utc)
    sleep_fn: SleepFn = sleep or asyncio.sleep
    active_runner: CodexProcessRunner = runner or PopenCodexRunner()

    # 1) 剥引号 / file URI / 严格归一
    # 2) 必须是已存在的目录
    try:
        workdir = normalize_workdir(req.cd)
    except InvalidWorkdirError as e:
        msg = f"{e}\n（原始输入：{req.cd!r}）"
        return _finish(
            req,
            ts_start,
            ReviewResult(
                success=False,
                error_kind=ErrorKind.INVALID_PATH,
                error_message=msg,
                error_detail=build_error_detail(msg),
            ),
            retries=0,
        )

    if not workdir.is_dir():
        msg = (
            f"工作目录不存在或不是目录：{workdir}\n"
            f"（原始输入：{req.cd!r}）"
        )
        return _finish(
            req,
            ts_start,
            ReviewResult(
                success=False,
                error_kind=ErrorKind.INVALID_PATH,
                error_message=msg,
                error_detail=build_error_detail(msg),
                workdir=workdir,
            ),
            retries=0,
        )

    # workdir → OS 字符串只格式化一次：同一 cli_workdir 进 argv --cd，
    # 也经 run(...) 穿 runner seam 原样用作子进程 cwd（同源不变量；
    # 此前生产 adapter 私自再格式化一次，seam 两侧约定不一致）。
    cli_workdir = format_cli_path(workdir)
    cmd = build_codex_argv(req, workdir, cli_workdir=cli_workdir)
    max_retries = max(0, req.max_retries)
    retries = 0
    last: StreamOutcome | None = None

    while retries <= max_retries:
        try:
            proc = active_runner.run(
                cmd,
                prompt=req.prompt,
                workdir=cli_workdir,
                timeout=req.timeout,
                max_duration=req.max_duration,
            )
        except CommandNotFoundError as e:
            return _finish(
                req,
                ts_start,
                ReviewResult(
                    success=False,
                    error_kind=ErrorKind.COMMAND_NOT_FOUND,
                    error_message=str(e),
                    error_detail=build_error_detail(str(e)),
                    workdir=workdir,
                ),
                retries=retries,
            )
        except OSError as e:
            # Popen 启动失败：种类判定在 classify，不在编排里内联 errno 表
            kind = classify_spawn_oserror(e)
            msg = f"启动 Codex 进程失败：{e}"
            return _finish(
                req,
                ts_start,
                ReviewResult(
                    success=False,
                    error_kind=kind,
                    error_message=msg,
                    error_detail=build_error_detail(msg),
                    workdir=workdir,
                ),
                retries=retries,
            )

        # 折叠 + 终态判类都在 stream 的单入口里（超时不得 finalize 的
        # 不变量属于 stream 实现，编排层不再分流）。
        attempt = reduce_codex_stream(
            proc, collect_messages=req.return_all_messages
        )

        if not attempt.had_error:
            return _finish(
                req,
                ts_start,
                ReviewResult(
                    success=True,
                    text=attempt.text,
                    session_id=attempt.session_id,
                    workdir=workdir,
                    all_messages=_maybe_messages(req, attempt.all_messages),
                ),
                retries=retries,
                exit_code=attempt.exit_code,
                raw_output_lines=attempt.raw_output_lines,
                json_decode_errors=attempt.json_decode_errors,
            )

        last = attempt
        if is_retryable_error(attempt.error_kind) and retries < max_retries:
            retries += 1
            await sleep_fn(0.5 * (2 ** (retries - 1)))
            continue
        break

    assert last is not None
    detail = build_error_detail(
        message=(
            last.error_message.strip().split("\n")[0]
            if last.error_message
            else "未知错误"
        ),
        exit_code=last.exit_code,
        last_lines=last.last_lines,
        json_decode_errors=last.json_decode_errors,
        idle_timeout_s=(
            req.timeout if last.error_kind == ErrorKind.IDLE_TIMEOUT else None
        ),
        max_duration_s=(
            req.max_duration if last.error_kind == ErrorKind.TIMEOUT else None
        ),
        retries=retries,
    )
    return _finish(
        req,
        ts_start,
        ReviewResult(
            success=False,
            # 失败终局 text 保持空串（行为冻结）；partial 仅经 result_text 进 metrics。
            # 已取得的会话标识不丢弃（0.6.0）：复审可 resume 失败会话。
            session_id=last.session_id,
            error_kind=last.error_kind,
            error_message=last.error_message,
            error_detail=detail,
            workdir=workdir,
            all_messages=_maybe_messages(req, last.all_messages),
        ),
        result_text=last.text,
        retries=retries,
        exit_code=last.exit_code,
        raw_output_lines=last.raw_output_lines,
        json_decode_errors=last.json_decode_errors,
    )
