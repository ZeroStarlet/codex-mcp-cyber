"""工作目录归一化与 Windows 路径加固。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codex_mcp_cyber.errors import (
    InvalidWorkdirError,
    format_cli_path,
    looks_like_invalid_path_error,
    normalize_workdir,
    path_has_non_ascii,
    prefer_codex_workdir,
)
from codex_mcp_cyber.process import PopenCodexRunner, ProcessOutcome
from codex_mcp_cyber.review import ReviewRequest, _build_cmd, run_review

from runners import ScriptedLinesRunner


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
    from codex_mcp_cyber import errors as err

    cache = tmp_path / "ascii-cache"
    cache.mkdir()
    monkeypatch.setattr(err, "_junction_cache_root", lambda: cache)
    chinese = tmp_path / "审查项目"
    chinese.mkdir()
    (chinese / "m.txt").write_text("ok", encoding="utf-8")
    alias = err.prefer_codex_workdir(chinese)
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
    cache.mkdir()
    monkeypatch.setattr(
        "codex_mcp_cyber.errors._junction_cache_root",
        lambda: cache,
    )

    chinese = tmp_path / "审查项目"
    chinese.mkdir()
    marker = chinese / "marker.txt"
    marker.write_text("ok", encoding="utf-8")

    preferred = prefer_codex_workdir(chinese)
    assert preferred.exists()
    assert path_has_non_ascii(preferred) is False
    cli = format_cli_path(preferred)
    assert path_has_non_ascii(cli) is False
    assert str(cache) in cli or cache.name in cli.replace("\\", "/")
    assert (preferred / "marker.txt").read_text(encoding="utf-8") == "ok"
    again = prefer_codex_workdir(chinese)
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
    from codex_mcp_cyber import errors as err

    ancestor = tmp_path / "reparse-ancestor"
    # 叶子与中间层都不存在：走链必须靠 lstat 而非 exists()
    leaf = ancestor / "mid" / "leaf"
    monkeypatch.setattr(err, "_is_reparse_point", lambda p: p == ancestor)

    assert err._path_chain_has_reparse(leaf) is True
    # 负例：同级另一棵树上无 reparse，必须为 False（否则上面的 True 无意义）
    assert err._path_chain_has_reparse(tmp_path / "clean" / "leaf") is False


def test_missing_path_is_not_reparse() -> None:
    from codex_mcp_cyber import errors as err
    from pathlib import Path as P

    missing = P("C:/codex-mcp-cyber-wd/__definitely_missing_xyz__")
    assert err._is_reparse_point(missing) is False
    # 父链含盘符根，缺失叶子不该因 FileNotFound 整链判死
    # 只测叶子本身


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_junction_points_to_and_recreate_wrong_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一 link 路径若指向错误目标，应替换为正确目标。"""
    from codex_mcp_cyber import errors as err

    cache = tmp_path / "ascii-cache"
    cache.mkdir()
    monkeypatch.setattr(err, "_junction_cache_root", lambda: cache)
    # 固定 digest，强制同一 link 路径
    monkeypatch.setattr(
        err.hashlib,
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

    pa = err.prefer_codex_workdir(a)
    assert (pa / "a.txt").read_text(encoding="utf-8") == "a"
    # 同一 digest → 同一 link 路径，应重绑到 b
    pb = err.prefer_codex_workdir(b)
    assert pa == pb
    assert (pb / "b.txt").read_text(encoding="utf-8") == "b"
    assert not (pb / "a.txt").exists()
    assert err._junction_points_to(pb, b)


@pytest.mark.skipif(os.name != "nt", reason="drive-relative is Windows semantics")
def test_normalize_rejects_drive_relative() -> None:
    for raw in ("C:", "C:.", "C:foo", "c:bar"):
        with pytest.raises(InvalidWorkdirError):
            normalize_workdir(raw)


def test_is_drive_relative() -> None:
    from codex_mcp_cyber.errors import _is_drive_relative

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
    # SYSTEM / Administrators present
    assert "NT AUTHORITY\\SYSTEM" in out or "SYSTEM" in out
    assert "BUILTIN\\Administrators" in out or "Administrators" in out
    # No Everyone / world SID full control in any inheritance form
    low = out.lower().replace(" ", "")
    # 匹配 Everyone 或 S-1-1-0 后接任意括号标记再以 (F) 结尾的 ACE
    import re

    everyone_full = re.compile(
        r"(?:everyone|s-1-1-0):\([^\n]*\(f\)\)|(?:everyone|s-1-1-0):\([^\n]*f\)",
        re.IGNORECASE,
    )
    # 更直接：行内含 Everyone/S-1-1-0 且含 (F)
    for line in out.splitlines():
        ll = line.lower()
        if "everyone" in ll or "s-1-1-0" in ll:
            # 去掉空格后看是否含 (f) 作为权限标志
            compact = ll.replace(" ", "")
            # 典型: everyone:(i)(oi)(ci)(f) 或 everyone:(f)
            if re.search(r"\([^)]*f\)", compact):
                # 若仅 RX 等不含单独 F 权限字母则放行；Full 控制标记为 (F)
                if "(f)" in compact or compact.endswith("f)"):
                    # 排除误伤：不含 (rx)/(r)/(w) 单独？Full 就是 (F)
                    # 只要 ACE 主体是 everyone/world 且含 (f) 即失败
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
    from codex_mcp_cyber import errors as err
    import subprocess

    parent = tmp_path / "loose-parent"
    parent.mkdir()
    # 父目录预置 Everyone:F，用于证明子目录收敛后无继承宽权限
    _grant_everyone_full(parent)
    d = parent / "priv"
    d.mkdir()
    assert err._restrict_private_dir_acl(d) is True
    sid = err._current_user_sid()
    assert err._path_owner_sid(d) == sid
    out = _icacls_dump(d)
    _assert_private_acl_shape(out)


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_junction_reuse_fails_closed_when_sid_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已有 link 且 SID 不可用时必须拒绝复用（不得 target-only）。"""
    from codex_mcp_cyber import errors as err

    cache = tmp_path / "ascii-cache"
    cache.mkdir()
    chinese = tmp_path / "审查项目"
    chinese.mkdir()
    (chinese / "m.txt").write_text("ok", encoding="utf-8")
    monkeypatch.setattr(err, "_junction_cache_root", lambda: cache)
    alias = err.prefer_codex_workdir(chinese)
    assert path_has_non_ascii(alias) is False
    monkeypatch.setattr(err, "_current_user_sid", lambda: None)
    fallback = err.prefer_codex_workdir(chinese)
    # 精确：回到原中文目录，且不是 alias
    assert fallback != alias
    assert fallback.resolve() == chinese.resolve()


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_create_private_dir_atomic_acl_not_everyone_full(tmp_path: Path) -> None:
    from codex_mcp_cyber import errors as err

    # 父目录先给 Everyone:F，证明原子创建不会继承宽权限
    parent = tmp_path / "loose"
    parent.mkdir()
    _grant_everyone_full(parent)
    d = parent / "atom2"
    err._create_private_dir_atomic(d)
    assert err._path_owner_sid(d) == err._current_user_sid()
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
    from codex_mcp_cyber import errors as err

    monkeypatch.setattr(err, "_current_user_sid", lambda: "S-1-5-21-me")
    monkeypatch.setattr(err, "_path_owner_sid", lambda p: "S-1-5-21-attacker")
    monkeypatch.setattr(err.os.path, "lexists", lambda p: True)
    assert err._restrict_private_dir_acl(Path("C:/preowned")) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_junction_cache_root_rejects_reparse_before_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生产 _junction_cache_root：wd-junctions 已是 reparse 时，不得对其做 private ACL。"""
    from codex_mcp_cyber import errors as err

    sid = "S-1-5-21-test-sid-for-reparse-order"
    user_root = tmp_path / "codex-mcp-cyber-user"
    user_root.mkdir()
    root = user_root / "wd-junctions"
    root.mkdir()

    acl_calls: list[str] = []

    def track_acl(p: Path) -> bool:
        acl_calls.append(os.path.normcase(str(Path(p).resolve())))
        return True

    monkeypatch.setattr(err, "_current_user_sid", lambda: sid)
    monkeypatch.setattr(err, "_JUNCTION_BASE_OVERRIDE", user_root)
    monkeypatch.setattr(err, "_restrict_private_dir_acl", track_acl)
    monkeypatch.setattr(
        err,
        "_is_reparse_point",
        lambda p: os.path.normcase(str(Path(p).resolve()))
        == os.path.normcase(str(root.resolve())),
    )
    monkeypatch.delenv("TEMP", raising=False)
    monkeypatch.delenv("TMP", raising=False)

    with pytest.raises(OSError):
        err._junction_cache_root()

    root_key = os.path.normcase(str(root.resolve()))
    assert root_key not in acl_calls


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_junction_cache_root_shape_no_temp_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生产候选只能是系统盘根下的 codex-mcp-cyber-<sidhash>。"""
    from codex_mcp_cyber import errors as err
    import hashlib

    sid = "S-1-5-21-shape-test"
    uh = hashlib.sha1(sid.encode()).hexdigest()[:12]
    seen: list[Path] = []

    real_exists = err.os.path.lexists

    def fake_lexists(p):  # noqa: ANN001
        seen.append(Path(p))
        return False  # force create path then fail early on parent reparse or mkdir

    monkeypatch.setattr(err, "_current_user_sid", lambda: sid)
    monkeypatch.setattr(err, "_windows_directory", lambda: r"C:\Windows")
    monkeypatch.setattr(err, "_JUNCTION_BASE_OVERRIDE", None)
    monkeypatch.setattr(err.os.path, "lexists", fake_lexists)
    monkeypatch.setattr(err, "_path_chain_has_reparse", lambda p: True)  # fail all
    monkeypatch.setenv("TEMP", r"\\evil\share\tmp")
    monkeypatch.setenv("TMP", r"C:\Users\Public\tmp")

    with pytest.raises(OSError):
        err._junction_cache_root()

    # 所有尝试的路径都必须是 C:\codex-mcp-cyber-<hash> 或其子路径，不得含 evil/Public
    joined = " ".join(str(p) for p in seen).lower()
    assert "evil" not in joined
    assert "public" not in joined
    assert any(
        str(p).lower().replace("/", "\\").startswith(rf"c:\codex-mcp-cyber-v3-{uh}")
        for p in seen
    )


def test_looks_like_unc_mixed_separators() -> None:
    from codex_mcp_cyber.errors import _looks_like_unc

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
