"""行流 seam：Codex 进程执行 adapter。"""

from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Generator, Iterator, Optional, Protocol

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


@contextmanager
def safe_codex_command(
    cmd: list[str],
    timeout: int = 300,
    max_duration: int = 1800,
    prompt: str = "",
    is_terminal_line: Callable[[str], bool] | None = None,
) -> Iterator[Generator[str, None, tuple[Optional[int], int]]]:
    """安全执行 Codex 命令的上下文管理器（生产实现细节）。"""
    if is_terminal_line is None:
        from codex_mcp_cyber.stream import is_turn_completed_line

        is_terminal_line = is_turn_completed_line

    codex_path = shutil.which("codex")
    if not codex_path:
        raise CommandNotFoundError(
            "未找到 codex CLI。请确保已安装 Codex CLI 并添加到 PATH。\n"
            "安装指南：https://developers.openai.com/codex/quickstart"
        )
    popen_cmd = cmd.copy()
    popen_cmd[0] = codex_path

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

    thread: Optional[threading.Thread] = None

    def cleanup() -> None:
        nonlocal thread
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

        output_queue: queue.Queue[str | BaseException | None] = queue.Queue()
        raw_output_lines_holder = [0]
        GRACEFUL_SHUTDOWN_DELAY = 0.3

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
                            time.sleep(GRACEFUL_SHUTDOWN_DELAY)
                            break
                    process.stdout.close()
            except (OSError, IOError, ValueError):
                pass
            finally:
                output_queue.put(None)

        thread = threading.Thread(target=read_output, daemon=True)
        thread.start()

        def generator() -> Generator[str, None, tuple[Optional[int], int]]:
            nonlocal thread
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
                        yield item
                except queue.Empty:
                    if process.poll() is not None and not thread.is_alive():
                        break

            if predicate_error is not None:
                cleanup()
                raise predicate_error

            if timeout_error is not None:
                cleanup()
                raise timeout_error

            exit_code: Optional[int] = None
            try:
                exit_code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                timeout_error = CommandTimeoutError(
                    "codex 进程等待超时，进程已终止。",
                    is_idle=False,
                )
            finally:
                if thread is not None:
                    thread.join(timeout=5)

            if timeout_error is not None:
                raise timeout_error

            while not output_queue.empty():
                try:
                    item = output_queue.get_nowait()
                    if item is None:
                        continue
                    if isinstance(item, BaseException):
                        raise item
                    if item:
                        yield item
                except queue.Empty:
                    break

            return (exit_code, raw_output_lines_holder[0])

        yield generator()

    except Exception:
        cleanup()
        raise
    finally:
        cleanup()


class PopenCodexRunner:
    """生产 adapter：真实 subprocess + 超时。"""

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
        lines: list[str] = []
        exit_code: Optional[int] = None
        raw_output_lines = 0
        try:
            with safe_codex_command(
                cmd,
                timeout=timeout,
                max_duration=max_duration,
                prompt=prompt,
                is_terminal_line=self.is_terminal_line,
            ) as gen:
                it = iter(gen)
                while True:
                    try:
                        line = next(it)
                    except StopIteration as e:
                        if isinstance(e.value, tuple) and len(e.value) == 2:
                            exit_code, raw_output_lines = e.value
                        break
                    if line:
                        lines.append(line)
        except CommandTimeoutError as e:
            # 保留超时前已产出的行，供 last_lines / 诊断（与旧边读边归约一致）
            raise CommandTimeoutError(
                str(e),
                is_idle=e.is_idle,
                partial_lines=lines,
                raw_output_lines=len(lines),
            ) from e
        return ProcessOutcome(
            lines=lines, exit_code=exit_code, raw_output_lines=raw_output_lines
        )
