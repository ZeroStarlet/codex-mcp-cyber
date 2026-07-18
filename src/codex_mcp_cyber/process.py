"""行流 seam：Codex 进程执行 adapter。"""

from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from codex_mcp_cyber.errors import CommandNotFoundError, CommandTimeoutError


@dataclass(frozen=True)
class ProcessOutcome:
    """一次进程执行的行流结果。"""

    lines: list[str]
    exit_code: Optional[int]
    raw_output_lines: int


class CodexProcessRunner(Protocol):
    """行流 seam：执行命令并产出 stdout 行 + exit code。"""

    def run(
        self,
        cmd: list[str],
        *,
        prompt: str,
        timeout: int,
        max_duration: int,
    ) -> ProcessOutcome: ...


@dataclass
class ScriptedLinesRunner:
    """测试 adapter：回放固定行序列，不碰 OS。"""

    lines: list[str]
    exit_code: Optional[int] = 0
    calls: int = 0

    def run(
        self,
        cmd: list[str],
        *,
        prompt: str,
        timeout: int,
        max_duration: int,
    ) -> ProcessOutcome:
        del cmd, prompt, timeout, max_duration
        self.calls += 1
        lines = list(self.lines)
        raw = sum(1 for line in lines if line)
        return ProcessOutcome(lines=lines, exit_code=self.exit_code, raw_output_lines=raw)


@dataclass
class SequenceRunner:
    """测试 adapter：按调用次序返回不同 outcome 或抛超时。"""

    steps: list[ProcessOutcome | CommandTimeoutError]
    calls: int = 0

    def run(
        self,
        cmd: list[str],
        *,
        prompt: str,
        timeout: int,
        max_duration: int,
    ) -> ProcessOutcome:
        del cmd, prompt, timeout, max_duration
        if self.calls >= len(self.steps):
            raise RuntimeError("SequenceRunner exhausted")
        step = self.steps[self.calls]
        self.calls += 1
        if isinstance(step, CommandTimeoutError):
            raise step
        return step


