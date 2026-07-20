"""行流 seam 的测试 adapter。

生产 module 只放生产 adapter（PopenCodexRunner）；这两个回放式 adapter
只服务测试，随生产包发布没有意义 —— 删掉它们生产代码零影响。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from codex_mcp_cyber.process import ProcessOutcome, Terminal


@dataclass
class ScriptedLinesRunner:
    """测试 adapter：回放固定行序列，不碰 OS。

    记录每次调用收到的 workdir，供「workdir 确实穿过 seam」类断言使用。
    """

    lines: list[str]
    exit_code: Optional[int] = 0
    terminal: Terminal = "completed"
    error_message: str = ""
    calls: int = 0
    seen_workdirs: list[Path | str | None] = field(default_factory=list)

    def run(
        self,
        cmd: list[str],
        *,
        prompt: str,
        workdir: Path | str | None = None,
        timeout: int,
        max_duration: int,
    ) -> ProcessOutcome:
        del cmd, prompt, timeout, max_duration
        self.calls += 1
        self.seen_workdirs.append(workdir)
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
    seen_workdirs: list[Path | str | None] = field(default_factory=list)

    def run(
        self,
        cmd: list[str],
        *,
        prompt: str,
        workdir: Path | str | None = None,
        timeout: int,
        max_duration: int,
    ) -> ProcessOutcome:
        del cmd, prompt, timeout, max_duration
        if self.calls >= len(self.steps):
            raise RuntimeError("SequenceRunner exhausted")
        step = self.steps[self.calls]
        self.calls += 1
        self.seen_workdirs.append(workdir)
        return step
