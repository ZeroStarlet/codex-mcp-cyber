"""wire 契约的文档表述 ↔ 代码单一来源互钉。

codex-guide.md 的读者是 LLM（cc-review 技能按它调用工具），文档就是接口的
一部分：参数表、error_kind 表、返回键、诊断行数、安装命令、已移除机制的
残留描述若与代码漂移，会直接变成调用方行为错误（历史：文档承诺过代码永不
发出的幽灵种类 `json_decode`；0.5.0 移除联接层后活跃文档曾残留旧指引）。
本文件让漂移在 CI 变红，而不是在终审现场暴露。

互钉方向：期望值一律从代码单一来源**派生**（to_wire 真实输出、
inspect.signature、is_retryable_error、setup.sh 命令行），不在测试里
手抄第二份契约。
"""

from __future__ import annotations

import inspect
import json
import re
import tomllib
from pathlib import Path

import codex_mcp_cyber
from codex_mcp_cyber.classify import is_retryable_error
from codex_mcp_cyber.errors import ErrorKind, build_error_detail
from codex_mcp_cyber.redact import LAST_LINES_LIMIT
from codex_mcp_cyber.review import ReviewResult, to_wire
from codex_mcp_cyber.tools.codex import codex_tool

_REPO = Path(__file__).resolve().parents[1]
_GUIDE = (_REPO / "skills" / "cc-review" / "codex-guide.md").read_text(
    encoding="utf-8"
)
_SCENARIOS = (_REPO / "skills" / "cc-review" / "scenarios.md").read_text(
    encoding="utf-8"
)


def _error_kind_values() -> set[str]:
    return {
        v
        for k, v in vars(ErrorKind).items()
        if not k.startswith("_") and isinstance(v, str)
    }


def _section(text: str, heading: str) -> str:
    """取 heading 起、至下一个二/三级标题前的片段。"""
    start = text.index(heading)
    rest = text[start + len(heading) :]
    nxt = re.search(r"\n#{2,3}\s", rest)
    return rest[: nxt.start()] if nxt else rest


def _strip_ps_comments(text: str) -> str:
    """剥 PowerShell 注释：<# … #> 块注释 + 引号外的 # 至行尾（含整行与行尾注释）。

    逐行引号状态机（' 与 " 各自成对闭合）——被注释掉的调用不得充当
    「真实 argv」证据，而字符串里的 # 不误伤。守卫对象是本仓库自己的
    setup.ps1，不追求覆盖反引号转义等深层语法。
    """
    text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    out_lines: list[str] = []
    for ln in text.splitlines():
        quote: str | None = None
        cut = len(ln)
        for i, ch in enumerate(ln):
            if quote is None:
                if ch in ("'", '"'):
                    quote = ch
                elif ch == "#":
                    cut = i
                    break
            elif ch == quote:
                quote = None
        out_lines.append(ln[:cut])
    return "\n".join(out_lines)


def test_error_kind_table_matches_enum() -> None:
    """文档 error_kind 表 == ErrorKind 全集（不多不少，杜绝幽灵种类）。"""
    section = _section(_GUIDE, "### error_kind")
    doc_kinds = set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|", section, re.M))
    assert doc_kinds == _error_kind_values()


def _parse_doc_default(token: str) -> object:
    if token == '""':
        return ""
    if token in ("True", "False"):
        return token == "True"
    if token == "None":
        return None
    if token.isdigit():
        return int(token)
    return token


def test_param_table_matches_codex_tool_signature() -> None:
    """文档参数表 == wire 壳 15 参签名：名称全集、必填列、
    以及**每个可选参数**的默认值（含 bool / str / None，类型也互钉）。"""
    section = _section(_GUIDE, "## 参数说明")
    rows = re.findall(r"^\|\s*([A-Za-z_]+)\s*\|(.*)$", section, re.M)
    doc = {name: rest for name, rest in rows if name != "参数"}
    sig = inspect.signature(codex_tool).parameters
    assert set(doc) == set(sig)

    # 必填列（✅）== 签名中无默认值的参数
    doc_required = {name for name, rest in doc.items() if "✅" in rest}
    sig_required = {
        n for n, p in sig.items() if p.default is inspect.Parameter.empty
    }
    assert doc_required == sig_required

    # 每个可选参数的文档行都必须写明「默认 …」，且值与类型均等于签名默认值
    #（只钉整数会漏掉 bool 翻转 / 字符串与 None 漂移）。
    # token 取「默认」后到分隔符为止的**完整**连续串（贪婪，不做字面量交替），
    # 再交给解析器严格判定 —— 否则 `Truee` 会被前缀匹配成 `True`。
    # 分隔符同时含中英文标点：`默认 True, …` 是合法表述，不得误报。
    for name, p in sig.items():
        if p.default is inspect.Parameter.empty:
            continue
        m = re.search(r"默认\s*`?([^`\s，。；（）,.;()]+)`?", doc[name])
        assert m, f"参数表 {name} 行必须写明默认值（默认 …）"
        parsed = _parse_doc_default(m.group(1))
        assert parsed == p.default and type(parsed) is type(p.default), (
            f"{name} 文档默认值 {parsed!r} 与签名默认值 {p.default!r} 不一致"
        )


