"""PopenCodexRunner 生产路径 lifecycle。"""

from __future__ import annotations

import json
import subprocess
import threading

import pytest

from codex_mcp_cyber.errors import CommandTimeoutError
from codex_mcp_cyber.process import PopenCodexRunner


class _FakeStdout:
    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self._i = 0
        self.closed = False

    def readline(self) -> str:
        if self._i >= len(self._lines):
            return ""
        value = self._lines[self._i]
        self._i += 1
        return value + "\n"

    def close(self) -> None:
        self.closed = True

class _FakeProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = _FakeStdout(lines)
        self.stdin = None
        self._alive = True

    def poll(self) -> int | None:
        return None if self._alive else 0

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        self._alive = False
        return 0

    def terminate(self) -> None:
        self._alive = False

    def kill(self) -> None:
        self._alive = False

def _skip_grace_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 early-stop grace 睡眠压到 0，其余 sleep 保持真实。

    阈值从 ``PopenCodexRunner.GRACEFUL_SHUTDOWN_DELAY`` 读取，不写字面值 ——
    否则改动该常数时测试只会静默变慢，而不会变红。
    """
    import codex_mcp_cyber.process as process_mod

    real_sleep = process_mod.time.sleep
    grace = PopenCodexRunner.GRACEFUL_SHUTDOWN_DELAY

    def _fast_sleep(sec: float) -> None:
        if sec == grace:
            return
        real_sleep(sec)

    monkeypatch.setattr(process_mod.time, "sleep", _fast_sleep)


def _run_popen_with_fake_lines(
    lines: list[str],
    *,
    is_terminal_line=None,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    from codex_mcp_cyber.process import PopenCodexRunner
    import codex_mcp_cyber.process as process_mod

    monkeypatch.setattr(process_mod.shutil, "which", lambda _name: "codex-fake")
    monkeypatch.setattr(
        process_mod.subprocess,
        "Popen",
        lambda *a, **k: _FakeProcess(lines),  # noqa: ARG005
    )
    _skip_grace_sleep(monkeypatch)
    runner = PopenCodexRunner(is_terminal_line=is_terminal_line)
    outcome = runner.run(
        ["codex", "exec"],
        prompt="hi",
        timeout=30,
        max_duration=60,
    )
    return outcome.lines

class _ExplodingStdin:
    """stdin.write 抛未捕获异常，模拟 spawn 初始化失败。"""

    def write(self, _data: str) -> int:
        raise RuntimeError("stdin boom")

    def close(self) -> None:
        return None

class _LifecycleFakeProcess:
    """可观测 terminate/kill 与存活状态的 FakeProcess。"""

    def __init__(self, lines: list[str], *, explode_stdin: bool = False) -> None:
        self.stdout = _FakeStdout(lines)
        self.stdin = _ExplodingStdin() if explode_stdin else None
        self._alive = True
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self) -> int | None:
        return None if self._alive else 0

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        self._alive = False
        return 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._alive = False

    def kill(self) -> None:
        self.kill_calls += 1
        self._alive = False

class _BlockingStdout:
    """readline 挂起直到 close —— 模拟「进程存活但无输出」的空闲状态。"""

    def __init__(self) -> None:
        self._release = threading.Event()
        self.closed = False

    def readline(self) -> str:
        self._release.wait()
        return ""

    def close(self) -> None:
        self.closed = True
        self._release.set()

class _SteppingClock:
    """按脚本推进的假时钟：依次返回给定时刻，耗尽后停在最后一刻。"""

    def __init__(self, *times: float) -> None:
        self._times = list(times)
        self._last = times[-1]

    def __call__(self) -> float:
        if self._times:
            self._last = self._times.pop(0)
        return self._last

class _StubbornWaitProcess(_LifecycleFakeProcess):
    """wait 前 N 次抛 TimeoutExpired —— 驱动 terminate→kill 升级链。"""

    def __init__(self, lines: list[str], *, raises: int) -> None:
        super().__init__(lines)
        self._raises = raises

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        if self._raises > 0:
            self._raises -= 1
            raise subprocess.TimeoutExpired(cmd="codex", timeout=timeout or 0)
        self._alive = False
        return 0

class _IgnoresTerminateProcess(_LifecycleFakeProcess):
    """terminate 不生效、首个 wait 抛超时 —— 驱动 _cleanup 的 kill 升级。"""

    def __init__(self, lines: list[str]) -> None:
        super().__init__(lines)
        self._wait_raises = 1

    def terminate(self) -> None:
        self.terminate_calls += 1  # 不置死：模拟顽固进程

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        if self._wait_raises > 0:
            self._wait_raises -= 1
            raise subprocess.TimeoutExpired(cmd="codex", timeout=timeout or 0)
        self._alive = False
        return 0

def test_popen_idle_timeout_via_injected_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0 缺口：空闲超时分支此前零覆盖（真实时钟从 run() 不可测）。"""
    import codex_mcp_cyber.process as process_mod

    fake = _LifecycleFakeProcess([])
    fake.stdout = _BlockingStdout()  # type: ignore[assignment]
    monkeypatch.setattr(process_mod.shutil, "which", lambda _n: "codex-fake")
    monkeypatch.setattr(process_mod.subprocess, "Popen", lambda *a, **k: fake)  # noqa: ARG005
    runner = PopenCodexRunner(clock=_SteppingClock(0.0, 0.0, 31.0))
    outcome = runner.run(["codex", "exec"], prompt="hi", timeout=30, max_duration=0)
    assert outcome.terminal == "idle_timeout"
    assert "空闲超时" in outcome.error_message
    assert outcome.exit_code is None
    assert fake.terminate_calls >= 1
    assert fake.stdout.closed is True

