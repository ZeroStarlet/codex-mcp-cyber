"""工作目录归一化与 Windows 路径加固。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codex_mcp_cyber.classify import looks_like_invalid_path_error
from codex_mcp_cyber.paths import (
    InvalidWorkdirError,
    format_cli_path,
    normalize_workdir,
    path_has_non_ascii,
)
from codex_mcp_cyber.winlink import prefer_codex_workdir
from codex_mcp_cyber.process import PopenCodexRunner, ProcessOutcome
from codex_mcp_cyber.review import ReviewRequest, _build_cmd, run_review

from runners import ScriptedLinesRunner
from winsec_fake import FakeWinSecurity


def _ok_jsonl_lines(text: str = "OK", thread_id: str = "sess-1") -> list[str]:
    """一条最小的成功行流：thread.started → agent_message → turn.completed。"""
    import json

    return [
        json.dumps({"type": "thread.started", "thread_id": thread_id}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": text},
            }
        ),
        json.dumps({"type": "turn.completed", "usage": {}}),
    ]


async def _no_sleep(_seconds: float) -> None:
    """退避 seam 的测试 adapter：立即返回，不真的等。"""
    return None


@pytest.mark.parametrize(
    "raw",
    [
        '"C:/Users/you/project"',
        "'C:/Users/you/project'",
        '""C:/Users/you/project""',
        "“C:/Users/you/project”",
        "＇C:/Users/you/project＇",
        "`C:/Users/you/project`",
        "\"'C:/Users/you/project'\"",
    ],
)
def test_normalize_strips_paired_wrapping_quotes(raw: str) -> None:
    p = normalize_workdir(raw)
    assert '"' not in str(p)
    assert "'" not in str(p)
    assert str(p).replace("\\", "/").endswith("project")


def test_normalize_rejects_empty_and_nul() -> None:
    with pytest.raises(InvalidWorkdirError):
        normalize_workdir("")
    with pytest.raises(InvalidWorkdirError):
        normalize_workdir("   ")
    with pytest.raises(InvalidWorkdirError):
        normalize_workdir("C:/a\x00b")


def test_normalize_does_not_strip_unpaired_trailing_quote() -> None:
    # 单侧引号是路径合法字符的一部分（罕见），不得有损剥除
    p = normalize_workdir("C:/Users/you/project'")
    assert str(p).replace("\\", "/").endswith("project'")


def test_normalize_file_uri_windows() -> None:
    p = normalize_workdir("file:///C:/Users/you/project")
    s = str(p).replace("\\", "/")
    assert s.lower().endswith("users/you/project") or "project" in s.lower()
    assert not s.lower().startswith("file:")


def test_normalize_rejects_quoted_remote_file_uri() -> None:
    with pytest.raises(InvalidWorkdirError):
        normalize_workdir('"file://server/share/repo"')
    with pytest.raises(InvalidWorkdirError):
        normalize_workdir("file:////server/share/repo")
    # 混合分隔符绕过
    with pytest.raises(InvalidWorkdirError):
        normalize_workdir(r"file:/\server\share")
    with pytest.raises(InvalidWorkdirError):
        normalize_workdir(r'"file:///\server\share"')
    with pytest.raises(InvalidWorkdirError):
        normalize_workdir(r"\\server\share\repo")


def test_normalize_rejects_path_dot_variants() -> None:
    for raw in (Path("."), ".", "./", ".\\", ".//", "./."):
        with pytest.raises(InvalidWorkdirError):
            normalize_workdir(raw)

def test_normalize_preserves_internal_backslash_quote() -> None:
    # 不得全局删除路径内部的 \'
    p = normalize_workdir(r"C:\folder\'repo")
    s = str(p)
    assert "folder" in s
    # 内部引号必须原样保留 —— 只剥成对包裹引号，不碰路径内部
    assert "'" in s
    assert s.endswith("'repo")


@pytest.mark.asyncio
async def test_codex_tool_empty_cd_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """wire 入口 cd=str，空串不得变成 Path('.') 后静默成功。"""
    from codex_mcp_cyber.tools.codex import codex_tool

    wire = await codex_tool(PROMPT="x", cd="", max_retries=0)
    assert wire["success"] is False
    assert wire["error_kind"] == "invalid_path"


def test_normalize_keeps_ascii_junction_without_following(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已有 ASCII junction 作 cd 时不得 resolve 回中文真实路径。"""
    if os.name != "nt":
        pytest.skip("Windows only")
    cache = tmp_path / "ascii-cache"
    chinese = tmp_path / "审查项目"
    chinese.mkdir()
    (chinese / "m.txt").write_text("ok", encoding="utf-8")
    alias = prefer_codex_workdir(chinese, cache_base=cache)
    assert path_has_non_ascii(alias) is False
    # 关键：normalize 不得跟随 reparse
    normalized = normalize_workdir(str(alias))
    assert path_has_non_ascii(normalized) is False
    assert "wd-junctions" in str(normalized).replace("\\", "/") or str(cache) in str(
        normalized
    )


