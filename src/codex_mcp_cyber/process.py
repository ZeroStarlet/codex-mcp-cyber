"""行流 seam：Codex 进程执行 adapter。"""

from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional, Protocol

from codex_mcp_cyber.errors import CommandNotFoundError, CommandTimeoutError, format_cli_path

Terminal = Literal["completed", "timeout", "idle_timeout"]


@dataclass(frozen=True)
class ProcessOutcome:
    """一次进程执行的行流结果（含超时终态；单通道）。"""

    lines: list[str]
    exit_code: Optional[int]
    raw_output_lines: int
    terminal: Terminal = "completed"
    error_message: str = ""


class CodexProcessRunner(Protocol):
    """行流 seam：执行命令并产出 ProcessOutcome（含 timeout terminal）。"""

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
    terminal: Terminal = "completed"
    error_message: str = ""
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
        return ProcessOutcome(
            lines=lines,
            exit_code=self.exit_code,
            raw_output_lines=raw,
            terminal=self.terminal,
            error_message=self.error_message,
        )


@dataclass
class SequenceRunner:
    """测试 adapter：按调用次序返回不同 ProcessOutcome。"""

    steps: list[ProcessOutcome]
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
        return step


class PopenCodexRunner:
    """生产 adapter：真实 subprocess + 超时 + early-stop。

    对外 interface 仅 ``run(...) -> ProcessOutcome``（及 ``__init__`` 的谓词注入）。
    超时走 terminal，不 raise CommandTimeoutError。

    ``workdir``：可选，设为 Popen 的 cwd，使 Codex 子工具相对路径解析落在审核目录
   （Windows 中文路径场景下通常是 ASCII 目录联接）。
    """

    GRACEFUL_SHUTDOWN_DELAY = 0.3

    def __init__(
        self,
        is_terminal_line: Callable[[str], bool] | None = None,
        *,
        workdir: Path | str | None = None,
    ) -> None:
        self.is_terminal_line = is_terminal_line
        self.workdir = workdir

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
        thread_box: list[threading.Thread | None] = [None]
        try:
            process = self._spawn(popen_cmd, prompt=prompt)
            return self._drain(
                process,
                timeout=timeout,
                max_duration=max_duration,
                is_terminal_line=terminal,
                thread_box=thread_box,
            )
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
        cwd: str | None = None
        if self.workdir is not None:
            cwd = format_cli_path(Path(self.workdir))
        process = subprocess.Popen(
            popen_cmd,
            shell=False,
            cwd=cwd,
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
    ) -> ProcessOutcome:
        """读 stdout 至终态；超时返回 terminal=timeout|idle_timeout 的 ProcessOutcome。"""
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
                        try:
                            terminal = is_terminal_line(stripped)
                        except Exception as pred_err:  # noqa: BLE001
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
        timeout_terminal: Terminal | None = None
        timeout_message = ""
        predicate_error: BaseException | None = None

        while True:
            now = time.time()
            if max_duration > 0 and (now - start_time) >= max_duration:
                timeout_terminal = "timeout"
                timeout_message = (
                    f"codex 执行超时（总时长超过 {max_duration}s），进程已终止。"
                )
                break
            if (now - last_activity_time) >= timeout:
                timeout_terminal = "idle_timeout"
                timeout_message = (
                    f"codex 空闲超时（{timeout}s 无输出），进程已终止。"
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
            # 谓词抛 CommandTimeoutError → 收成单通道 ProcessOutcome
            if isinstance(predicate_error, CommandTimeoutError):
                term: Terminal = (
                    "idle_timeout" if predicate_error.is_idle else "timeout"
                )
                return ProcessOutcome(
                    lines=list(lines),
                    exit_code=None,
                    raw_output_lines=len(lines),
                    terminal=term,
                    error_message=str(predicate_error),
                )
            raise predicate_error

        if timeout_terminal is not None:
            return ProcessOutcome(
                lines=list(lines),
                exit_code=None,
                raw_output_lines=len(lines),
                terminal=timeout_terminal,
                error_message=timeout_message,
            )

        exit_code: Optional[int] = None
        wait_timeout = False
        wait_message = ""
        try:
            exit_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            wait_timeout = True
            wait_message = "codex 进程等待超时，进程已终止。"
        finally:
            if thread.is_alive():
                thread.join(timeout=5)

        if wait_timeout:
            return ProcessOutcome(
                lines=list(lines),
                exit_code=None,
                raw_output_lines=len(lines),
                terminal="timeout",
                error_message=wait_message,
            )

        while not output_queue.empty():
            try:
                item = output_queue.get_nowait()
                if item is None:
                    continue
                if isinstance(item, BaseException):
                    if isinstance(item, CommandTimeoutError):
                        term = "idle_timeout" if item.is_idle else "timeout"
                        return ProcessOutcome(
                            lines=list(lines),
                            exit_code=None,
                            raw_output_lines=len(lines),
                            terminal=term,
                            error_message=str(item),
                        )
                    raise item
                if item:
                    lines.append(item)
            except queue.Empty:
                break

        return ProcessOutcome(
            lines=lines,
            exit_code=exit_code,
            raw_output_lines=raw_output_lines_holder[0],
            terminal="completed",
        )

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
