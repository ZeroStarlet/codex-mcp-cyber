"""审核错误的分类法与详情装配。

只放「错误是什么」——异常类型、ErrorKind 取值、以及给 wire 用的 error_detail。
「什么算哪种错」在 classify；路径归一在 paths；Windows 安全原语在 winsec /
winlink；脱敏在 redact。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from codex_mcp_cyber.redact import LAST_LINES_LIMIT, filter_last_lines


class CommandNotFoundError(Exception):
    """命令不存在错误"""


class CommandTimeoutError(Exception):
    """命令执行超时错误。

    生产端不抛此异常（超时走 ProcessOutcome.terminal）；它是注入型
    ``is_terminal_line`` 谓词向 runner 报告超时的唯一信道。
    行的收集由 runner 自己负责，异常只带 is_idle。
    """

    def __init__(self, message: str, is_idle: bool = False):
        super().__init__(message)
        self.is_idle = is_idle


class ErrorKind:
    """结构化错误类型枚举"""

    TIMEOUT = "timeout"
    IDLE_TIMEOUT = "idle_timeout"
    COMMAND_NOT_FOUND = "command_not_found"
    UPSTREAM_ERROR = "upstream_error"
    AUTH_REQUIRED = "auth_required"
    INVALID_PATH = "invalid_path"
    PROTOCOL_MISSING_SESSION = "protocol_missing_session"
    EMPTY_RESULT = "empty_result"
    SUBPROCESS_ERROR = "subprocess_error"
    UNEXPECTED_EXCEPTION = "unexpected_exception"


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
        detail["last_lines"] = filter_last_lines(last_lines, max_lines=LAST_LINES_LIMIT)
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
