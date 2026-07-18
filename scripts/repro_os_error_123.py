#!/usr/bin/env python3
"""Red/green feedback loop for Windows os error 123 on codex MCP.

Symptom reproduced from production transcripts (sp_web_api review):
  error_kind: protocol_missing_session
  last_lines: ["Error: 文件名、目录名或卷标语法不正确。 (os error 123)"]

Root trigger: `cd` arrives with *literal* surrounding quotes, e.g.
  '"C:/Users/Starlet/Desktop/sp_web_api"'
which Windows rejects as an illegal path (WinError 123).

Usage (from repo root):
  uv run python scripts/repro_os_error_123.py
  uv run python scripts/repro_os_error_123.py --expect-fixed

Exit codes:
  0  — loop verdict matches expectation
  1  — unexpected success/failure (bug still present, or fix regressed)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running against either src/ layout or installed package.
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from codex_mcp_cyber.tools.codex import codex_tool  # noqa: E402


def _quoted(path: Path) -> str:
    # Literal quote characters wrapped around a real absolute path — the
    # exact shape observed in failing MCP tool_use inputs.
    return f'"{path.as_posix()}"'


async def run_case(name: str, cd_value: str | Path, prompt: str = "Reply with the single word PONG. No file changes.") -> dict:
    result = await codex_tool(
        PROMPT=prompt,
        cd=cd_value,  # type: ignore[arg-type]
        sandbox="read-only",
        SESSION_ID="",
        skip_git_repo_check=True,
        timeout=45,
        max_duration=60,
        max_retries=0,
        return_all_messages=True,
        return_metrics=True,
    )
    detail = result.get("error_detail") or {}
    last_lines = detail.get("last_lines") or []
    has_123 = any("os error 123" in str(x) or "文件名、目录名或卷标语法不正确" in str(x) for x in last_lines)
    has_123 = has_123 or ("os error 123" in str(result.get("error") or ""))
    return {
        "case": name,
        "cd_repr": repr(cd_value) if not isinstance(cd_value, Path) else f"Path({cd_value!s})",
        "success": bool(result.get("success")),
        "error_kind": result.get("error_kind"),
        "has_os_error_123": has_123,
        "last_lines": last_lines,
        "result_snip": (result.get("result") or result.get("error") or "")[:180],
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--expect-fixed",
        action="store_true",
        help="After the fix: quoted cd must succeed (green). Default: expect red on quoted cd.",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path.cwd(),
        help="A real existing directory to use as --cd base (default: cwd).",
    )
    args = parser.parse_args()
    workdir = args.workdir.resolve()
    if not workdir.is_dir():
        print(f"FATAL: workdir does not exist: {workdir}", file=sys.stderr)
        return 2

    plain = workdir.as_posix()
    quoted = _quoted(workdir)

    print(f"workdir={workdir}")
    print(f"plain_cd={plain!r}")
    print(f"quoted_cd={quoted!r}")
    print("---")

    control = await run_case("plain_cd", plain)
    buggy = await run_case("quoted_cd", quoted)

    print(json.dumps({"control": control, "buggy": buggy}, ensure_ascii=False, indent=2))
    print("---")

    # Control must always work; otherwise the environment is broken.
    if not control["success"]:
        print("LOOP BROKEN: plain cd failed — cannot diagnose os error 123 in this env.")
        print(control)
        return 2

    if args.expect_fixed:
        # Green when quoted path is accepted (sanitized) and no 123.
        ok = buggy["success"] and not buggy["has_os_error_123"]
        print("EXPECT: quoted_cd SUCCESS (fix applied)")
        print("VERDICT:", "GREEN" if ok else "RED (fix missing or regressed)")
        return 0 if ok else 1

    # Default: red-capable assertion for the historical bug.
    red = (not buggy["success"]) and buggy["has_os_error_123"]
    print("EXPECT: quoted_cd FAIL with os error 123 (historical bug present)")
    print("VERDICT:", "RED (bug present — good for diagnosis)" if red else "NOT RED (bug absent or different symptom)")
    # Exit 0 when we successfully observed the bug (loop is red-capable).
    # Exit 1 when we failed to observe it (can't use this loop).
    return 0 if red else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