def test_return_examples_match_wire_shapes() -> None:
    """文档 JSON 示例顶层键 == 真实 to_wire 键集（精确相等，双向防漂移）；
    error_detail 嵌套键 ⊆ build_error_detail 可能产生的键。"""
    section = _section(_GUIDE, "## 返回值")
    block = re.search(r"```json\n(.*?)```", section, re.S)
    assert block, "返回值章节应有 JSON 示例"
    success_src, sep, failure_src = block.group(1).partition("// 失败")
    assert sep, "示例应含 // 失败 分隔的两个对象"

    def parse(src: str) -> dict:
        body = "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("//")
        )
        return json.loads(body)

    doc_success = parse(success_src)
    doc_failure = parse(failure_src)

    # 顶层键从代表性 to_wire 输出派生，精确相等：
    # 文档多键（幽灵字段）或少键（wire 删字段后文档遗留）都变红。
    assert set(doc_success) == set(to_wire(ReviewResult(success=True)))
    assert set(doc_failure) == set(to_wire(ReviewResult(success=False)))

    # error_detail 夹逼：必有键（最小 probe）⊆ 文档示例键 ⊆ 可能键（全参 probe）
    # —— 只设上界会放过「文档删掉恒在的 message」这类漂移。
    minimal_detail = build_error_detail("m")
    probe_detail = build_error_detail(
        "m",
        exit_code=1,
        last_lines=["x"],
        json_decode_errors=1,
        idle_timeout_s=1,
        max_duration_s=1,
        retries=1,
    )
    doc_detail_keys = set(doc_failure["error_detail"])
    missing = set(minimal_detail) - doc_detail_keys
    assert not missing, f"error_detail 示例缺少恒在键：{missing}"
    unknown = doc_detail_keys - set(probe_detail)
    assert not unknown, f"error_detail 示例含代码不会产生的键：{unknown}"


def test_last_lines_window_matches_limit() -> None:
    """文档写的诊断窗口行数 == redact.LAST_LINES_LIMIT（历史：写成 20）。"""
    m = re.search(r"最后(\d+)行", _GUIDE)
    assert m, "codex-guide.md 应说明 last_lines 行数窗口"
    assert int(m.group(1)) == LAST_LINES_LIMIT


def test_docs_have_no_phantom_json_decode_kind() -> None:
    """`json_decode` 不是 error_kind（0.3.0 已删）；只允许 json_decode_errors 计数字段。"""
    for name, doc in (("codex-guide.md", _GUIDE), ("scenarios.md", _SCENARIOS)):
        assert "`json_decode`" not in doc, f"{name} 引用了不存在的 error_kind"


def test_scenarios_retry_rows_match_classifier() -> None:
    """场景 F 表的重试语义与 classify.is_retryable_error 互钉。

    「不重试」行的种类 token 集必须**恰等于**全部不可重试种类；
    「重试」行的 token 必须真实存在且可重试。两行都必须出现（防空过）。
    """
    section = _section(_SCENARIOS, "## F. 工具不可用")
    non_retryable = {k for k in _error_kind_values() if not is_retryable_error(k)}
    checked_no_retry = False
    checked_retry = False
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        first, rest = cells[0], " ".join(cells[1:])
        tokens = set(re.findall(r"`([a-z_]+)`", first))
        if not tokens:
            continue
        if "不重试" in rest:
            assert tokens == non_retryable, (
                f"不重试行应恰为 {sorted(non_retryable)}，实为 {sorted(tokens)}"
            )
            checked_no_retry = True
        elif "重试" in rest:
            unknown = tokens - _error_kind_values()
            assert not unknown, f"重试行引用了不存在的种类：{unknown}"
            wrong = {t for t in tokens if not is_retryable_error(t)}
            assert not wrong, f"重试行含不可重试种类：{wrong}"
            # 两个典型可重试种类必须始终列出（「等」只允许省略其余），
            # 否则删掉 timeout 后仅剩 upstream_error 也能通过。
            required_examples = {ErrorKind.TIMEOUT, ErrorKind.UPSTREAM_ERROR}
            assert required_examples <= tokens, (
                f"重试行至少应列出 {sorted(required_examples)}，实为 {sorted(tokens)}"
            )
            checked_retry = True
    assert checked_no_retry and checked_retry, "F 表应同时存在「不重试」与「重试」两行"