def test_popen_wall_clock_timeout_via_injected_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0 缺口：墙钟总时长超时分支此前零覆盖。"""
    import codex_mcp_cyber.process as process_mod

    fake = _LifecycleFakeProcess([])
    fake.stdout = _BlockingStdout()  # type: ignore[assignment]
    monkeypatch.setattr(process_mod.shutil, "which", lambda _n: "codex-fake")
    monkeypatch.setattr(process_mod.subprocess, "Popen", lambda *a, **k: fake)  # noqa: ARG005
    runner = PopenCodexRunner(clock=_SteppingClock(0.0, 0.0, 61.0))
    outcome = runner.run(["codex", "exec"], prompt="hi", timeout=1000, max_duration=60)
    assert outcome.terminal == "timeout"
    assert "总时长" in outcome.error_message
    assert outcome.exit_code is None
    assert fake.terminate_calls >= 1

def test_popen_wait_timeout_escalates_terminate_then_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0 缺口：process.wait 超时须走 terminate→kill 升级并返回 timeout 终局。"""
    import codex_mcp_cyber.process as process_mod

    fake = _StubbornWaitProcess([], raises=2)
    monkeypatch.setattr(process_mod.shutil, "which", lambda _n: "codex-fake")
    monkeypatch.setattr(process_mod.subprocess, "Popen", lambda *a, **k: fake)  # noqa: ARG005
    runner = PopenCodexRunner()
    outcome = runner.run(["codex", "exec"], prompt="hi", timeout=30, max_duration=60)
    assert outcome.terminal == "timeout"
    assert "等待超时" in outcome.error_message
    assert fake.terminate_calls == 1
    assert fake.kill_calls == 1
    assert outcome.exit_code is None