def test_normalize_path_object_passthrough(tmp_path: Path) -> None:
    # abspath 形式，不强制 resolve 相等
    got = normalize_workdir(tmp_path)
    assert got.is_absolute() or os.path.isabs(str(got))
    assert got.name == tmp_path.name or tmp_path.name in str(got)


def test_format_cli_path_no_quotes(tmp_path: Path) -> None:
    s = format_cli_path(tmp_path)
    assert '"' not in s
    assert "'" not in s
    assert os.path.isabs(s)


def test_format_cli_path_relative_uses_base(tmp_path: Path) -> None:
    s = format_cli_path(Path("shot.png"), base=tmp_path)
    assert s == os.path.normpath(str(tmp_path / "shot.png"))


def test_path_has_non_ascii_detects_chinese() -> None:
    assert path_has_non_ascii("C:/Users/you/审查/repo") is True
    assert path_has_non_ascii("C:/Users/you/repo") is False


def test_looks_like_invalid_path_markers() -> None:
    assert looks_like_invalid_path_error(
        "Error: 文件名、目录名或卷标语法不正确。 (os error 123)"
    )
    assert looks_like_invalid_path_error("WinError 123 something")
    assert looks_like_invalid_path_error("[WinError 123] bad")
    # 不得把 1231 / 1234 当成 123
    assert not looks_like_invalid_path_error("[WinError 1231] network")
    assert not looks_like_invalid_path_error("os error 1234")
    assert not looks_like_invalid_path_error("connection reset")