def test_active_docs_do_not_reference_removed_junction_layer() -> None:
    """0.5.0 移除了 ASCII 目录联接层——**所有**活跃文档不得再描述联接行为。

    扫描整个 skills/cc-review/ 目录 + README* + CONTEXT.md（不只抽查两份），
    禁的是行为词汇本身（「联接」/「junction」），不只缓存路径字符串——
    历史叙述只属于 CHANGELOG。枚举数量下限防空过。
    """
    forbidden = ("codex-mcp-cyber-v3-", "wd-junctions", "联接", "junction")
    doc_paths = sorted((_REPO / "skills" / "cc-review").glob("*.md"))
    doc_paths += [
        _REPO / "README.md",
        _REPO / "README_EN.md",
        _REPO / "CONTEXT.md",
    ]
    assert len(doc_paths) >= 10, f"活跃文档枚举异常（仅 {len(doc_paths)} 份，不得空过）"
    for p in doc_paths:
        text = p.read_text(encoding="utf-8")
        for tok in forbidden:
            assert tok not in text, f"{p.name} 仍引用已移除的联接层：{tok!r}"


def test_readme_remote_install_matches_setup_scripts() -> None:
    """README 远程安装命令必须存在，且与 setup.sh 的 uvx 命令逐词一致。

    单一来源 = setup.sh 的命令行；README 命令缺行（vacuous）、缺 --refresh、
    仓库地址漂移都变红。setup.ps1 以数组形式给 argv，至少钉 --refresh 存在。
    """
    setup_sh = (_REPO / "setup.sh").read_text(encoding="utf-8")
    m = re.search(r"uvx\s+[^\r\n]*git\+https[^\r\n]*", setup_sh)
    assert m, "setup.sh 应包含 uvx 远程安装命令"
    canonical = re.sub(r"\s+", " ", m.group(0)).strip()
    assert "--refresh" in canonical

    for name in ("README.md", "README_EN.md"):
        text = (_REPO / name).read_text(encoding="utf-8")
        # README 命令用反斜杠续行，先折叠续行再逐行归一空白
        folded = re.sub(r"\\\s*\r?\n\s*", " ", text)
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in folded.splitlines()]
        matches = [ln for ln in lines if "uvx" in ln and "git+https" in ln]
        assert matches, f"{name} 缺少 uvx 远程安装命令（不得空过）"
        assert any(canonical in ln for ln in matches), (
            f"{name} 的安装命令与 setup.sh 不一致；应含：{canonical}"
        )

    # setup.ps1 的**真实 argv**与 canonical 逐 token 对照。
    # 先剥全部注释（块注释 + 引号外的整行/行尾 # 注释，被注释的调用不算数），
    # 再**跨行**匹配 Invoke-Claude 的 -Arguments 数组（合法多行排版不误拒）；
    # token 同时接受单/双引号（两种写法语义等价）。
    setup_ps1 = (_REPO / "setup.ps1").read_text(encoding="utf-8")
    live = _strip_ps_comments(setup_ps1)
    arrays = re.findall(
        r"Invoke-Claude[^(]*?-Arguments\s*@\((.*?)\)", live, flags=re.S
    )
    uvx_arrays = [a for a in arrays if re.search(r"""['"]uvx['"]""", a)]
    assert uvx_arrays, (
        "setup.ps1 应存在未被注释的 Invoke-Claude -Arguments 调用（含 uvx，不得空过）"
    )
    for arr in uvx_arrays:
        tokens = re.findall(r"""["']([^"']*)["']""", arr)
        tail = tokens[tokens.index("uvx") :]
        assert tail == canonical.split(), (
            f"setup.ps1 实际 argv 与 setup.sh 不一致：{tail}"
        )


def test_package_version_single_source() -> None:
    """pyproject 与 __init__ 的版本字面量是手工镜像 —— 互钉防漂移。"""
    with open(_REPO / "pyproject.toml", "rb") as f:
        py = tomllib.load(f)
    assert py["project"]["version"] == codex_mcp_cyber.__version__
