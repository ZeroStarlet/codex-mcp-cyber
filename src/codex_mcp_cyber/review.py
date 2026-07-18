"""深 module：审核执行 — run_review(ReviewRequest) → ReviewResult；to_wire 映射冻结 wire。"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from codex_mcp_cyber.errors import (
    CommandNotFoundError,
    CommandTimeoutError,
    ErrorKind,
    build_error_detail,
    is_retryable_error,
    normalize_workdir,
)
from codex_mcp_cyber.process import CodexProcessRunner, PopenCodexRunner
from codex_mcp_cyber.stream import finalize_stream_outcome, reduce_codex_stream


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


@dataclass
class MetricsCollector:
    tool: str
    prompt: str
    sandbox: str
    prompt_chars: int = field(init=False)
    prompt_lines: int = field(init=False)
    ts_start: datetime = field(init=False)
    ts_end: Optional[datetime] = None
    duration_ms: int = 0
    success: bool = False
    error_kind: Optional[str] = None
    retries: int = 0
    exit_code: Optional[int] = None
    result_chars: int = 0
    result_lines: int = 0
    raw_output_lines: int = 0
    json_decode_errors: int = 0

    def __post_init__(self) -> None:
        self.prompt_chars = len(self.prompt)
        self.prompt_lines = self.prompt.count("\n") + 1
        self.ts_start = datetime.now(timezone.utc)

    def finish(
        self,
        success: bool,
        error_kind: Optional[str] = None,
        result: str = "",
        exit_code: Optional[int] = None,
        raw_output_lines: int = 0,
        json_decode_errors: int = 0,
        retries: int = 0,
    ) -> None:
        self.ts_end = datetime.now(timezone.utc)
        self.duration_ms = int((self.ts_end - self.ts_start).total_seconds() * 1000)
        self.success = success
        self.error_kind = error_kind
        self.result_chars = len(result)
        self.result_lines = result.count("\n") + 1 if result else 0
        self.exit_code = exit_code
        self.raw_output_lines = raw_output_lines
        self.json_decode_errors = json_decode_errors
        self.retries = retries

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts_start": self.ts_start.isoformat() if self.ts_start else None,
            "ts_end": self.ts_end.isoformat() if self.ts_end else None,
            "duration_ms": self.duration_ms,
            "tool": self.tool,
            "sandbox": self.sandbox,
            "success": self.success,
            "error_kind": self.error_kind,
            "retries": self.retries,
            "exit_code": self.exit_code,
            "prompt_chars": self.prompt_chars,
            "prompt_lines": self.prompt_lines,
            "result_chars": self.result_chars,
            "result_lines": self.result_lines,
            "raw_output_lines": self.raw_output_lines,
            "json_decode_errors": self.json_decode_errors,
        }

    def format_duration(self) -> str:
        return format_duration_ms(self.duration_ms)

    def log_to_stderr(self) -> None:
        metrics = {k: v for k, v in self.to_dict().items() if v is not None}
        try:
            print(json.dumps(metrics, ensure_ascii=False), file=sys.stderr)
        except Exception:
            pass


def format_duration_ms(duration_ms: int) -> str:
    total_seconds = duration_ms // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}m{seconds}s"


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
        path_line = f"已归一化路径：{result.workdir}\n" if result.workdir is not None else ""
        return (
            "工作目录路径非法（Windows 常见：cd 参数被包了字面引号，触发 os error 123）。\n"
            f"{path_line}"
            "正确写法：cd=C:/Users/you/project  或  cd=C:\\\\Users\\\\you\\\\project\n"
            '错误写法：cd="C:/Users/you/project"  （引号会成为路径的一部分）\n'
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
    cmd = ["codex", "exec", "--sandbox", req.sandbox, "--cd", str(cd_path), "--json"]
    image_list = (
        req.image
        if isinstance(req.image, list)
        else ([req.image] if isinstance(req.image, (str, Path)) else [])
    )
    if image_list:
        cmd.extend(["--image", ",".join(str(p) for p in image_list)])
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


def _maybe_metrics(req: ReviewRequest, metrics: MetricsCollector) -> Optional[Dict[str, Any]]:
    return metrics.to_dict() if req.return_metrics else None


def _maybe_messages(
    req: ReviewRequest, all_messages: list[dict[str, Any]]
) -> Optional[list[dict[str, Any]]]:
    return all_messages if req.return_all_messages else None


async def run_review(
    req: ReviewRequest,
    runner: CodexProcessRunner | None = None,
) -> ReviewResult:
    """执行审核。返回领域结局 ReviewResult（wire 由 to_wire 映射）。"""
    metrics = MetricsCollector(tool="codex", prompt=req.prompt, sandbox=req.sandbox)
    active_runner: CodexProcessRunner = runner or PopenCodexRunner()

    cd_path = normalize_workdir(req.cd)
    if not cd_path.exists():
        msg = (
            f"工作目录不存在或路径非法：{cd_path}\n"
            f"（原始输入：{req.cd!r}）"
        )
        metrics.finish(success=False, error_kind=ErrorKind.INVALID_PATH, retries=0)
        if req.log_metrics:
            metrics.log_to_stderr()
        return ReviewResult(
            success=False,
            error_kind=ErrorKind.INVALID_PATH,
            error_message=msg,
            error_detail=build_error_detail(msg),
            duration_ms=metrics.duration_ms,
            workdir=cd_path,
            metrics=_maybe_metrics(req, metrics),
        )

    cmd = _build_cmd(req, cd_path)
    max_retries = max(0, req.max_retries)
    retries = 0
    last_error: Optional[Dict[str, Any]] = None
    all_last_lines: list[str] = []
    success = False
    error_kind: Optional[str] = None
    err_message = ""
    agent_messages = ""
    thread_id: Optional[str] = None
    exit_code: Optional[int] = None
    raw_output_lines = 0
    json_decode_errors = 0
    all_messages: list[dict[str, Any]] = []

    while retries <= max_retries:
        # 每轮尝试独立状态，避免超时/失败混入上一轮 exit_code、messages 等
        attempt_exit_code: Optional[int] = None
        attempt_raw_lines = 0
        attempt_json_decode_errors = 0
        attempt_all_messages: list[dict[str, Any]] = []
        attempt_last_lines: list[str] = []
        attempt_agent_messages = ""
        attempt_thread_id: Optional[str] = None
        attempt_err_message = ""
        attempt_error_kind: Optional[str] = None

        try:
            outcome = active_runner.run(
                cmd,
                prompt=req.prompt,
                timeout=req.timeout,
                max_duration=req.max_duration,
            )
        except CommandNotFoundError as e:
            metrics.finish(
                success=False,
                error_kind=ErrorKind.COMMAND_NOT_FOUND,
                retries=retries,
            )
            if req.log_metrics:
                metrics.log_to_stderr()
            return ReviewResult(
                success=False,
                error_kind=ErrorKind.COMMAND_NOT_FOUND,
                error_message=str(e),
                error_detail=build_error_detail(str(e)),
                duration_ms=metrics.duration_ms,
                workdir=cd_path,
                metrics=_maybe_metrics(req, metrics),
            )
        except CommandTimeoutError as e:
            error_kind = ErrorKind.IDLE_TIMEOUT if e.is_idle else ErrorKind.TIMEOUT
            err_message = str(e)
            # 超时诊断仅来自本轮 partial_lines，不沿用上一轮 exit_code / decode 计数
            partial = list(getattr(e, "partial_lines", None) or [])
            if partial:
                stream = reduce_codex_stream(
                    partial, collect_messages=req.return_all_messages
                )
                attempt_last_lines = stream.last_lines
                attempt_json_decode_errors = stream.json_decode_errors
                attempt_all_messages = stream.all_messages
                # 超时本身即为失败原因；不把 partial 归约成 success
            else:
                attempt_last_lines = []
                attempt_json_decode_errors = 0
                attempt_all_messages = []
            attempt_raw_lines = int(getattr(e, "raw_output_lines", 0) or len(partial))
            all_last_lines = attempt_last_lines
            all_messages = attempt_all_messages
            json_decode_errors = attempt_json_decode_errors
            raw_output_lines = attempt_raw_lines
            exit_code = None
            agent_messages = ""
            thread_id = None
            last_error = {
                "error_kind": error_kind,
                "err_message": err_message,
                "exit_code": None,
                "json_decode_errors": attempt_json_decode_errors,
                "raw_output_lines": attempt_raw_lines,
            }
            success = False
            if retries < max_retries:
                retries += 1
                await asyncio.sleep(0.5 * (2 ** (retries - 1)))
                continue
            break

        attempt_exit_code = outcome.exit_code
        attempt_raw_lines = outcome.raw_output_lines
        stream = reduce_codex_stream(
            outcome.lines, collect_messages=req.return_all_messages
        )
        stream = finalize_stream_outcome(stream, exit_code=attempt_exit_code)
        attempt_agent_messages = stream.agent_messages
        attempt_thread_id = stream.thread_id
        attempt_err_message = stream.err_message
        attempt_error_kind = stream.error_kind
        attempt_json_decode_errors = stream.json_decode_errors
        attempt_all_messages = stream.all_messages
        attempt_last_lines = stream.last_lines

        exit_code = attempt_exit_code
        raw_output_lines = attempt_raw_lines
        agent_messages = attempt_agent_messages
        thread_id = attempt_thread_id
        err_message = attempt_err_message
        error_kind = attempt_error_kind
        json_decode_errors = attempt_json_decode_errors
        all_messages = attempt_all_messages
        all_last_lines = attempt_last_lines
        success = not stream.had_error

        if success:
            break

        if is_retryable_error(error_kind) and retries < max_retries:
            last_error = {
                "error_kind": error_kind,
                "err_message": err_message,
                "exit_code": exit_code,
                "json_decode_errors": json_decode_errors,
                "raw_output_lines": raw_output_lines,
            }
            retries += 1
            await asyncio.sleep(0.5 * (2 ** (retries - 1)))
            continue

        last_error = {
            "error_kind": error_kind,
            "err_message": err_message,
            "exit_code": exit_code,
            "json_decode_errors": json_decode_errors,
            "raw_output_lines": raw_output_lines,
        }
        break

    metrics.finish(
        success=success,
        error_kind=error_kind,
        result=agent_messages,
        exit_code=exit_code,
        raw_output_lines=raw_output_lines,
        json_decode_errors=json_decode_errors,
        retries=retries,
    )
    if req.log_metrics:
        metrics.log_to_stderr()

    if success:
        return ReviewResult(
            success=True,
            text=agent_messages,
            session_id=thread_id,
            duration_ms=metrics.duration_ms,
            workdir=cd_path,
            metrics=_maybe_metrics(req, metrics),
            all_messages=_maybe_messages(req, all_messages),
        )

    if last_error:
        error_kind = last_error["error_kind"]
        err_message = last_error["err_message"]
        exit_code = last_error["exit_code"]
        json_decode_errors = last_error["json_decode_errors"]

    detail = build_error_detail(
        message=err_message.strip().split("\n")[0] if err_message else "未知错误",
        exit_code=exit_code,
        last_lines=all_last_lines,
        json_decode_errors=json_decode_errors,
        idle_timeout_s=req.timeout if error_kind == ErrorKind.IDLE_TIMEOUT else None,
        max_duration_s=req.max_duration if error_kind == ErrorKind.TIMEOUT else None,
        retries=retries,
    )
    return ReviewResult(
        success=False,
        error_kind=error_kind,
        error_message=err_message,
        error_detail=detail,
        duration_ms=metrics.duration_ms,
        workdir=cd_path,
        metrics=_maybe_metrics(req, metrics),
        all_messages=_maybe_messages(req, all_messages),
    )
