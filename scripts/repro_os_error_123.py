#!/usr/bin/env python3
"""Red/green feedback loop for Windows os error 123 on codex MCP.

Two failure shapes observed in production:

1) MCP ``cd`` arrives with *literal* surrounding quotes, e.g.
     '"C:/Users/Starlet/Desktop/sp_web_api"'
   Windows rejects as illegal path (WinError 123) — fixed by normalize_workdir.

2) Real Chinese / non-ASCII workdir is legal for --cd, but Codex *internal tools*
   later hit os error 123 (8.3 short names often disabled). Mitigated by
   prefer_codex_workdir() ASCII directory junction + Popen cwd.

Usage (from repo root):
  uv run python scripts/repro_os_error_123.py
  uv run python scripts/repro_os_error_123.py --expect-fixed
  uv run python scripts/repro_os_error_123.py --expect-fixed --workdir "C:/Users/you/中文路径/repo"

Exit codes:
  0  — loop verdict matches expectation
  1  — unexpected success/failure
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from codex_mcp_cyber.classify import looks_like_invalid_path_error  # noqa: E402
from codex_mcp_cyber.paths import path_has_non_ascii  # noqa: E402
from codex_mcp_cyber.tools.codex import codex_tool  # noqa: E402
from codex_mcp_cyber.winlink import prefer_codex_workdir  # noqa: E402


def _quoted(path: Path) -> str:
    return f'"{path.as_posix()}"'


async def run_case(
    name: str,
    cd_value: str | Path,
    prompt: str = "Reply with the single word PONG. No file changes.",
) -> dict:
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
    # 123 特征判定复用 classify 的单一来源，避免与生产判定各自漂移
    has_123 = any(looks_like_invalid_path_error(str(x)) for x in last_lines)
    has_123 = has_123 or looks_like_invalid_path_error(str(result.get("error") or ""))
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
        help="After the fix: quoted cd must succeed (green).",
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
    preferred = prefer_codex_workdir(workdir)

    print(f"workdir={workdir}")
    print(f"non_ascii={path_has_non_ascii(workdir)}")
    print(f"preferred_codex_workdir={preferred}")
    print(f"plain_cd={plain!r}")
    print(f"quoted_cd={quoted!r}")
    print("---")

    control = await run_case("plain_cd", plain)
    buggy = await run_case("quoted_cd", quoted)

    print(json.dumps({"control": control, "buggy": buggy}, ensure_ascii=False, indent=2))
    print("---")

    if not control["success"]:
        print("LOOP BROKEN: plain cd failed — cannot diagnose os error 123 in this env.")
        print(control)
        return 2

    if args.expect_fixed:
        ok = buggy["success"] and not buggy["has_os_error_123"]
        print("EXPECT: quoted_cd SUCCESS (fix applied)")
        print("VERDICT:", "GREEN" if ok else "RED (fix missing or regressed)")
        return 0 if ok else 1

    red = (not buggy["success"]) and buggy["has_os_error_123"]
    print("EXPECT: quoted_cd FAIL with os error 123 (historical bug present)")
    print(
        "VERDICT:",
        "RED (bug present — good for diagnosis)"
        if red
        else "NOT RED (bug absent or different symptom)",
    )
    return 0 if red else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