def test_popen_cleanup_kill_upgrade_when_terminate_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0 缺口：_cleanup 对无视 terminate 的顽固进程须升级 kill。"""
    import codex_mcp_cyber.process as process_mod

    fake = _IgnoresTerminateProcess([])
    fake.stdout = _BlockingStdout()  # type: ignore[assignment]
    monkeypatch.setattr(process_mod.shutil, "which", lambda _n: "codex-fake")
    monkeypatch.setattr(process_mod.subprocess, "Popen", lambda *a, **k: fake)  # noqa: ARG005
    runner = PopenCodexRunner(clock=_SteppingClock(0.0, 0.0, 31.0))
    outcome = runner.run(["codex", "exec"], prompt="hi", timeout=30, max_duration=0)
    assert outcome.terminal == "idle_timeout"
    assert fake.terminate_calls >= 1
    assert fake.kill_calls == 1

def test_popen_spawn_uses_workdir_verbatim_as_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """seam 契约：workdir 是调用方交付的成品字符串，生产 adapter 原样用作
    Popen cwd，不得再加工（此前 process 私自二次 format_cli_path，
    测试 adapter 不复刻 —— seam 两侧约定不一致）。"""
    import codex_mcp_cyber.process as process_mod

    captured: dict = {}

    def fake_popen(*a, **k):  # noqa: ANN002, ANN003
        captured["cwd"] = k.get("cwd")
        return _FakeProcess([])

    monkeypatch.setattr(process_mod.shutil, "which", lambda _name: "codex-fake")
    monkeypatch.setattr(process_mod.subprocess, "Popen", fake_popen)
    _skip_grace_sleep(monkeypatch)
    runner = PopenCodexRunner()
    given = "C:/给定/成品路径"
    runner.run(
        ["codex", "exec"], prompt="hi", workdir=given, timeout=30, max_duration=60
    )
    assert captured["cwd"] == given

def test_popen_default_stops_after_turn_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codex_mcp_cyber.stream import is_turn_completed_line

    lines = [
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "turn.completed"}),
        json.dumps({"type": "after-should-not-read"}),
    ]
    got = _run_popen_with_fake_lines(lines, monkeypatch=monkeypatch)
    assert json.dumps({"type": "turn.completed"}) in got
    assert all("after-should-not-read" not in x for x in got)
    # 默认路径确实用 stream 谓词语义
    assert is_turn_completed_line(json.dumps({"type": "turn.completed"}))

def test_popen_custom_predicate_is_invoked_and_can_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def never_stop(line: str) -> bool:
        seen.append(line)
        return False

    lines = ["a", "b", "c"]
    got = _run_popen_with_fake_lines(
        lines, is_terminal_line=never_stop, monkeypatch=monkeypatch
    )
    assert got == ["a", "b", "c"]
    assert seen == ["a", "b", "c"]

def test_popen_predicate_exception_is_not_silent_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(line: str) -> bool:
        if line == "plain-log":
            raise ValueError("bad predicate")
        return False

    with pytest.raises(ValueError, match="bad predicate"):
        _run_popen_with_fake_lines(
            ["plain-log", "after"],
            is_terminal_line=boom,
            monkeypatch=monkeypatch,
        )

def test_popen_runner_accepts_custom_terminal_predicate() -> None:
    from codex_mcp_cyber.process import PopenCodexRunner

    def always_false(_line: str) -> bool:
        return False

    runner = PopenCodexRunner(is_terminal_line=always_false)
    assert runner.is_terminal_line is always_false

def test_popen_spawn_stdin_error_cleans_up_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1 回归：_spawn 在 Popen 成功后失败时必须 terminate 子进程。"""
    from codex_mcp_cyber.process import PopenCodexRunner
    import codex_mcp_cyber.process as process_mod

    fake = _LifecycleFakeProcess([], explode_stdin=True)
    monkeypatch.setattr(process_mod.shutil, "which", lambda _name: "codex-fake")
    monkeypatch.setattr(
        process_mod.subprocess,
        "Popen",
        lambda *a, **k: fake,  # noqa: ARG005
    )
    runner = PopenCodexRunner()
    with pytest.raises(RuntimeError, match="stdin boom"):
        runner.run(["codex", "exec"], prompt="hi", timeout=30, max_duration=60)
    assert fake.terminate_calls >= 1
    assert fake.poll() is not None  # 已结束

