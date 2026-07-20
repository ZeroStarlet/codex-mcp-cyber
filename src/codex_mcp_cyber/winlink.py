"""ASCII 目录联接：让 Codex 只看见纯 ASCII 的工作目录。

深 module —— 对外只有 ``prefer_codex_workdir`` 一个函数，背后是整套
私有缓存树、owner/ACL 校验与 NTFS reparse point 构造。

Windows 上 Codex 的部分内部工具在非 ASCII（中文等）路径下会触发
os error 123；此处在纯 ASCII 缓存根下建 directory junction 指回真实仓库，
真实仓库位置不变。任何一步失败都回退真实路径（fail-open 到"不加速"，
而不是 fail-open 到"不校验"—— 所有安全检查本身仍是 fail-closed）。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from codex_mcp_cyber.paths import normalize_workdir, path_has_non_ascii
from codex_mcp_cyber.winsec import WinApiSecurity, WinSecurity


def _junction_points_to(link: Path, target: Path) -> bool:
    """link 是否解析到 target（不信任仅存在）。"""
    try:
        return link.resolve() == target.resolve()
    except (OSError, RuntimeError):
        return False



def _ensure_private_user_tree(user_root: Path, *, sec: WinSecurity) -> Path:
    """确保 user_root/wd-junctions 存在且为当前用户私有，返回 wd-junctions 路径。"""
    import secrets

    if os.path.lexists(user_root):
        if sec.path_chain_has_reparse(user_root):
            raise OSError(f"用户缓存根路径链含 reparse：{user_root}")
        # 已存在：仅当 owner==me 且可收敛 ACL 才信任；否则 fail-closed
        # （不 /reset 到继承态，避免中间窗口）
        if not sec.restrict_private_dir_acl(user_root):
            raise OSError(f"用户缓存根 ACL/owner 不可信：{user_root}")
    else:
        if sec.path_chain_has_reparse(user_root.parent):
            raise OSError(f"用户缓存根父链含 reparse：{user_root.parent}")
        sec.create_private_dir_atomic(user_root)

    root = user_root / "wd-junctions"
    if os.path.lexists(root):
        if sec.is_reparse_point(root) or sec.path_chain_has_reparse(root):
            raise OSError(f"路径已是 reparse：{root}")
        if not sec.restrict_private_dir_acl(root):
            raise OSError(f"无法设置缓存 ACL：{root}")
    else:
        sec.create_private_dir_atomic(root)
        # 原子创建后无需 icacls；再验链
        if sec.path_chain_has_reparse(root):
            raise OSError(f"缓存路径链含 reparse：{root}")

    if sec.path_chain_has_reparse(root):
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
        if sec.is_reparse_point(probe):
            raise OSError(f"缓存探测文件为 reparse：{probe}")
    finally:
        try:
            os.unlink(str(probe))
        except OSError:
            pass
    return root


def _junction_cache_root(*, sec: WinSecurity, cache_base: Path | None = None) -> Path:
    """返回**每用户独立**的纯 ASCII 缓存根（无跨用户共享父目录）。

    路径：``C:\\codex-mcp-cyber-v3-<sidhash>\\wd-junctions``

    新建目录使用 CreateDirectoryW + 限制性 SDDL（原子 DACL），
    已存在目录要求 owner==当前 SID 并收敛 ACL。

    ``cache_base``：用户私有根。生产传 None（由 Windows 目录盘符 + SID 哈希
    推导）；测试显式传入 —— 它是正式参数，不是模块全局变量。
    """
    if os.name != "nt":
        raise OSError("junction cache 仅 Windows")

    sid = sec.current_user_sid()
    if not sid:
        raise OSError("无法从进程令牌获取 SID，拒绝使用缓存")

    user_hash = hashlib.sha1(sid.encode("utf-8")).hexdigest()[:12]

    if cache_base is not None:
        candidates = [Path(cache_base)]
    else:
        windir = sec.windows_directory()
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
            return _ensure_private_user_tree(user_root, sec=sec)
        except OSError as e:
            last_err = e
            continue
    raise OSError(f"无法创建 ASCII junction 缓存目录: {last_err}")


def _create_windows_junction(link: Path, target: Path, *, sec: WinSecurity) -> None:
    """创建 directory junction（IO_REPARSE_TAG_MOUNT_POINT），不经 cmd。

    Junction 通常无需管理员；CreateSymbolicLink 可能无特权。
    失败抛 OSError，由 prefer_codex_workdir 回退。
    """
    import ctypes
    from ctypes import wintypes

    link = Path(link)
    target = Path(target)
    # 父目录必须由 _ensure_private_user_tree 建好并验过（owner + protected DACL）。
    # 此处**不**用 mkdir(parents=True) 顺手创建 —— 那会绕开整套私有目录策略，
    # 拿到一个继承父级 ACL、未验 owner 的缓存根。缺失即 fail-closed。
    if not link.parent.is_dir():
        raise OSError(f"联接缓存父目录不存在或未经校验：{link.parent}")

    # lexists：dangling reparse 也要处理
    if os.path.lexists(link):
        # 复用前：必须能取 SID，且 link owner == 当前 SID
        sid = sec.current_user_sid()
        if not sid:
            raise OSError("无法获取当前 SID，拒绝复用路径别名")
        owner = sec.path_owner_sid(link)
        if owner is None or owner.upper() != sid.upper():
            try:
                if sec.is_reparse_point(link) or link.is_symlink():
                    link.rmdir()
                else:
                    raise OSError(f"联接路径被非当前用户占用：{link}")
            except OSError as e:
                raise OSError(f"无法替换外来路径别名 {link}: {e}") from e
        elif _junction_points_to(link, target):
            return
        try:
            if link.is_dir() and not sec.is_reparse_point(link) and not link.is_symlink():
                try:
                    next(link.iterdir())
                except StopIteration:
                    link.rmdir()
                else:
                    raise OSError(f"联接路径已被非空目录占用：{link}")
            elif sec.is_reparse_point(link) or link.is_symlink():
                link.rmdir()
            else:
                link.unlink(missing_ok=True)  # type: ignore[call-arg]
        except OSError as e:
            raise OSError(f"无法替换已有路径别名 {link}: {e}") from e

    # 与缓存树同一套策略：owner=当前 SID + protected DACL，原子创建。
    # 这个目录正是最终交给 Codex 的路径，不能比缓存根更宽松。
    sec.create_private_dir_atomic(link)

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


def prefer_codex_workdir(
    path: Path,
    *,
    sec: WinSecurity | None = None,
    cache_base: Path | None = None,
) -> Path:
    """为 Codex 选择实际使用的工作目录。

    Windows + 非 ASCII 路径时，在纯 ASCII 缓存根下建目录联接，让 Codex
    只看见 ASCII 路径。联接创建失败时回退真实路径。

    ``sec``：Windows 安全原语 adapter，默认 WinApiSecurity()。
    ``cache_base``：私有缓存根，默认由盘符 + SID 哈希推导。

    入参既可以是 Path 也可以是待归一的字符串 —— 字符串会先过
    normalize_workdir，Path 视为调用方已归一。
    """
    active_sec: WinSecurity = sec if sec is not None else WinApiSecurity()
    resolved = path if isinstance(path, Path) else normalize_workdir(path)
    if os.name != "nt" or not path_has_non_ascii(resolved):
        return resolved
    if not resolved.is_dir():
        return resolved

    digest = hashlib.sha1(str(resolved).encode("utf-8", errors="replace")).hexdigest()[
        :16
    ]
    try:
        link = _junction_cache_root(sec=active_sec, cache_base=cache_base) / digest
        _create_windows_junction(link, resolved, sec=active_sec)
        if not _junction_points_to(link, resolved):
            return resolved
        # 切勿 link.resolve() 作为返回值——会跟随联接回到中文真实路径。
        alias = Path(os.path.abspath(str(link)))
        if path_has_non_ascii(alias):
            return resolved
        return alias
    except (OSError, OverflowError, ValueError, RuntimeError):
        return resolved