@pytest.mark.skipif(os.name != "nt", reason="junction is Windows-only")
def test_prefer_codex_workdir_ascii_junction_for_non_ascii(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "ascii-cache"

    chinese = tmp_path / "审查项目"
    chinese.mkdir()
    marker = chinese / "marker.txt"
    marker.write_text("ok", encoding="utf-8")

    preferred = prefer_codex_workdir(chinese, cache_base=cache)
    assert preferred.exists()
    assert path_has_non_ascii(preferred) is False
    cli = format_cli_path(preferred)
    assert path_has_non_ascii(cli) is False
    assert str(cache) in cli or cache.name in cli.replace("\\", "/")
    assert (preferred / "marker.txt").read_text(encoding="utf-8") == "ok"
    again = prefer_codex_workdir(chinese, cache_base=cache)
    assert again == preferred


@pytest.mark.skipif(os.name != "nt", reason="Windows path formatting")
def test_build_cmd_uses_formatted_path_without_quotes(tmp_path: Path) -> None:
    req = ReviewRequest(prompt="x", cd=tmp_path)
    cmd = _build_cmd(req, tmp_path)
    assert "--cd" in cmd
    cd_arg = cmd[cmd.index("--cd") + 1]
    assert '"' not in cd_arg
    assert "'" not in cd_arg


def test_build_cmd_image_relative_to_codex_cd(tmp_path: Path) -> None:
    req = ReviewRequest(prompt="x", cd=tmp_path, image=[Path("shot.png")])
    cmd = _build_cmd(req, tmp_path)
    assert "--image" in cmd
    img = cmd[cmd.index("--image") + 1]
    assert img == os.path.normpath(str(tmp_path / "shot.png"))


# ── 复审（re-review）argv 形状 ────────────────────────────────────────────
# CONTEXT.md 把复审定为一等领域词，但此前整条路径零测试。
# codex CLI 契约：`codex exec [OPTIONS] <COMMAND> [ARGS]`，
# 即 resume 子命令必须排在所有 flag **之后**。


def test_build_cmd_initial_review_has_no_resume(tmp_path: Path) -> None:
    """初审：session_id 为空 → argv 不得出现 resume。"""
    req = ReviewRequest(prompt="x", cd=tmp_path, session_id="")
    cmd = _build_cmd(req, tmp_path)
    assert "resume" not in cmd


def test_build_cmd_re_review_appends_resume_after_all_flags(tmp_path: Path) -> None:
    """复审：resume <id> 必须是 argv 末尾，且排在每一个 flag 之后。"""
    sid = "01234567-89ab-cdef-0123-456789abcdef"
    req = ReviewRequest(
        prompt="x",
        cd=tmp_path,
        session_id=sid,
        model="gpt-5",
        profile="prof",
        yolo=True,
    )
    cmd = _build_cmd(req, tmp_path)

    assert cmd[:2] == ["codex", "exec"]
    # resume 与 id 相邻且收尾
    assert cmd[-2:] == ["resume", sid]
    # 每个 flag 都在 resume 之前 —— CLI 要求 OPTIONS 先于 COMMAND
    resume_at = cmd.index("resume")
    for i, token in enumerate(cmd):
        if token.startswith("--"):
            assert i < resume_at, f"flag {token!r} 排在 resume 之后，CLI 会拒绝"


def test_build_cmd_re_review_keeps_sandbox_and_cd(tmp_path: Path) -> None:
    """复审不得丢掉沙箱策略与工作目录 —— 只读边界在复审同样生效。"""
    req = ReviewRequest(prompt="x", cd=tmp_path, session_id="abc-123")
    cmd = _build_cmd(req, tmp_path)
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert cmd[cmd.index("--cd") + 1] == format_cli_path(tmp_path)


@pytest.mark.asyncio
async def test_workdir_reaches_any_adapter_not_just_popen(tmp_path: Path) -> None:
    """workdir 属于 seam，任何 adapter 都收得到。

    此前它是 PopenCodexRunner 的字段，run_review 靠 isinstance 认出具体
    adapter 才赋值 —— 非 Popen 的 adapter 会静默收不到 cwd。
    """
    runner = ScriptedLinesRunner(lines=_ok_jsonl_lines(), exit_code=0)
    result = await run_review(
        ReviewRequest(prompt="hi", cd=tmp_path, max_retries=0),
        runner=runner,
    )
    assert result.success is True
    assert len(runner.seen_workdirs) == 1
    seen = runner.seen_workdirs[0]
    assert seen is not None, "非 Popen adapter 未收到 workdir —— seam 又漏了"
    assert Path(seen).name == tmp_path.name


@pytest.mark.asyncio
async def test_workdir_is_stable_across_retries(tmp_path: Path) -> None:
    """重试不得改变交给 adapter 的 workdir。"""
    runner = ScriptedLinesRunner(lines=["not-json"], exit_code=1)
    await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=2),
        runner=runner,
        sleep=_no_sleep,
    )
    assert runner.calls == 3
    assert len(set(str(w) for w in runner.seen_workdirs)) == 1


@pytest.mark.asyncio
async def test_run_review_sets_popen_workdir_on_real_runner(
    tmp_path: Path,
) -> None:
    captured: dict = {}

    class _CaptureRunner(PopenCodexRunner):
        def run(self, cmd, *, prompt, workdir=None, timeout, max_duration):  # noqa: ANN001
            captured["cmd"] = list(cmd)
            captured["workdir"] = workdir
            import json

            lines = [
                json.dumps({"type": "thread.started", "thread_id": "s1"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "OK"},
                    }
                ),
                json.dumps({"type": "turn.completed", "usage": {}}),
            ]
            return ProcessOutcome(lines=lines, exit_code=0, raw_output_lines=3)

    runner = _CaptureRunner()
    result = await run_review(
        ReviewRequest(prompt="hi", cd=tmp_path, max_retries=0),
        runner=runner,
    )
    assert result.success is True
    assert captured["workdir"] is not None
    assert "--cd" in captured["cmd"]
    cd_arg = captured["cmd"][captured["cmd"].index("--cd") + 1]
    assert '"' not in cd_arg


@pytest.mark.asyncio
async def test_curly_quoted_missing_path_invalid() -> None:
    result = await run_review(
        ReviewRequest(
            prompt="x",
            cd="“C:/this/path/does/not/exist/cc-curly”",
            max_retries=0,
        ),
        runner=ScriptedLinesRunner(lines=[], exit_code=0),
    )
    assert result.success is False
    assert result.error_kind == "invalid_path"


