"""WinSecurity 的内存 adapter。

让 winlink 的加固逻辑能在不碰真实 SID / ACL / reparse 的前提下被测 ——
测试穿过 interface，而不是 monkeypatch 生产模块的私有符号。

真实 WinAPI 行为（DACL 形状、owner 归属）仍由针对 WinApiSecurity 的
真机测试覆盖；此处只替换原语，不替换被测逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FakeWinSecurity:
    """可编程的 WinSecurity adapter。

    sid=None 模拟「取不到进程令牌 SID」；acl_ok=False 模拟「ACL 收敛失败」；
    reparse 集合里的路径视为 reparse point。
    """

    sid: str | None = "S-1-5-21-me"
    owners: dict[str, str] = field(default_factory=dict)
    reparse: set[str] = field(default_factory=set)
    acl_ok: bool = True
    windir: str | None = r"C:\Windows"

    # 所有路径链检查一律判为 reparse
    all_chains_reparse: bool = False
    # 创建私有目录一律失败（用于「候选路径形状」类测试：候选会被记入
    # create_attempts 后立即失败，从而把生产实际选中的缓存根暴露出来）
    fail_create: bool = False

    # 调用留痕，供「顺序 / 是否被调用 / 尝试了哪些路径」类断言使用
    acl_calls: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    create_attempts: list[str] = field(default_factory=list)
    chain_calls: list[str] = field(default_factory=list)

    def current_user_sid(self) -> str | None:
        return self.sid

    def path_owner_sid(self, path: Path) -> str | None:
        return self.owners.get(str(path), self.sid)

    def is_reparse_point(self, path: Path) -> bool:
        return str(path) in self.reparse

    def path_chain_has_reparse(self, path: Path) -> bool:
        self.chain_calls.append(str(path))
        if self.all_chains_reparse:
            return True
        cur = Path(path)
        while True:
            if str(cur) in self.reparse:
                return True
            if cur.parent == cur:
                return False
            cur = cur.parent

    def restrict_private_dir_acl(self, path: Path) -> bool:
        self.acl_calls.append(str(path))
        return self.acl_ok

    def create_private_dir_atomic(self, path: Path) -> None:
        self.create_attempts.append(str(path))
        if self.fail_create:
            raise OSError(f"fake: 拒绝创建 {path}")
        Path(path).mkdir(parents=False, exist_ok=False)
        self.created.append(str(path))

    def windows_directory(self) -> str | None:
        return self.windir
