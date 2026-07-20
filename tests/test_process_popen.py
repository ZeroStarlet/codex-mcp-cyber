"""PopenCodexRunner 生产路径 lifecycle。"""

from __future__ import annotations

import json

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
    from codex_mcp_cyber.errors import CommandTimeoutError
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

