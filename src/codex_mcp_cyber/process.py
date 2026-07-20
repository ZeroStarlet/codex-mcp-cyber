"""行流 seam：Codex 进程执行 adapter。

行流词汇（``ProcessOutcome`` / ``Terminal``）定义在 stream；本模块只放
seam 声明（``CodexProcessRunner``）与生产 adapter（``PopenCodexRunner``）。
历史导入路径 ``codex_mcp_cyber.process.ProcessOutcome`` 经由本导入仍然可用。
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Protocol

from codex_mcp_cyber.errors import CommandNotFoundError, CommandTimeoutError
from codex_mcp_cyber.paths import format_cli_path
from codex_mcp_cyber.stream import ProcessOutcome, Terminal, is_turn_completed_line


class CodexProcessRunner(Protocol):
    """行流 seam：执行命令并产出 ProcessOutcome（含 timeout terminal）。

    ``workdir``：子进程工作目录；None 表示继承当前进程目录。
    它属于 interface 而非某个具体 adapter 的构造字段 —— 否则调用方得先
    isinstance 认出具体 adapter 才能传，其余 adapter 会静默收不到。

    0.3.0 起 run_review 无条件传入 workdir=，未声明该参数的自定义 adapter
    会抛 TypeError。这是有意的响亮失败：静默收不到 cwd 正是本参数要消灭的
    故障模式（见 CHANGELOG.md）。
    """

    def run(
        self,
        cmd: list[str],
        *,
        prompt: str,
        workdir: Path | str | None = None,
        timeout: int,
        max_duration: int,
    ) -> ProcessOutcome: ...


class PopenCodexRunner:
    """生产 adapter：真实 subprocess + 超时 + early-stop。

    对外 interface 仅 ``run(...) -> ProcessOutcome``（及 ``__init__`` 的谓词注入）。
    超时走 terminal，不 raise CommandTimeoutError。

    ``workdir`` 经 ``run(...)`` 传入并设为 Popen 的 cwd，使 Codex 子工具的相对路径
    解析落在审核目录。
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
        workdir: Path | str | None = None,
        timeout: int,
        max_duration: int,
    ) -> ProcessOutcome:
        terminal = self._resolve_terminal_predicate()
        popen_cmd = self._resolve_codex_path(cmd)
        process: subprocess.Popen[str] | None = None
        thread_box: list[threading.Thread | None] = [None]
        try:
            process = self._spawn(popen_cmd, prompt=prompt, workdir=workdir)
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

    def _spawn(
        self,
        popen_cmd: list[str],
        *,
        prompt: str,
        workdir: Path | str | None,
    ) -> subprocess.Popen[str]:
        cwd: str | None = None
        if workdir is not None:
            cwd = format_cli_path(Path(workdir))
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

        def interrupted(term: Terminal, message: str) -> ProcessOutcome:
            """中断类单通道结局：无退出码，行数以已收到的为准。"""
            return ProcessOutcome(
                lines=list(lines),
                exit_code=None,
                raw_output_lines=len(lines),
                terminal=term,
                error_message=message,
            )

        def from_predicate_timeout(err: CommandTimeoutError) -> ProcessOutcome:
            """注入型谓词报告的超时 → 单通道结局。两处收敛点共用。"""
            return interrupted("idle_timeout" if err.is_idle else "timeout", str(err))

        if predicate_error is not None:
            if isinstance(predicate_error, CommandTimeoutError):
                return from_predicate_timeout(predicate_error)
            raise predicate_error

        if timeout_terminal is not None:
            return interrupted(timeout_terminal, timeout_message)

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
            return interrupted("timeout", wait_message)

        while not output_queue.empty():
            try:
                item = output_queue.get_nowait()
                if item is None:
                    continue
                if isinstance(item, BaseException):
                    if isinstance(item, CommandTimeoutError):
                        return from_predicate_timeout(item)
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
