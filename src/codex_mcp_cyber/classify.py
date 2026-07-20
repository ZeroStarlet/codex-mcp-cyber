"""错误分类：把各种失败证据折算成 ErrorKind。

「什么算哪种错」此前摊在三个 module 上 —— errors 放谓词、stream 在折叠期
定种类、review 在重试循环里内联 Windows errno 表。想知道某个错误为何被判成
invalid_path 得同时读三处。这里是唯一的判定来源。
"""

from __future__ import annotations

import re
from typing import Optional

from codex_mcp_cyber.errors import ErrorKind

# 仅匹配精确 123（边界），避免 WinError 1231 / os error 1234 误报。
_OS_ERROR_123_RE = re.compile(
    r"(?:"
    r"os\s+error\s+123\b|"
    r"winerror\s+123\b|"
    r"文件名、目录名或卷标语法不正确|"
    r"the filename, directory name, or volume label syntax is incorrect"
    r")",
    re.IGNORECASE,
)

# Popen 启动失败时可归为路径问题的 Windows 错误号。
#   2   ERROR_FILE_NOT_FOUND（cwd 丢失时也可能）
#   3   ERROR_PATH_NOT_FOUND
#   123 ERROR_INVALID_NAME
#   161 ERROR_BAD_PATHNAME
#   206 ERROR_FILENAME_EXCED_RANGE
#   267 ERROR_DIRECTORY
_PATHISH_WIN_ERRNOS = frozenset({2, 3, 123, 161, 206, 267})

_AUTH_KEYWORDS = (
    "401",
    "unauthorized",
    "authentication failed",
    "token refresh failed",
    "login required",
    "not logged in",
    "invalid_grant",
    "credentials",
)


def looks_like_invalid_path_error(text: str) -> bool:
    """文本是否带 Windows os error 123 家族的路径失败特征。"""
    return bool(_OS_ERROR_123_RE.search(text))


def is_auth_error(text: str) -> bool:
    """文本是否指向认证失败（需要 codex login）。"""
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in _AUTH_KEYWORDS)


def is_retryable_error(error_kind: Optional[str]) -> bool:
    """该错误种类是否值得重试。

    只读审核可安全重试；但命令缺失、认证失败、路径非法重试多少次都一样。
    """
    return error_kind not in (
        ErrorKind.COMMAND_NOT_FOUND,
        ErrorKind.AUTH_REQUIRED,
        ErrorKind.INVALID_PATH,
    )


def classify_spawn_oserror(error: OSError) -> str:
    """Popen 启动失败的 OSError → ErrorKind。

    路径类（cwd 非法 / WinError 123、267 等）归 invalid_path，
    其余归 subprocess_error。文本特征与错误号任一命中即算路径问题。
    """
    winerror = getattr(error, "winerror", None)
    errno = getattr(error, "errno", None)
    pathish = winerror in _PATHISH_WIN_ERRNOS or errno in _PATHISH_WIN_ERRNOS
    if pathish or looks_like_invalid_path_error(str(error)):
        return ErrorKind.INVALID_PATH
    return ErrorKind.SUBPROCESS_ERROR
