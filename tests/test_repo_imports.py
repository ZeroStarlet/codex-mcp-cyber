"""仓库自有的独立 Python 入口都必须可导入。

回归背景：一次模块拆分把 path_has_non_ascii / prefer_codex_workdir 从 errors
搬到 paths / winlink，但 scripts/ 下的复现脚本仍从 errors 导入。
当时 `pytest` 只跑 tests/，`ruff check src tests` 也不含 scripts/，
于是这个 ImportError 一路漏到终审才被发现。

本测试守的是「搬移后有没有遗留旧调用点」，不是脚本的业务行为。

**枚举边界以 git 跟踪文件为准**，不用 rglob 扫盘：.gitignore 允许
build/、dist/、venv/、ENV/、.scratch/ 等目录，扫盘会把别人工作区里
成百上千个第三方 .py 当作仓库入口执行 —— 用例数与结果都会随本地工作区漂移。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]

# 单个脚本的导入不该超过这个时间；模块级阻塞必须变红而不是挂住整个套件。
_IMPORT_TIMEOUT_S = 60


def _tracked_standalone_scripts() -> list[Path]:
    """git 跟踪的、src/ 与 tests/ 之外的 Python 文件。

    git 不可用时回退到 scripts/ 的**直接子文件**：递归会钻进
    scripts/build/、scripts/venv/ 这类嵌套 ignored 目录，而降级路径下
    没有 .gitignore 可依据。宁可漏掉嵌套脚本，也不执行 ignored 文件。
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "*.py"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(_REPO),
            check=True,
            timeout=30,
        ).stdout
        rel = [Path(x) for x in out.split("\0") if x]
    except (OSError, subprocess.SubprocessError):
        rel = [p.relative_to(_REPO) for p in (_REPO / "scripts").glob("*.py")]

    return sorted(
        _REPO / r
        for r in rel
        if r.parts[:1] not in (("src",), ("tests",)) and (_REPO / r).is_file()
    )


def test_standalone_scripts_are_discovered() -> None:
    """枚举为空时下面的参数化会静默通过 —— 这里钉住至少有一个入口。"""
    assert _tracked_standalone_scripts(), (
        "未发现 git 跟踪的独立脚本；若确已移除，请一并删除本测试"
    )


@pytest.mark.parametrize(
    "script",
    _tracked_standalone_scripts(),
    ids=lambda p: p.relative_to(_REPO).as_posix(),
)
def test_standalone_script_imports_clean(script: Path) -> None:
    """在子进程里导入脚本，捕获搬移遗留的 ImportError。

    用子进程而非 importlib：脚本有模块级 sys.path 改写与副作用，
    不该污染测试进程。run_name 非 __main__ 以跳过 CLI 主流程。
    """
    code = (
        "import runpy, sys;"
        f"sys.argv = [{str(script)!r}];"
        f"runpy.run_path({str(script)!r}, run_name='__not_main__')"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(_REPO),
            check=False,
            timeout=_IMPORT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"{script.relative_to(_REPO)} 导入超过 {_IMPORT_TIMEOUT_S}s —— "
            "疑似模块级阻塞（网络 / 输入等待）"
        )

    assert result.returncode == 0, (
        f"{script.relative_to(_REPO)} 导入失败：\n{result.stdout}\n{result.stderr}"
    )
