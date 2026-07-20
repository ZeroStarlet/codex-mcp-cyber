"""深 module：审核执行 — run_review(ReviewRequest) → ReviewResult；to_wire 映射冻结 wire。"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from codex_mcp_cyber.classify import classify_spawn_oserror, is_retryable_error
from codex_mcp_cyber.errors import (
    CommandNotFoundError,
    ErrorKind,
    build_error_detail,
)
from codex_mcp_cyber.paths import (
    InvalidWorkdirError,
    format_cli_path,
    normalize_workdir,
)
from codex_mcp_cyber.process import CodexProcessRunner, PopenCodexRunner
from codex_mcp_cyber.winlink import prefer_codex_workdir
from codex_mcp_cyber.stream import finalize_stream_outcome, reduce_codex_stream

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


def _display_error(result: ReviewResult) -> str:
    """wire 用人类可读错误文案；领域结局只保留 error_message。"""
    raw = result.error_message or ""
    if result.error_kind == ErrorKind.AUTH_REQUIRED:
        return (
            "请先登录 Codex CLI。运行以下命令完成认证：\n"
            "  codex login\n"
            "\n"
            "或使用 API Key 认证：\n"
            "  printenv OPENAI_API_KEY | codex login --with-api-key\n"
            "\n" + raw
        )
    if result.error_kind == ErrorKind.INVALID_PATH:
        path_line = (
            f"已归一化路径：{result.workdir}\n" if result.workdir is not None else ""
        )
        return (
            "工作目录路径非法或 Codex 在访问路径时触发 Windows os error 123。\n"
            f"{path_line}"
            "常见原因：\n"
            "1) cd 被包了字面引号（应传裸路径：C:/Users/you/project）\n"
            "2) 中文/非 ASCII 路径下 Codex 内部工具解析失败"
            "（本工具会尝试建 ASCII 目录联接；若仍失败，请把仓库放到纯英文路径）\n"
            "3) 路径不存在或含非法尾部空格/点\n"
            "\n" + raw
        )
    return raw


def to_wire(result: ReviewResult) -> Dict[str, Any]:
    """ReviewResult → 冻结 MCP wire dict。"""
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
            "error": _display_error(result),
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


def _build_cmd(req: ReviewRequest, cd_path: Path) -> list[str]:
    # list argv：路径本身不再包引号；format_cli_path 保证 Windows 原生形态。
    cmd = [
        "codex",
        "exec",
        "--sandbox",
        req.sandbox,
        "--cd",
        format_cli_path(cd_path),
        "--json",
    ]
    image_list = (
        req.image
        if isinstance(req.image, list)
        else ([req.image] if isinstance(req.image, (str, Path)) else [])
    )
    if image_list:
        # 相对图片路径相对审核目录（codex_cd），不是 MCP 服务 cwd
        cmd.extend(
            [
                "--image",
                ",".join(format_cli_path(Path(p), base=cd_path) for p in image_list),
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


@dataclass
class _AttemptOutcome:
    """一次行流尝试的内部结局（成功 / 失败 / 超时同型）。不导出。"""

    success: bool
    text: str = ""
    session_id: Optional[str] = None
    error_kind: Optional[str] = None
    error_message: str = ""
    exit_code: Optional[int] = None
    raw_output_lines: int = 0
    json_decode_errors: int = 0
    last_lines: list[str] = field(default_factory=list)
    all_messages: list[dict[str, Any]] = field(default_factory=list)


def _run_attempt(
    runner: CodexProcessRunner,
    cmd: list[str],
    *,
    prompt: str,
    workdir: Path | str | None,
    timeout: int,
    max_duration: int,
    collect_messages: bool,
) -> _AttemptOutcome:
    """执行一次 runner 调用并归约。CommandNotFoundError 向上抛出。"""
    outcome = runner.run(
        cmd,
        prompt=prompt,
        workdir=workdir,
        timeout=timeout,
        max_duration=max_duration,
    )

    # 单通道超时：只 reduce，不 finalize。
    # 理由：finalize 会按「无 session_id / 无正文 / 退出码非零」补判错误种类，
    # 而超时终局本就没有 session_id 与正文 —— 跑 finalize 会把 timeout
    # 覆写成 protocol_missing_session 或 empty_result，丢掉真正的失败原因。
    # 超时的种类已由 outcome.terminal 决定，不需要也不允许再补判。
    if outcome.terminal in ("timeout", "idle_timeout"):
        partial = list(outcome.lines)
        if partial:
            stream = reduce_codex_stream(partial, collect_messages=collect_messages)
            last_lines = stream.last_lines
            json_decode_errors = stream.json_decode_errors
            all_messages = stream.all_messages
        else:
            last_lines = []
            json_decode_errors = 0
            all_messages = []
        kind = (
            ErrorKind.IDLE_TIMEOUT
            if outcome.terminal == "idle_timeout"
            else ErrorKind.TIMEOUT
        )
        return _AttemptOutcome(
            success=False,
            error_kind=kind,
            error_message=outcome.error_message or "timeout",
            exit_code=None,
            raw_output_lines=outcome.raw_output_lines or len(partial),
            json_decode_errors=json_decode_errors,
            last_lines=last_lines,
            all_messages=all_messages,
        )

    stream = reduce_codex_stream(outcome.lines, collect_messages=collect_messages)
    stream = finalize_stream_outcome(stream, exit_code=outcome.exit_code)
    return _AttemptOutcome(
        success=not stream.had_error,
        text=stream.text,
        session_id=stream.session_id,
        error_kind=stream.error_kind,
        error_message=stream.error_message,
        exit_code=outcome.exit_code,
        raw_output_lines=outcome.raw_output_lines,
        json_decode_errors=stream.json_decode_errors,
        last_lines=stream.last_lines,
        all_messages=stream.all_messages,
    )


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
    # 3) 交给 Codex 时优先 ASCII 联接别名（Windows 中文路径防 123）
    try:
        cd_path = normalize_workdir(req.cd)
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

    if not cd_path.is_dir():
        msg = (
            f"工作目录不存在或不是目录：{cd_path}\n"
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
                workdir=cd_path,
            ),
            retries=0,
        )

    codex_cd = prefer_codex_workdir(cd_path)
    cmd = _build_cmd(req, codex_cd)
    # 子进程 cwd 也设为审核目录，让 Codex 子工具的相对路径解析落在同一处
    #（ASCII 联接时尤其重要）。经 run(...) 传入 —— 它是 seam 的一部分，
    # 不是某个具体 adapter 的字段。
    max_retries = max(0, req.max_retries)
    retries = 0
    last: _AttemptOutcome | None = None

    while retries <= max_retries:
        try:
            attempt = _run_attempt(
                active_runner,
                cmd,
                prompt=req.prompt,
                workdir=codex_cd,
                timeout=req.timeout,
                max_duration=req.max_duration,
                collect_messages=req.return_all_messages,
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
                    workdir=cd_path,
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
                    workdir=cd_path,
                ),
                retries=retries,
            )

        if attempt.success:
            return _finish(
                req,
                ts_start,
                ReviewResult(
                    success=True,
                    text=attempt.text,
                    session_id=attempt.session_id,
                    workdir=cd_path,
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
            # 失败终局 text 保持空串（行为冻结）；partial 仅经 result_text 进 metrics
            error_kind=last.error_kind,
            error_message=last.error_message,
            error_detail=detail,
            workdir=cd_path,
            all_messages=_maybe_messages(req, last.all_messages),
        ),
        result_text=last.text,
        retries=retries,
        exit_code=last.exit_code,
        raw_output_lines=last.raw_output_lines,
        json_decode_errors=last.json_decode_errors,
    )
