"""Windows 文件系统安全原语 —— WinSecurity seam。

生产 adapter 走 WinAPI（ctypes）；测试 adapter 用内存表，让 winlink 的
路径加固逻辑能在不碰真实 ACL / SID 的前提下被测。

seam 的存在理由是两个真实 adapter，不是一个假想的可能性：
- WinApiSecurity —— 生产，advapi32 / kernel32
- 测试侧 fake —— 见 tests/winsec_fake.py

实现体就在 WinApiSecurity 的方法里，内部组合一律经 ``self``（链走查调用
``self.is_reparse_point``、ACL 收敛调用 ``self.current_user_sid`` 等），
因此子类可按方法粒度替换单个原语来测组合逻辑，无需 monkeypatch 模块符号。

非 Windows 平台上除 is_reparse_point 外均为 no-op / None，
调用方（winlink）本就只在 os.name == "nt" 时进入。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


def _private_dacl_sddl(sid: str) -> str:
    """私有目录 DACL allowlist —— 单一来源。

    SYSTEM / Administrators / 当前用户 = 完全控制（FA）；
    Authenticated Users = 只读执行（GRGX）。
    ``D:P`` = protected，不继承父目录的宽松 ACE。

    创建期与收敛期必须用同一份 allowlist，否则两条路径会各自漂移，
    而只有其中一条会被测到。
    """
    return (
        f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
        f"(A;OICI;FA;;;{sid})(A;OICI;GRGX;;;AU)"
    )


class WinSecurity(Protocol):
    """Windows 安全原语 seam。

    这是 adapter 边界 —— 方法个数由 WinAPI 的形状决定，天然偏薄。
    深的是它上面的 winlink.prefer_codex_workdir：一个函数背后藏着
    整套联接缓存与加固策略。
    """

    def current_user_sid(self) -> str | None: ...
    def path_owner_sid(self, path: Path) -> str | None: ...
    def is_reparse_point(self, path: Path) -> bool: ...
    def path_chain_has_reparse(self, path: Path) -> bool: ...
    def restrict_private_dir_acl(self, path: Path) -> bool: ...
    def create_private_dir_atomic(self, path: Path) -> None: ...
    def windows_directory(self) -> str | None: ...


class WinApiSecurity:
    """生产 adapter：advapi32 / kernel32。

    方法即实现；组合型原语（path_chain_has_reparse /
    restrict_private_dir_acl / create_private_dir_atomic）经 ``self``
    调用叶子原语。
    """

    def current_user_sid(self) -> str | None:
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

    def path_owner_sid(self, path: Path) -> str | None:
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

    def is_reparse_point(self, path: Path) -> bool:
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

    def path_chain_has_reparse(self, path: Path) -> bool:
        """从 path 向上到根，任一组件是 reparse 则不可信。

        用 lstat（经 ``self.is_reparse_point``），不先 exists()——
        否则会漏 dangling reparse。
        """
        cur = path
        seen: set[str] = set()
        while True:
            key = str(cur)
            if key in seen:
                break
            seen.add(key)
            try:
                if self.is_reparse_point(cur):
                    return True
            except OSError:
                return True
            parent = cur.parent
            if parent == cur:
                break
            cur = parent
        return False

    def restrict_private_dir_acl(self, path: Path) -> bool:
        """将已存在私有目录的 DACL **一次性**替换为 allowlist（WinAPI）。

        不经多步 icacls /reset，避免中间继承态。
        要求 owner==当前 SID；失败 fail-closed。
        """
        if os.name != "nt":
            return True
        sid = self.current_user_sid()
        if not sid:
            return False
        if not os.path.lexists(path):
            return False
        owner = self.path_owner_sid(path)
        if owner is None or owner.upper() != sid.upper():
            return False
        if self.is_reparse_point(path):
            return False

        import ctypes
        from ctypes import wintypes

        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)

        # protected DACL only（D:P）；不改 owner（已校验）
        sddl = _private_dacl_sddl(sid)
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

        owner2 = self.path_owner_sid(path)
        return owner2 is not None and owner2.upper() == sid.upper()

    def create_private_dir_atomic(self, path: Path) -> None:
        """以限制性 DACL **原子创建**目录（CreateDirectoryW + SDDL）。

        创建时即 owner=当前 SID，且 DACL 为 SYSTEM/Admins/me F + AuthUsers RX，
        不继承父目录 Authenticated Users Modify，消除 create→icacls 竞态窗口。
        若路径已存在则抛 FileExistsError。
        """
        if os.name != "nt":
            path.mkdir(parents=False, exist_ok=False)
            return

        sid = self.current_user_sid()
        if not sid:
            raise OSError("无法获取 SID，拒绝创建缓存目录")

        import ctypes
        from ctypes import wintypes

        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)

        # SDDL: owner=me + 与收敛期同一份 protected DACL
        sddl = f"O:{sid}G:BA" + _private_dacl_sddl(sid)

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
        if self.is_reparse_point(path):
            raise OSError(f"创建后为 reparse：{path}")
        owner = self.path_owner_sid(path)
        if owner is None or owner.upper() != sid.upper():
            raise OSError(f"创建后 owner 不是当前 SID：{path}")

    def windows_directory(self) -> str | None:
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