class PopenCodexRunner:
    """生产 adapter：真实 subprocess + 超时 + early-stop。

    对外 interface 仅 ``run(...) -> ProcessOutcome``（及 ``__init__`` 的谓词注入）。
    线程 / queue / cleanup 是 implementation，不进 Protocol。
    """

    GRACEFUL_SHUTDOWN_DELAY = 0.3

    def __init__(
        self,
        is_terminal_line: Callable[[str], bool] | None = None,
    ) -> None:
        self.is_terminal_line = is_terminal_line

    def run(
        self,
        cmd: list[str],
        *,
        prompt: str,
        timeout: int,
        max_duration: int,
    ) -> ProcessOutcome:
        terminal = self._resolve_terminal_predicate()
        popen_cmd = self._resolve_codex_path(cmd)
        process: subprocess.Popen[str] | None = None
        # thread 经 box 回传，确保 _drain 异常路径 finally 仍能 join
        thread_box: list[threading.Thread | None] = [None]
        try:
            process = self._spawn(popen_cmd, prompt=prompt)
            lines, exit_code, raw_output_lines = self._drain(
                process,
                timeout=timeout,
                max_duration=max_duration,
                is_terminal_line=terminal,
                thread_box=thread_box,
            )
            return ProcessOutcome(
                lines=lines,
                exit_code=exit_code,
                raw_output_lines=raw_output_lines,
            )
        except CommandTimeoutError as e:
            # partial 由 _drain 挂在异常上（含谓词抛 CommandTimeoutError）
            partial = list(e.partial_lines)
            raise CommandTimeoutError(
                str(e),
                is_idle=e.is_idle,
                partial_lines=partial,
                raw_output_lines=len(partial),
            ) from e
        finally:
            if process is not None:
                self._cleanup(process, thread_box[0])

    def _resolve_terminal_predicate(self) -> Callable[[str], bool]:
        if self.is_terminal_line is not None:
            return self.is_terminal_line
        from codex_mcp_cyber.stream import is_turn_completed_line

        return is_turn_completed_line

    def _resolve_codex_path(self, cmd: list[str]) -> list[str]:
        codex_path = shutil.which("codex")
        if not codex_path:
            raise CommandNotFoundError(
                "未找到 codex CLI。请确保已安装 Codex CLI 并添加到 PATH。\n"
                "安装指南：https://developers.openai.com/codex/quickstart"
            )
        popen_cmd = cmd.copy()
        popen_cmd[0] = codex_path
        return popen_cmd

    def _spawn(self, popen_cmd: list[str], *, prompt: str) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            popen_cmd,
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            if process.stdin:
                try:
                    if prompt:
                        process.stdin.write(prompt)
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    try:
                        process.stdin.close()
                    except (BrokenPipeError, OSError):
                        pass
            return process
        except BaseException:
            # 含 KeyboardInterrupt / SystemExit：旧 finally 也会杀进程，不得泄漏
            self._cleanup(process, thread=None)
            raise

    def _drain(
        self,
        process: subprocess.Popen[str],
        *,
        timeout: int,
        max_duration: int,
        is_terminal_line: Callable[[str], bool],
        thread_box: list[threading.Thread | None],
    ) -> tuple[list[str], Optional[int], int]:
        """读 stdout 行至终态；返回 (lines, exit_code, raw_output_lines)。

        超时 / 谓词异常：把 partial 挂到异常上后抛出；
        reader thread 写入 thread_box，由 run.finally 统一 cleanup/join。
        """
        output_queue: queue.Queue[str | BaseException | None] = queue.Queue()
        raw_output_lines_holder = [0]
        grace = self.GRACEFUL_SHUTDOWN_DELAY

        def read_output() -> None:
            try:
                if process.stdout:
                    for line in iter(process.stdout.readline, ""):
                        stripped = line.strip()
                        output_queue.put(stripped)
                        if stripped:
                            raw_output_lines_holder[0] += 1
                        # 谓词异常不得被 I/O catch 吞掉（静默截断且可能 success）
                        try:
                            terminal = is_terminal_line(stripped)
                        except Exception as pred_err:  # noqa: BLE001 — 透传给消费端
                            output_queue.put(pred_err)
                            break
                        if terminal:
                            time.sleep(grace)
                            break
                    process.stdout.close()
            except (OSError, IOError, ValueError):
                pass
            finally:
                output_queue.put(None)

        thread = threading.Thread(target=read_output, daemon=True)
        thread_box[0] = thread
        thread.start()

        lines: list[str] = []
        start_time = time.time()
        last_activity_time = time.time()
        timeout_error: CommandTimeoutError | None = None
        predicate_error: BaseException | None = None

        while True:
            now = time.time()
            if max_duration > 0 and (now - start_time) >= max_duration:
                timeout_error = CommandTimeoutError(
                    f"codex 执行超时（总时长超过 {max_duration}s），进程已终止。",
                    is_idle=False,
                )
                break
            if (now - last_activity_time) >= timeout:
                timeout_error = CommandTimeoutError(
                    f"codex 空闲超时（{timeout}s 无输出），进程已终止。",
                    is_idle=True,
                )
                break
            try:
                item = output_queue.get(timeout=0.5)
                if item is None:
                    break
                if isinstance(item, BaseException):
                    predicate_error = item
                    break
                last_activity_time = time.time()
                if item:
                    lines.append(item)
            except queue.Empty:
                if process.poll() is not None and not thread.is_alive():
                    break

        if predicate_error is not None:
            # 谓词抛 CommandTimeoutError 时保留已收集行（与旧外层 list 行为一致）
            if isinstance(predicate_error, CommandTimeoutError):
                predicate_error.partial_lines = list(lines)
                predicate_error.raw_output_lines = len(lines)
            raise predicate_error

        if timeout_error is not None:
            timeout_error.partial_lines = list(lines)
            timeout_error.raw_output_lines = len(lines)
            raise timeout_error

        exit_code: Optional[int] = None
        wait_timeout_error: CommandTimeoutError | None = None
        try:
            exit_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            wait_timeout_error = CommandTimeoutError(
                "codex 进程等待超时，进程已终止。",
                is_idle=False,
            )
        finally:
            if thread.is_alive():
                thread.join(timeout=5)

        if wait_timeout_error is not None:
            wait_timeout_error.partial_lines = list(lines)
            wait_timeout_error.raw_output_lines = len(lines)
            raise wait_timeout_error

        while not output_queue.empty():
            try:
                item = output_queue.get_nowait()
                if item is None:
                    continue
                if isinstance(item, BaseException):
                    if isinstance(item, CommandTimeoutError):
                        item.partial_lines = list(lines)
                        item.raw_output_lines = len(lines)
                    raise item
                if item:
                    lines.append(item)
            except queue.Empty:
                break

        return (lines, exit_code, raw_output_lines_holder[0])

    def _cleanup(
        self,
        process: subprocess.Popen[str],
        thread: threading.Thread | None,
    ) -> None:
        try:
            if process.stdout and not process.stdout.closed:
                process.stdout.close()
        except (OSError, IOError):
            pass
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
        except (ProcessLookupError, OSError):
            pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
