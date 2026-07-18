"""审核错误种类与路径/认证判定。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Optional


class CommandNotFoundError(Exception):
    """命令不存在错误"""


class CommandTimeoutError(Exception):
    """命令执行超时错误"""

    def __init__(
        self,
        message: str,
        is_idle: bool = False,
        *,
        partial_lines: list[str] | None = None,
        raw_output_lines: int = 0,
    ):
        super().__init__(message)
        self.is_idle = is_idle
        # 超时前已产出的行（用于 last_lines / 诊断，与旧实现一致）
        self.partial_lines: list[str] = list(partial_lines or [])
        self.raw_output_lines = raw_output_lines


class ErrorKind:
    """结构化错误类型枚举"""

    TIMEOUT = "timeout"
    IDLE_TIMEOUT = "idle_timeout"
    COMMAND_NOT_FOUND = "command_not_found"
    UPSTREAM_ERROR = "upstream_error"
    AUTH_REQUIRED = "auth_required"
    INVALID_PATH = "invalid_path"
    JSON_DECODE = "json_decode"
    PROTOCOL_MISSING_SESSION = "protocol_missing_session"
    EMPTY_RESULT = "empty_result"
    SUBPROCESS_ERROR = "subprocess_error"
    UNEXPECTED_EXCEPTION = "unexpected_exception"


_OS_ERROR_123_MARKERS = (
    "os error 123",
    "文件名、目录名或卷标语法不正确",
    "the filename, directory name, or volume label syntax is incorrect",
)


def normalize_workdir(cd: Path | str) -> Path:
    """剥掉字面包裹引号并返回 Path。"""
    text = str(cd).strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    return Path(text)


def looks_like_invalid_path_error(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _OS_ERROR_123_MARKERS)


def is_auth_error(text: str) -> bool:
    text_lower = text.lower()
    auth_keywords = [
        "401",
        "unauthorized",
        "authentication failed",
        "token refresh failed",
        "login required",
        "not logged in",
        "invalid_grant",
        "credentials",
    ]
    return any(keyword in text_lower for keyword in auth_keywords)


def is_retryable_error(error_kind: Optional[str], err_message: str = "") -> bool:
    del err_message  # 预留；当前仅按 kind 判断
    if error_kind == ErrorKind.COMMAND_NOT_FOUND:
        return False
    if error_kind == ErrorKind.AUTH_REQUIRED:
        return False
    if error_kind == ErrorKind.INVALID_PATH:
        return False
    return True


def filter_last_lines(lines: list[str], max_lines: int = 50) -> list[str]:
    """过滤 last_lines，脱敏 tool_result 中的大内容。"""
    filtered: list[str] = []
    for line in lines:
        try:
            data = json.loads(line)
            item = data.get("item", {})
            if item.get("type") == "tool_result":
                data = copy.deepcopy(data)
                if "content" in data["item"]:
                    data["item"]["content"] = "[truncated]"
                filtered.append(json.dumps(data, ensure_ascii=False))
                continue
            filtered.append(line)
        except (json.JSONDecodeError, TypeError, AttributeError):
            filtered.append(line)
    return filtered[-max_lines:]


def build_error_detail(
    message: str,
    exit_code: Optional[int] = None,
    last_lines: Optional[list[str]] = None,
    json_decode_errors: int = 0,
    idle_timeout_s: Optional[int] = None,
    max_duration_s: Optional[int] = None,
    retries: int = 0,
) -> Dict[str, Any]:
    detail: Dict[str, Any] = {"message": message}
    if exit_code is not None:
        detail["exit_code"] = exit_code
    if last_lines:
        detail["last_lines"] = filter_last_lines(last_lines, max_lines=50)
    if json_decode_errors > 0:
        detail["json_decode_errors"] = json_decode_errors
    if idle_timeout_s is not None:
        detail["idle_timeout_s"] = idle_timeout_s
        detail["suggestion"] = (
            "任务空闲超时（无输出）。建议：1) 增加 timeout 参数 "
            "2) 检查任务是否卡住 3) 拆分为更小的子任务"
        )
    if max_duration_s is not None:
        detail["max_duration_s"] = max_duration_s
        detail["suggestion"] = (
            "任务总时长超时。建议：1) 增加 max_duration 参数 "
            "2) 拆分为更小的子任务 3) 检查是否存在死循环"
        )
    if retries > 0:
        detail["retries"] = retries
    return detail
