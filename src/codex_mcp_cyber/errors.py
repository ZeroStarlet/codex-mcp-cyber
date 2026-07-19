"""审核错误种类与路径/认证判定。"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
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


class InvalidWorkdirError(ValueError):
    """工作目录输入无法无歧义地归一（应映射为 invalid_path，不得静默改道）。"""


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
    if isinstance(cd, Path):
        text = str(cd)
    else:
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
    审核场景应传入 codex_cd，避免图片落到 MCP 服务 cwd。
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


def _is_reparse_point(path: Path) -> bool:
    """Windows：路径是否为 reparse point（junction/symlink）。

    - 不存在 → False（允许首次创建缓存目录）
    - 权限/其他 IO 错误 → True（fail-closed，不信任）
    """
    if os.name != "nt":
        try:
            return path.is_symlink()
        except FileNotFoundError:
            return False
        except OSError:
            return True
    try:
        st = os.lstat(path)
        attrs = getattr(st, "st_file_attributes", None)
        if attrs is None:
            try:
                return path.is_symlink()
            except FileNotFoundError:
                return False
            except OSError:
                return True
        # FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        return bool(attrs & 0x400)
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _path_chain_has_reparse(path: Path) -> bool:
    """从 path 向上到根，任一组件是 reparse 则不可信。

    用 lstat（经 _is_reparse_point），不先 exists()——否则会漏 dangling reparse。
    """
    cur = path
    seen: set[str] = set()
    while True:
        key = str(cur)
        if key in seen:
            break
        seen.add(key)
        try:
            if _is_reparse_point(cur):
                return True
        except OSError:
            return True
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return False


def _current_user_sid() -> str | None:
    """从当前进程令牌取 SID 字符串（不读可伪造的 USERNAME 环境变量）。"""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    TOKEN_QUERY = 0x0008
    TokenUser = 1

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", SID_AND_ATTRIBUTES)]

    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(
        kernel.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        return None
    try:
        size = wintypes.DWORD(0)
        advapi.GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(size))
        if size.value == 0:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(
            token, TokenUser, buf, size, ctypes.byref(size)
        ):
            return None
        tu = TOKEN_USER.from_buffer(buf)
        sid_str = ctypes.c_wchar_p()
        if not advapi.ConvertSidToStringSidW(tu.User.Sid, ctypes.byref(sid_str)):
            return None
        try:
            return sid_str.value
        finally:
            if sid_str:
                kernel.LocalFree(sid_str)
    finally:
        kernel.CloseHandle(token)


def _system32_icacls() -> str | None:
    """可信的 icacls 绝对路径；不在 PATH / cwd 搜索，不信可写的 SystemRoot 环境变量。"""
    windir = _windows_directory()
    if not windir:
        return None
    icacls = os.path.normpath(os.path.join(windir, "System32", "icacls.exe"))
    if not os.path.isabs(icacls):
        return None
    if not os.path.isfile(icacls):
        return None
    return icacls


def _windows_directory() -> str | None:
    """真实 Windows 目录：仅 WinAPI。失败返回 None（不 fail-open 到 C:\\Windows）。"""
    if os.name != "nt":
        return None
    try:
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        GetSystemWindowsDirectoryW = kernel.GetSystemWindowsDirectoryW
        GetSystemWindowsDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
        GetSystemWindowsDirectoryW.restype = ctypes.c_uint
        n = GetSystemWindowsDirectoryW(None, 0)
        if n == 0:
            return None
        buf = ctypes.create_unicode_buffer(n + 1)
        got = GetSystemWindowsDirectoryW(buf, len(buf))
        if got and buf.value and os.path.isabs(buf.value):
            if os.path.isdir(os.path.join(buf.value, "System32")):
                return buf.value
    except (AttributeError, OSError, ValueError, TypeError):
        return None
    return None


def _path_owner_sid(path: Path) -> str | None:
    """读取路径 owner SID；失败返回 None。"""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    OWNER_SECURITY_INFORMATION = 0x00000001
    SE_FILE_OBJECT = 1

    pSD = ctypes.c_void_p()
    pOwner = ctypes.c_void_p()
    GetNamedSecurityInfoW = advapi.GetNamedSecurityInfoW
    GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    GetNamedSecurityInfoW.restype = wintypes.DWORD
    rc = GetNamedSecurityInfoW(
        str(path),
        SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION,
        ctypes.byref(pOwner),
        None,
        None,
        None,
        ctypes.byref(pSD),
    )
    if rc != 0:
        return None
    try:
        sid_str = ctypes.c_wchar_p()
        advapi.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
        if not advapi.ConvertSidToStringSidW(pOwner, ctypes.byref(sid_str)):
            return None
        try:
            return sid_str.value
        finally:
            if sid_str:
                kernel.LocalFree(sid_str)
    finally:
        if pSD:
            kernel.LocalFree(pSD)


def _run_icacls(args: list[str]) -> bool:
    try:
        import subprocess

        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
        )
        return completed.returncode == 0
    except OSError:
        return False


def _restrict_shared_base_acl(path: Path) -> bool:
    """兼容旧名：共享 base 已废弃，转私有策略。"""
    return _restrict_private_dir_acl(path)


def _restrict_private_dir_acl(path: Path) -> bool:
    """将已存在私有目录的 DACL **一次性**替换为 allowlist（WinAPI）。

    不经多步 icacls /reset，避免中间继承态。
    要求 owner==当前 SID；失败 fail-closed。
    """
    if os.name != "nt":
        return True
    sid = _current_user_sid()
    if not sid:
        return False
    if not os.path.lexists(path):
        return False
    owner = _path_owner_sid(path)
    if owner is None or owner.upper() != sid.upper():
        return False
    if _is_reparse_point(path):
        return False

    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    # protected DACL only（D:P）；不改 owner（已校验）
    sddl = (
        f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
        f"(A;OICI;FA;;;{sid})(A;OICI;GRGX;;;AU)"
    )
    SDDL_REVISION_1 = 1
    pSD = ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, SDDL_REVISION_1, ctypes.byref(pSD), None
    ):
        return False

    try:
        # 取出 DACL 指针
        pDacl = ctypes.c_void_p()
        bDaclPresent = wintypes.BOOL()
        bDaclDefaulted = wintypes.BOOL()
        if not advapi.GetSecurityDescriptorDacl(
            pSD,
            ctypes.byref(bDaclPresent),
            ctypes.byref(pDacl),
            ctypes.byref(bDaclDefaulted),
        ):
            return False
        if not bDaclPresent:
            return False

        DACL_SECURITY_INFORMATION = 0x00000004
        PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
        SE_FILE_OBJECT = 1
        SetNamedSecurityInfoW = advapi.SetNamedSecurityInfoW
        SetNamedSecurityInfoW.argtypes = [
            wintypes.LPWSTR,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        SetNamedSecurityInfoW.restype = wintypes.DWORD
        rc = SetNamedSecurityInfoW(
            str(path),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            pDacl,
            None,
        )
        if rc != 0:
            return False
    finally:
        if pSD:
            kernel.LocalFree(pSD)

    owner2 = _path_owner_sid(path)
    return owner2 is not None and owner2.upper() == sid.upper()


def _restrict_dir_acl_current_user(path: Path) -> bool:
    """兼容旧名：按私有目录策略收敛。"""
    return _restrict_private_dir_acl(path)


def _junction_points_to(link: Path, target: Path) -> bool:
    """link 是否解析到 target（不信任仅存在）。"""
    try:
        return link.resolve() == target.resolve()
    except (OSError, RuntimeError):
        return False


# 测试可注入用户私有根（生产保持 None）
_JUNCTION_BASE_OVERRIDE: Path | None = None


def _create_private_dir_atomic(path: Path) -> None:
    """以限制性 DACL **原子创建**目录（CreateDirectoryW + SDDL）。

    创建时即 owner=当前 SID，且 DACL 为 SYSTEM/Admins/me F + AuthUsers RX，
    不继承父目录 Authenticated Users Modify，消除 create→icacls 竞态窗口。
    若路径已存在则抛 FileExistsError。
    """
    if os.name != "nt":
        path.mkdir(parents=False, exist_ok=False)
        return

    sid = _current_user_sid()
    if not sid:
        raise OSError("无法获取 SID，拒绝创建缓存目录")

    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    # SDDL: protected DACL, owner=me
    sddl = (
        f"O:{sid}G:BA"
        f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
        f"(A;OICI;FA;;;{sid})(A;OICI;GRGX;;;AU)"
    )

    SDDL_REVISION_1 = 1
    pSD = ctypes.c_void_p()
    sd_size = wintypes.ULONG()
    Convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    Convert.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.ULONG),
    ]
    Convert.restype = wintypes.BOOL
    if not Convert(sddl, SDDL_REVISION_1, ctypes.byref(pSD), ctypes.byref(sd_size)):
        err = ctypes.get_last_error()
        raise OSError(err, f"SDDL 转换失败: {sddl[:80]}...")

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    sa = SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
    sa.lpSecurityDescriptor = pSD
    sa.bInheritHandle = False

    try:
        CreateDirectoryW = kernel.CreateDirectoryW
        CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
        CreateDirectoryW.restype = wintypes.BOOL
        if not CreateDirectoryW(str(path), ctypes.byref(sa)):
            err = ctypes.get_last_error()
            # ERROR_ALREADY_EXISTS = 183
            if err == 183:
                raise FileExistsError(err, f"目录已存在：{path}")
            raise OSError(err, f"CreateDirectoryW 失败：{path}")
    finally:
        if pSD:
            kernel.LocalFree(pSD)

    # 创建后立刻校验 owner / 非 reparse
    if _is_reparse_point(path):
        raise OSError(f"创建后为 reparse：{path}")
    owner = _path_owner_sid(path)
    if owner is None or owner.upper() != sid.upper():
        raise OSError(f"创建后 owner 不是当前 SID：{path}")


def _ensure_private_user_tree(user_root: Path) -> Path:
    """确保 user_root/wd-junctions 存在且为当前用户私有，返回 wd-junctions 路径。"""
    import secrets

    if os.path.lexists(user_root):
        if _path_chain_has_reparse(user_root):
            raise OSError(f"用户缓存根路径链含 reparse：{user_root}")
        # 已存在：仅当 owner==me 且可收敛 ACL 才信任；否则 fail-closed
        # （不 /reset 到继承态，避免中间窗口）
        if not _restrict_private_dir_acl(user_root):
            raise OSError(f"用户缓存根 ACL/owner 不可信：{user_root}")
    else:
        if _path_chain_has_reparse(user_root.parent):
            raise OSError(f"用户缓存根父链含 reparse：{user_root.parent}")
        _create_private_dir_atomic(user_root)

    root = user_root / "wd-junctions"
    if os.path.lexists(root):
        if _is_reparse_point(root) or _path_chain_has_reparse(root):
            raise OSError(f"路径已是 reparse：{root}")
        if not _restrict_private_dir_acl(root):
            raise OSError(f"无法设置缓存 ACL：{root}")
    else:
        _create_private_dir_atomic(root)
        # 原子创建后无需 icacls；再验链
        if _path_chain_has_reparse(root):
            raise OSError(f"缓存路径链含 reparse：{root}")

    if _path_chain_has_reparse(root):
        raise OSError(f"缓存路径链含 reparse：{root}")

    probe = root / f".wp-{os.getpid()}-{secrets.token_hex(8)}"
    if os.path.lexists(probe):
        raise OSError(f"探测路径已存在：{probe}")
    fd = os.open(str(probe), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, b"ok")
    finally:
        os.close(fd)
    try:
        if _is_reparse_point(probe):
            raise OSError(f"缓存探测文件为 reparse：{probe}")
    finally:
        try:
            os.unlink(str(probe))
        except OSError:
            pass
    return root


def _junction_cache_root() -> Path:
    """返回**每用户独立**的纯 ASCII 缓存根（无跨用户共享父目录）。

    路径：``C:\\codex-mcp-cyber-v3-<sidhash>\\wd-junctions``

    新建目录使用 CreateDirectoryW + 限制性 SDDL（原子 DACL），
    已存在目录要求 owner==当前 SID 并收敛 ACL。
    测试可设 ``_JUNCTION_BASE_OVERRIDE`` 作为用户私有根。
    """
    if os.name != "nt":
        raise OSError("junction cache 仅 Windows")

    sid = _current_user_sid()
    if not sid:
        raise OSError("无法从进程令牌获取 SID，拒绝使用缓存")

    user_hash = hashlib.sha1(sid.encode("utf-8")).hexdigest()[:12]

    if _JUNCTION_BASE_OVERRIDE is not None:
        candidates = [Path(_JUNCTION_BASE_OVERRIDE)]
    else:
        windir = _windows_directory()
        if not windir:
            raise OSError("无法解析 Windows 目录")
        drive = os.path.splitdrive(windir)[0]
        # 严格 [A-Za-z]: ，无 "C:" fail-open，无 UNC
        if not drive or len(drive) != 2 or drive[1] != ":" or not (
            "A" <= drive[0].upper() <= "Z"
        ):
            raise OSError(f"非本地盘符 Windows 目录：{windir!r}")
        candidates = [Path(drive.upper()[0] + ":\\") / f"codex-mcp-cyber-v3-{user_hash}"]

    last_err: OSError | None = None
    for user_root in candidates:
        if path_has_non_ascii(user_root):
            continue
        try:
            return _ensure_private_user_tree(user_root)
        except OSError as e:
            last_err = e
            continue
    raise OSError(f"无法创建 ASCII junction 缓存目录: {last_err}")


def _create_windows_junction(link: Path, target: Path) -> None:
    """创建 directory junction（IO_REPARSE_TAG_MOUNT_POINT），不经 cmd。

    Junction 通常无需管理员；CreateSymbolicLink 可能无特权。
    失败抛 OSError，由 prefer_codex_workdir 回退。
    """
    import ctypes
    from ctypes import wintypes

    link = Path(link)
    target = Path(target)
    link.parent.mkdir(parents=True, exist_ok=True)

    # lexists：dangling reparse 也要处理
    if os.path.lexists(link):
        # 复用前：必须能取 SID，且 link owner == 当前 SID
        sid = _current_user_sid()
        if not sid:
            raise OSError("无法获取当前 SID，拒绝复用路径别名")
        owner = _path_owner_sid(link)
        if owner is None or owner.upper() != sid.upper():
            try:
                if _is_reparse_point(link) or link.is_symlink():
                    link.rmdir()
                else:
                    raise OSError(f"联接路径被非当前用户占用：{link}")
            except OSError as e:
                raise OSError(f"无法替换外来路径别名 {link}: {e}") from e
        elif _junction_points_to(link, target):
            return
        try:
            if link.is_dir() and not _is_reparse_point(link) and not link.is_symlink():
                try:
                    next(link.iterdir())
                except StopIteration:
                    link.rmdir()
                else:
                    raise OSError(f"联接路径已被非空目录占用：{link}")
            elif _is_reparse_point(link) or link.is_symlink():
                link.rmdir()
            else:
                link.unlink(missing_ok=True)  # type: ignore[call-arg]
        except OSError as e:
            raise OSError(f"无法替换已有路径别名 {link}: {e}") from e

    link.mkdir(parents=False, exist_ok=False)

    target_abs = os.path.abspath(str(target))
    if target_abs.startswith("\\\\"):
        # UNC → \??\UNC\server\share\...
        body = "UNC\\" + target_abs.lstrip("\\")
    else:
        body = target_abs
    if not body.endswith("\\"):
        body += "\\"
    substitute = "\\??\\" + body
    print_name = body

    sub_b = substitute.encode("utf-16-le")
    print_b = print_name.encode("utf-16-le")
    # PathBuffer 内两串均需 UTF-16 NUL 终止；Length 字段不含 NUL
    nul = b"\x00\x00"
    path_b = sub_b + nul + print_b + nul
    # MountPointReparseBuffer header = 4 * USHORT = 8
    reparse_data_length = 8 + len(path_b)
    # Windows reparse data 上限约 16KB
    if reparse_data_length > 16384:
        raise OSError(f"路径过长，无法创建 junction: {target}")

    try:
        buf = bytearray()
        buf += (0xA0000003).to_bytes(4, "little")  # IO_REPARSE_TAG_MOUNT_POINT
        buf += reparse_data_length.to_bytes(2, "little")
        buf += (0).to_bytes(2, "little")  # Reserved
        buf += (0).to_bytes(2, "little")  # SubstituteNameOffset
        buf += len(sub_b).to_bytes(2, "little")  # SubstituteNameLength (no NUL)
        buf += (len(sub_b) + 2).to_bytes(2, "little")  # PrintNameOffset (after sub+NUL)
        buf += len(print_b).to_bytes(2, "little")  # PrintNameLength (no NUL)
        buf += path_b
    except OverflowError as e:
        raise OSError(f"路径过长，无法创建 junction: {target}") from e

    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_SHARE_READ = 0x1
    FILE_SHARE_WRITE = 0x2
    FILE_SHARE_DELETE = 0x4
    FSCTL_SET_REPARSE_POINT = 0x000900A4
    INVALID_HANDLE = ctypes.c_void_p(-1).value

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    k32.DeviceIoControl.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL

    handle = k32.CreateFileW(
        str(link),
        GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle == INVALID_HANDLE or handle is None or int(handle) == -1:
        err = ctypes.get_last_error()
        try:
            link.rmdir()
        except OSError:
            pass
        raise OSError(err, f"CreateFileW 打开 junction 失败: {link}")

    c_buf = (ctypes.c_char * len(buf)).from_buffer_copy(bytes(buf))
    returned = wintypes.DWORD(0)
    try:
        ok = k32.DeviceIoControl(
            handle,
            FSCTL_SET_REPARSE_POINT,
            c_buf,
            len(buf),
            None,
            0,
            ctypes.byref(returned),
            None,
        )
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(err, f"DeviceIoControl 设置 junction 失败: {link} → {target}")
    finally:
        k32.CloseHandle(handle)

    if not link.exists():
        raise OSError(f"创建 junction 后路径不存在: {link}")


def prefer_codex_workdir(path: Path) -> Path:
    """为 Codex 选择实际使用的工作目录。

    Windows + 非 ASCII 路径时，在纯 ASCII 缓存根下建目录联接，让 Codex
    只看见 ASCII 路径。联接创建失败时回退真实路径。
    """
    resolved = path if isinstance(path, Path) else normalize_workdir(path)
    if os.name != "nt" or not path_has_non_ascii(resolved):
        return resolved
    if not resolved.is_dir():
        return resolved

    digest = hashlib.sha1(str(resolved).encode("utf-8", errors="replace")).hexdigest()[
        :16
    ]
    try:
        link = _junction_cache_root() / digest
        _create_windows_junction(link, resolved)
        if not _junction_points_to(link, resolved):
            return resolved
        # 切勿 link.resolve() 作为返回值——会跟随联接回到中文真实路径。
        alias = Path(os.path.abspath(str(link)))
        if path_has_non_ascii(alias):
            return resolved
        return alias
    except (OSError, OverflowError, ValueError, RuntimeError):
        return resolved


def looks_like_invalid_path_error(text: str) -> bool:
    return bool(_OS_ERROR_123_RE.search(text))


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


def redact_tool_result_event(event: dict[str, Any]) -> dict[str, Any]:
    """对已解析 JSON 事件做 tool_result 脱敏（deepcopy）。

    仅当 item.type == tool_result 且存在 content 键时，将 content 置为 "[truncated]"。
    """
    safe = copy.deepcopy(event)
    item = safe.get("item", {})
    if isinstance(item, dict) and item.get("type") == "tool_result" and "content" in item:
        item["content"] = "[truncated]"
    return safe


def filter_last_lines(lines: list[str], max_lines: int = 50) -> list[str]:
    """过滤 last_lines，脱敏 tool_result 中的大内容。"""
    filtered: list[str] = []
    for line in lines:
        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                filtered.append(line)
                continue
            item = data.get("item", {})
            if isinstance(item, dict) and item.get("type") == "tool_result":
                redacted = redact_tool_result_event(data)
                filtered.append(json.dumps(redacted, ensure_ascii=False))
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
