"""工作目录归一化与 argv / runner seam 的路径行为。"""

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
from codex_mcp_cyber.process import PopenCodexRunner
from codex_mcp_cyber.review import ReviewRequest, build_codex_argv, run_review
from codex_mcp_cyber.stream import ProcessOutcome

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


def test_normalize_path_object_passthrough(tmp_path: Path) -> None:
    # abspath 形式，不强制 resolve 相等
    got = normalize_workdir(tmp_path)
    assert got.is_absolute() or os.path.isabs(str(got))
    assert got.name == tmp_path.name or tmp_path.name in str(got)


def test_normalize_non_ascii_workdir_passes_through(tmp_path: Path) -> None:
    """中文 / 非 ASCII 工作目录是一等公民：裸路径直接归一通过，不改写、不别名。

    历史：0.4.x 曾为非 ASCII 路径建 ASCII 目录联接（os error 123 防御）；
    实测元凶是字面引号（normalize 已剥），当前 Codex CLI 在非 ASCII
    工作目录下内部工具工作正常，0.5.0 起该机制移除。
    """
    chinese = tmp_path / "审查项目"
    chinese.mkdir()
    got = normalize_workdir(str(chinese))
    assert got == Path(os.path.abspath(str(chinese)))
    assert path_has_non_ascii(got) is True
    cli = format_cli_path(got)
    assert "审查项目" in cli
    assert '"' not in cli


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


@pytest.mark.skipif(os.name != "nt", reason="Windows path formatting")
def test_build_codex_argv_uses_formatted_path_without_quotes(tmp_path: Path) -> None:
    req = ReviewRequest(prompt="x", cd=tmp_path)
    cmd = build_codex_argv(req, tmp_path)
    assert "--cd" in cmd
    cd_arg = cmd[cmd.index("--cd") + 1]
    assert '"' not in cd_arg
    assert "'" not in cd_arg


def test_build_codex_argv_image_relative_to_workdir(tmp_path: Path) -> None:
    req = ReviewRequest(prompt="x", cd=tmp_path, image=[Path("shot.png")])
    cmd = build_codex_argv(req, tmp_path)
    assert "--image" in cmd
    img = cmd[cmd.index("--image") + 1]
    assert img == os.path.normpath(str(tmp_path / "shot.png"))


# ── 复审（re-review）argv 形状 ────────────────────────────────────────────
# CONTEXT.md 把复审定为一等领域词，但此前整条路径零测试。
# codex CLI 契约：`codex exec [OPTIONS] <COMMAND> [ARGS]`，
# 即 resume 子命令必须排在所有 flag **之后**。


def test_build_codex_argv_initial_review_has_no_resume(tmp_path: Path) -> None:
    """初审：session_id 为空 → argv 不得出现 resume。"""
    req = ReviewRequest(prompt="x", cd=tmp_path, session_id="")
    cmd = build_codex_argv(req, tmp_path)
    assert "resume" not in cmd


def test_build_codex_argv_re_review_appends_resume_after_all_flags(tmp_path: Path) -> None:
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
    cmd = build_codex_argv(req, tmp_path)

    assert cmd[:2] == ["codex", "exec"]
    # resume 与 id 相邻且收尾
    assert cmd[-2:] == ["resume", sid]
    # 每个 flag 都在 resume 之前 —— CLI 要求 OPTIONS 先于 COMMAND
    resume_at = cmd.index("resume")
    for i, token in enumerate(cmd):
        if token.startswith("--"):
            assert i < resume_at, f"flag {token!r} 排在 resume 之后，CLI 会拒绝"


def test_build_codex_argv_re_review_keeps_sandbox_and_cd(tmp_path: Path) -> None:
    """复审不得丢掉沙箱策略与工作目录 —— 只读边界在复审同样生效。"""
    req = ReviewRequest(prompt="x", cd=tmp_path, session_id="abc-123")
    cmd = build_codex_argv(req, tmp_path)
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
async def test_non_ascii_workdir_reaches_seam_unaliased(tmp_path: Path) -> None:
    """中文工作目录必须**原样**穿过 runner seam —— 与归一结果精确相等。

    宽断言（basename / 包含判断）会放过「换一个同名别名」类改写；
    这里要求 seam 收到的 workdir、argv --cd、领域结局三处与
    normalize_workdir 的输出逐一全等。
    """
    chinese = tmp_path / "审查项目"
    chinese.mkdir()
    expected = normalize_workdir(str(chinese))
    runner = ScriptedLinesRunner(lines=_ok_jsonl_lines(), exit_code=0)
    result = await run_review(
        ReviewRequest(prompt="hi", cd=str(chinese), max_retries=0),
        runner=runner,
    )
    assert result.success is True
    assert runner.seen_workdirs[0] == expected
    assert result.workdir == expected
    cd_arg = runner.seen_cmds[0][runner.seen_cmds[0].index("--cd") + 1]
    assert cd_arg == format_cli_path(expected)
    assert path_has_non_ascii(cd_arg) is True
    assert '"' not in cd_arg


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


def test_normalize_tilde_without_home_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(InvalidWorkdirError):
        normalize_workdir("~")


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