def test_popen_predicate_timeout_keeps_partial_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2 回归：谓词抛 CommandTimeoutError 时保留此前已读行。"""
    from codex_mcp_cyber.process import PopenCodexRunner
    import codex_mcp_cyber.process as process_mod

    monkeypatch.setattr(process_mod.shutil, "which", lambda _name: "codex-fake")
    monkeypatch.setattr(
        process_mod.subprocess,
        "Popen",
        lambda *a, **k: _FakeProcess(["before", "boom"]),  # noqa: ARG005
    )
    _skip_grace_sleep(monkeypatch)

    def boom_timeout(line: str) -> bool:
        if line == "boom":
            raise CommandTimeoutError("pred timeout", is_idle=True)
        return False

    runner = PopenCodexRunner(is_terminal_line=boom_timeout)
    outcome = runner.run(["codex", "exec"], prompt="hi", timeout=30, max_duration=60)
    assert outcome.terminal == "idle_timeout"
    assert outcome.lines == ["before", "boom"]
    assert outcome.raw_output_lines == 2

def test_popen_predicate_error_still_joins_via_outer_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2 回归：谓词异常路径 run.finally 仍持有 thread 并 terminate 进程。"""
    from codex_mcp_cyber.process import PopenCodexRunner
    import codex_mcp_cyber.process as process_mod
    import threading

    fake = _LifecycleFakeProcess(["plain-log"])
    join_timeouts: list[float | None] = []
    real_thread = threading.Thread

    class _RecordingThread(real_thread):  # type: ignore[misc, valid-type]
        """清理时仍报告 alive，强制走 join 路径（避免 reader 先退出导致漏断言）。"""

        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            super().__init__(*args, **kwargs)
            self._force_alive = True

        def is_alive(self) -> bool:
            if self._force_alive:
                return True
            return super().is_alive()

        def join(self, timeout: float | None = None) -> None:  # noqa: A003
            join_timeouts.append(timeout)
            self._force_alive = False
            return super().join(timeout=timeout)

    monkeypatch.setattr(process_mod.shutil, "which", lambda _name: "codex-fake")
    monkeypatch.setattr(
        process_mod.subprocess,
        "Popen",
        lambda *a, **k: fake,  # noqa: ARG005
    )
    monkeypatch.setattr(process_mod.threading, "Thread", _RecordingThread)
    _skip_grace_sleep(monkeypatch)

    def boom(line: str) -> bool:
        if line == "plain-log":
            raise ValueError("bad predicate")
        return False

    runner = PopenCodexRunner(is_terminal_line=boom)
    with pytest.raises(ValueError, match="bad predicate"):
        runner.run(["codex", "exec"], prompt="hi", timeout=30, max_duration=60)
    # drain 异常后外层 cleanup 必须杀进程（thread_box 持有 reader）
    assert fake.poll() is not None
    assert fake.terminate_calls >= 1
    # 真正断言 join 被调用（timeout=5 为 _cleanup 约定）
    assert 5 in join_timeouts

def test_popen_spawn_base_exception_cleans_up_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1 回归：stdin 阶段 KeyboardInterrupt 也必须 terminate 子进程。"""
    from codex_mcp_cyber.process import PopenCodexRunner
    import codex_mcp_cyber.process as process_mod

    class _KiStdin:
        def write(self, _data: str) -> int:
            raise KeyboardInterrupt()

        def close(self) -> None:
            return None

    fake = _LifecycleFakeProcess([])
    fake.stdin = _KiStdin()  # type: ignore[assignment]
    monkeypatch.setattr(process_mod.shutil, "which", lambda _name: "codex-fake")
    monkeypatch.setattr(
        process_mod.subprocess,
        "Popen",
        lambda *a, **k: fake,  # noqa: ARG005
    )
    runner = PopenCodexRunner()
    with pytest.raises(KeyboardInterrupt):
        runner.run(["codex", "exec"], prompt="hi", timeout=30, max_duration=60)
    assert fake.terminate_calls >= 1
    assert fake.poll() is not None

