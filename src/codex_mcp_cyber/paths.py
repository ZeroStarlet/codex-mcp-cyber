"""工作目录归一与 CLI 路径格式化。

把 MCP / agent 传来的路径文本收成一个可信的 Path，或明确拒绝。
纯文本处理 —— 不碰 ACL、不建目录、不解析联接（那些在 winsec / winlink）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path


class InvalidWorkdirError(ValueError):
    """工作目录输入无法无歧义地归一（应映射为 invalid_path，不得静默改道）。"""



# 仅剥**成对**包裹的引号（含弯引号 / 全角）；不剥单侧合法引号。
_QUOTE_PAIRS = (
    ('"', '"'),
    ("'", "'"),
    ("“", "”"),
    ("‘", "’"),
    ("＂", "＂"),
    ("＇", "＇"),
    ("`", "`"),
)

def _strip_wrapping_quotes(text: str) -> str:
    """剥多层成对包裹引号。"""
    s = text.strip()
    changed = True
    while changed and len(s) >= 2:
        changed = False
        for left, right in _QUOTE_PAIRS:
            if s.startswith(left) and s.endswith(right) and len(s) >= len(left) + len(right):
                s = s[len(left) : -len(right)].strip()
                changed = True
                break
    return s


def _strip_file_uri(text: str) -> str:
    """只接受本地 file URI；拒绝含 host / 多斜杠 UNC 变体。"""
    lower = text.lower()
    if not lower.startswith("file:"):
        return text
    rest = text[5:]
    # file://localhost/C:/... or file://127.0.0.1/C:/...
    m_host = re.match(r"^//(localhost|127\.0\.0\.1)(/.*)$", rest, re.IGNORECASE)
    if m_host:
        rest = m_host.group(2)
    elif rest.startswith("////"):
        raise InvalidWorkdirError(f"不支持 UNC/多斜杠 file URI：{text!r}")
    elif rest.startswith("///"):
        # file:///C:/foo
        rest = rest[2:]  # -> /C:/foo
    elif rest.startswith("//"):
        # file://server/share → 拒绝
        raise InvalidWorkdirError(f"不支持带 host 的 file URI：{text!r}")
    elif rest.startswith("/"):
        # file:/C:/foo
        pass
    else:
        raise InvalidWorkdirError(f"无法解析 file URI：{text!r}")

    # Windows: /C:/... → C:/...
    if re.match(r"^/[A-Za-z]:", rest):
        rest = rest[1:]
    # 拒绝任何 UNC 形态（含混合分隔符 /\server\share）
    if _looks_like_unc(rest):
        raise InvalidWorkdirError(f"不支持 UNC 工作目录 URI：{text!r}")
    return rest


def _looks_like_unc(path_text: str) -> bool:
    """Windows UNC 判定：规范化分隔符后以 \\\\ 开头，或 // 开头。"""
    if not path_text:
        return False
    # 统一成反斜杠再看前缀，挡住 /\\server、\\/server、//server
    norm = path_text.replace("/", "\\")
    return norm.startswith("\\\\")


def path_has_non_ascii(path: Path | str) -> bool:
    try:
        str(path).encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


def normalize_workdir(cd: Path | str) -> Path:
    """把 MCP / agent 传入的工作目录归一成干净 Path。

    严格策略：空串 / 内嵌 NUL / 不可解析 URI → InvalidWorkdirError。
    先剥成对包裹引号，再解析 file URI；不做路径内部有损替换。
    """
    text = str(cd)

    if "\x00" in text:
        raise InvalidWorkdirError("工作目录含 NUL 字节")

    text = text.strip()
    if (text.startswith('\\"') and text.endswith('\\"') and len(text) >= 4) or (
        text.startswith("\\'") and text.endswith("\\'") and len(text) >= 4
    ):
        text = text[2:-2].strip()
    text = _strip_wrapping_quotes(text)
    text = _strip_file_uri(text)
    text = _strip_wrapping_quotes(text)
    if not text:
        raise InvalidWorkdirError("工作目录为空")
    if _is_cwd_alias(text):
        raise InvalidWorkdirError("工作目录为空或未指定")
    if _looks_like_unc(text):
        raise InvalidWorkdirError(f"不支持 UNC 工作目录：{text!r}")
    if os.name == "nt" and _is_drive_relative(text):
        raise InvalidWorkdirError(
            f"不支持盘符相对路径（请传绝对路径）：{text!r}"
        )

    try:
        path = Path(text).expanduser()
    except RuntimeError as e:
        raise InvalidWorkdirError(f"无法展开用户目录：{text!r} ({e})") from e

    try:
        path = Path(os.path.abspath(os.path.normpath(str(path))))
    except (OSError, ValueError) as e:
        raise InvalidWorkdirError(f"无法归一化工作目录：{text!r} ({e})") from e

    if os.name == "nt":
        s = str(path)
        if len(s) > 3:
            s = s.rstrip(" .")
            path = Path(s)
        if _looks_like_unc(s):
            raise InvalidWorkdirError(f"不支持 UNC 工作目录：{text!r}")
    return path


def _is_drive_relative(text: str) -> bool:
    """Windows drive-relative：'C:foo' / 'C:.' / 'C:'（无根分隔符）。"""
    # 绝对：C:\ 或 C:/ 或 \\server
    if len(text) >= 2 and text[1] == ":":
        if len(text) == 2:
            return True  # C:
        return text[2] not in ("\\", "/")
    return False


def _is_cwd_alias(text: str) -> bool:
    """是否表示「当前目录」的空/点路径（.  .\\  .//  ./.），不含 ..。"""
    t = text.strip().replace("\\", "/")
    while "//" in t:
        t = t.replace("//", "/")
    # 去掉尾部 /
    t = t.rstrip("/")
    # 折叠为仅由 "." 段组成的相对路径：. / ./. / ./. /.
    if t in ("", "."):
        return True
    parts = [p for p in t.split("/") if p != ""]
    return bool(parts) and all(p == "." for p in parts)


def format_cli_path(path: Path, *, base: Path | None = None) -> str:
    """传给 codex --cd / Popen cwd / --image 的稳定字符串。

    list argv 不加引号。**不要** Path.resolve()——会跟随目录联接抵消 ASCII 别名。

    base：若 path 为相对路径，相对 base 解析（默认 os.getcwd()）。
    审核场景应传入审核别名（codex_workdir），避免图片落到 MCP 服务 cwd。
    """
    raw = os.path.normpath(str(path))
    if not os.path.isabs(raw):
        root = str(base) if base is not None else os.getcwd()
        raw = os.path.normpath(os.path.join(root, raw))
    else:
        raw = os.path.abspath(raw)
    if os.name == "nt":
        raw = os.path.normpath(raw)
    return raw