@pytest.mark.asyncio
async def test_empty_workdir_invalid_without_runner() -> None:
    result = await run_review(
        ReviewRequest(prompt="x", cd="", max_retries=0),
        runner=ScriptedLinesRunner(lines=[], exit_code=0),
    )
    assert result.success is False
    assert result.error_kind == "invalid_path"


@pytest.mark.asyncio
async def test_file_as_workdir_is_invalid(tmp_path: Path) -> None:
    f = tmp_path / "not-a-dir.txt"
    f.write_text("x", encoding="utf-8")
    result = await run_review(
        ReviewRequest(prompt="x", cd=f, max_retries=0),
        runner=ScriptedLinesRunner(lines=[], exit_code=0),
    )
    assert result.success is False
    assert result.error_kind == "invalid_path"


def test_path_chain_has_reparse_walks_up_to_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reparse 在祖先上时子路径也不可信；且不得因 exists()=False 漏检。

    只在祖先上打标，叶子与中间层都不是 reparse —— 断言真的走了整条链，
    而不是仅仅证明「A 调用了 B」。
    """
    # 被测单元是 WinApiSecurity 自身：链式走查由它的两个内部原语组合而成，
    # 所以在 winsec 模块内替换叶子谓词是「测 adapter 的组合」，不是穿模块。
    from codex_mcp_cyber import winsec

    ancestor = tmp_path / "reparse-ancestor"
    # 叶子与中间层都不存在：走链必须靠 lstat 而非 exists()
    leaf = ancestor / "mid" / "leaf"
    monkeypatch.setattr(winsec, "_is_reparse_point", lambda p: p == ancestor)

    assert winsec._path_chain_has_reparse(leaf) is True
    # 负例：同级另一棵树上无 reparse，必须为 False（否则上面的 True 无意义）
    assert winsec._path_chain_has_reparse(tmp_path / "clean" / "leaf") is False


def test_missing_path_is_not_reparse(tmp_path: Path) -> None:
    """缺失路径不是 reparse point —— FileNotFoundError 分支必须返回 False。"""
    from codex_mcp_cyber.winsec import WinApiSecurity

    missing = tmp_path / "__definitely_missing_xyz__"
    assert WinApiSecurity().is_reparse_point(missing) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_junction_points_to_and_recreate_wrong_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一 link 路径若指向错误目标，应替换为正确目标。"""
    from codex_mcp_cyber import winlink

    cache = tmp_path / "ascii-cache"
    # 固定 digest，强制两个不同目标落在同一 link 路径上
    monkeypatch.setattr(
        winlink.hashlib,
        "sha1",
        lambda data: type(
            "H",
            (),
            {"hexdigest": lambda self: "fixeddigest000001"},
        )(),
    )

    a = tmp_path / "审查A"
    b = tmp_path / "审查B"
    a.mkdir()
    b.mkdir()
    (a / "a.txt").write_text("a", encoding="utf-8")
    (b / "b.txt").write_text("b", encoding="utf-8")

    pa = prefer_codex_workdir(a, cache_base=cache)
    assert (pa / "a.txt").read_text(encoding="utf-8") == "a"
    # 同一 digest → 同一 link 路径，应重绑到 b
    pb = prefer_codex_workdir(b, cache_base=cache)
    assert pa == pb
    assert (pb / "b.txt").read_text(encoding="utf-8") == "b"
    assert not (pb / "a.txt").exists()
    assert winlink._junction_points_to(pb, b)


@pytest.mark.skipif(os.name != "nt", reason="drive-relative is Windows semantics")
def test_normalize_rejects_drive_relative() -> None:
    for raw in ("C:", "C:.", "C:foo", "c:bar"):
        with pytest.raises(InvalidWorkdirError):
            normalize_workdir(raw)


def test_is_drive_relative() -> None:
    from codex_mcp_cyber.paths import _is_drive_relative

    assert _is_drive_relative("C:")
    assert _is_drive_relative("C:.")
    assert _is_drive_relative("C:foo")
    assert not _is_drive_relative(r"C:\Users")
    assert not _is_drive_relative("C:/Users")


@pytest.mark.asyncio
async def test_popen_oserror_267_is_invalid_path(tmp_path: Path) -> None:
    class _Boom(PopenCodexRunner):
        def run(self, cmd, *, prompt, workdir=None, timeout, max_duration):  # noqa: ANN001
            err = OSError(267, "The directory name is invalid")
            err.winerror = 267  # type: ignore[attr-defined]
            raise err

    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=0),
        runner=_Boom(),
    )
    assert result.success is False
    assert result.error_kind == "invalid_path"


def _icacls_exe() -> str | None:
    """测试自用：定位 System32\\icacls.exe。

    不依赖生产符号 —— 生产侧 ACL 走 WinAPI，不再 shell out 到 icacls。
    """
    windir = os.environ.get("SystemRoot") or r"C:\Windows"
    exe = os.path.normpath(os.path.join(windir, "System32", "icacls.exe"))
    return exe if os.path.isfile(exe) else None


def _icacls_dump(path: Path) -> str:
    import subprocess

    exe = _icacls_exe()
    assert exe is not None
    r = subprocess.run(
        [exe, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        shell=False,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    return (r.stdout or "") + (r.stderr or "")


def _assert_private_acl_shape(out: str) -> None:
    """icacls 输出必须符合私有目录形状：SYSTEM/Admins 在，Everyone 无完全控制。"""
    assert "NT AUTHORITY\\SYSTEM" in out or "SYSTEM" in out
    assert "BUILTIN\\Administrators" in out or "Administrators" in out

    # 任何以 Everyone / S-1-1-0（world SID）为主体且带 (F) 完全控制的 ACE 都不允许，
    # 无论继承标记（I）(OI)(CI) 如何组合。典型形态：everyone:(i)(oi)(ci)(f)
    for line in out.splitlines():
        compact = line.lower().replace(" ", "")
        if "everyone" not in compact and "s-1-1-0" not in compact:
            continue
        if "(f)" in compact:
            raise AssertionError(f"unexpected world full ACE: {line!r}")


def _grant_everyone_full(path: Path) -> None:
    import subprocess

    exe = _icacls_exe()
    assert exe is not None
    r = subprocess.run(
        [exe, str(path), "/grant", "Everyone:(OI)(CI)F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        shell=False,
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    # 确认父目录确实带上了 Everyone 宽权限，否则后续“无继承”断言无意义
    dump = _icacls_dump(path).lower()
    assert "everyone" in dump


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL APIs")
def test_restrict_private_acl_protected_dacl_and_owner(tmp_path: Path) -> None:
    """收敛后：owner=me；父目录宽松 ACE 不得继承到子目录。"""
    from codex_mcp_cyber.winsec import WinApiSecurity

    sec = WinApiSecurity()
    parent = tmp_path / "loose-parent"
    parent.mkdir()
    # 父目录预置 Everyone:F，用于证明子目录收敛后无继承宽权限
    _grant_everyone_full(parent)
    d = parent / "priv"
    d.mkdir()
    assert sec.restrict_private_dir_acl(d) is True
    assert sec.path_owner_sid(d) == sec.current_user_sid()
    out = _icacls_dump(d)
    _assert_private_acl_shape(out)


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_junction_reuse_fails_closed_when_sid_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已有 link 且 SID 不可用时必须拒绝复用（不得 target-only）。"""
    from codex_mcp_cyber.winsec import WinApiSecurity

    cache = tmp_path / "ascii-cache"
    chinese = tmp_path / "审查项目"
    chinese.mkdir()
    (chinese / "m.txt").write_text("ok", encoding="utf-8")

    alias = prefer_codex_workdir(chinese, sec=WinApiSecurity(), cache_base=cache)
    assert path_has_non_ascii(alias) is False

    # SID 取不到 → 不得复用已存在的别名，必须回退真实路径
    blind = FakeWinSecurity(sid=None)
    fallback = prefer_codex_workdir(chinese, sec=blind, cache_base=cache)
    # 精确：回到原中文目录，且不是 alias
    assert fallback != alias
    assert fallback.resolve() == chinese.resolve()


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_create_private_dir_atomic_acl_not_everyone_full(tmp_path: Path) -> None:
    from codex_mcp_cyber.winsec import WinApiSecurity

    sec = WinApiSecurity()
    # 父目录先给 Everyone:F，证明原子创建不会继承宽权限
    parent = tmp_path / "loose"
    parent.mkdir()
    _grant_everyone_full(parent)
    d = parent / "atom2"
    sec.create_private_dir_atomic(d)
    assert sec.path_owner_sid(d) == sec.current_user_sid()
    out = _icacls_dump(d)
    _assert_private_acl_shape(out)
    assert "SYSTEM" in out or "NT AUTHORITY\\SYSTEM" in out

def test_normalize_tilde_without_home_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(InvalidWorkdirError):
        normalize_workdir("~")


@pytest.mark.skipif(os.name != "nt", reason="icacls is Windows-only")
def test_restrict_acl_rejects_foreign_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    from codex_mcp_cyber import winsec

    # 被测单元是 adapter 自身的 owner 闸门：owner != me 时必须 fail-closed
    monkeypatch.setattr(winsec, "_current_user_sid", lambda: "S-1-5-21-me")
    monkeypatch.setattr(winsec, "_path_owner_sid", lambda p: "S-1-5-21-attacker")
    monkeypatch.setattr(winsec.os.path, "lexists", lambda p: True)
    assert winsec._restrict_private_dir_acl(Path("C:/preowned")) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_junction_cache_root_rejects_reparse_before_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """wd-junctions 已是 reparse 时，不得对其做 private ACL。

    顺序很重要：先验 reparse 再动 ACL。反过来就等于对一个可能指向别处的
    reparse point 改权限。
    """
    from codex_mcp_cyber.winlink import _junction_cache_root

    user_root = tmp_path / "codex-mcp-cyber-user"
    user_root.mkdir()
    root = user_root / "wd-junctions"
    root.mkdir()

    sec = FakeWinSecurity(
        sid="S-1-5-21-test-sid-for-reparse-order",
        reparse={str(root)},
    )

    with pytest.raises(OSError):
        _junction_cache_root(sec=sec, cache_base=user_root)

    assert str(root) not in sec.acl_calls


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_junction_cache_root_shape_no_temp_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生产候选只能是系统盘根下的 codex-mcp-cyber-v3-<sidhash>。

    TEMP / TMP 是用户可写的，绝不能成为回退候选 —— 那等于把缓存根交给
    攻击者可控的路径。
    """
    from codex_mcp_cyber.winlink import _junction_cache_root
    import hashlib

    sid = "S-1-5-21-shape-test"
    uh = hashlib.sha1(sid.encode()).hexdigest()[:12]

    # 让候选被记下后立即创建失败，从而把生产真正选中的缓存根暴露出来
    sec = FakeWinSecurity(sid=sid, windir=r"C:\Windows", fail_create=True)
    monkeypatch.setenv("TEMP", r"\\evil\share\tmp")
    monkeypatch.setenv("TMP", r"C:\Users\Public\tmp")

    with pytest.raises(OSError):
        _junction_cache_root(sec=sec, cache_base=None)

    attempts = [c.lower().replace("/", "\\") for c in sec.create_attempts]
    assert attempts, "未尝试创建任何候选缓存根"

    expected = rf"c:\codex-mcp-cyber-v3-{uh}"
    # 每一个被尝试创建的路径都必须落在系统盘根下的 codex-mcp-cyber-v3-<hash> 里
    for path in attempts:
        assert path.startswith(expected), f"候选落在预期缓存根之外：{path!r}"

    # TEMP / TMP 用户可写，绝不能成为回退候选
    joined = " ".join(attempts + [c.lower() for c in sec.chain_calls])
    assert "evil" not in joined
    assert "public" not in joined


def test_looks_like_unc_mixed_separators() -> None:
    from codex_mcp_cyber.paths import _looks_like_unc

    assert _looks_like_unc(r"\\server\share")
    assert _looks_like_unc("//server/share")
    assert _looks_like_unc(r"/\server\share")
    assert not _looks_like_unc(r"C:\Users\x")


@pytest.mark.asyncio
async def test_popen_oserror_becomes_structured_failure(tmp_path: Path) -> None:
    class _Boom(PopenCodexRunner):
        def run(self, cmd, *, prompt, workdir=None, timeout, max_duration):  # noqa: ANN001
            raise OSError(123, "模拟 WinError 123")

    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=0),
        runner=_Boom(),
    )
    assert result.success is False
    assert result.error_kind == "invalid_path"
