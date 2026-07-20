"""行流：行流结果（ProcessOutcome）与归约 —— JSONL / 纯文本 → StreamOutcome。

单入口 ``reduce_codex_stream(ProcessOutcome)``，终态在实现内折叠：

- completed：折叠事件后叠加 finalize 优先级（invalid_path / 缺会话标识 /
  空正文 / 退出码非零）。
- timeout / idle_timeout：只折叠诊断信息（last_lines / json_decode_errors /
  all_messages），正文与会话标识不外流，错误种类由终态决定，**不做**
  finalize 补判 —— 超时终局本就没有会话标识与正文，补判会把 timeout
  盖成 protocol_missing_session / empty_result，丢掉真正的失败原因。

调用方因此无需知道「何时 finalize」；该顺序不变量属于本模块实现。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from codex_mcp_cyber.classify import is_auth_error, looks_like_invalid_path_error
from codex_mcp_cyber.errors import ErrorKind
from codex_mcp_cyber.redact import redact_tool_result_event, tail_window

Terminal = Literal["completed", "timeout", "idle_timeout"]


@dataclass(frozen=True)
class ProcessOutcome:
    """一次进程执行的行流结果（含超时终态；单通道）。

    行流词汇的一部分，定义在本模块；runner seam（process）导入使用。
    """

    lines: list[str]
    exit_code: Optional[int]
    raw_output_lines: int
    terminal: Terminal = "completed"
    error_message: str = ""


@dataclass
class StreamOutcome:
    """归约结局：字段用领域词（与 ReviewResult 对齐）。

    exit_code / raw_output_lines 来自行流结果，随归约一起交付，
    编排层无需再拼第二个同构结构。
    """

    text: str = ""
    session_id: Optional[str] = None
    had_error: bool = False
    error_message: str = ""
    error_kind: Optional[str] = None
    # 分类证据（非展示）：折叠期错误文本命中过 os error 123 特征。
    saw_invalid_path_text: bool = False
    exit_code: Optional[int] = None
    raw_output_lines: int = 0
    json_decode_errors: int = 0
    last_lines: list[str] = field(default_factory=list)
    all_messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _OkEvent:
    """内部：一行解码后的结构化事件。"""

    data: dict[str, Any]


@dataclass(frozen=True)
class _JsonDecode:
    line: str


@dataclass(frozen=True)
class _Malformed:
    line: str
    error: Exception


def is_turn_completed_line(line: str) -> bool:
    """行流 early-stop 默认谓词：事件 type == turn.completed。"""
    try:
        data = json.loads(line)
        return isinstance(data, dict) and data.get("type") == "turn.completed"
    except (json.JSONDecodeError, AttributeError, TypeError):
        return False


def _decode_line(line: str) -> _OkEvent | _JsonDecode | _Malformed:
    """一行 → 结构化事件 / JSON 解码失败 / 其它异常（内部 seam）。"""
    try:
        parsed = json.loads(line.strip())
    except json.JSONDecodeError:
        return _JsonDecode(line=line)
    except Exception as error:  # noqa: BLE001 — 与历史行为一致
        return _Malformed(line=line, error=error)

    if not isinstance(parsed, dict):
        return _Malformed(
            line=line,
            error=TypeError(
                f"expected JSON object event, got {type(parsed).__name__}"
            ),
        )
    return _OkEvent(data=parsed)


def _require_item(line_dict: dict[str, Any]) -> dict[str, Any]:
    item = line_dict.get("item", {})
    if item is None or not isinstance(item, dict):
        raise TypeError(f"expected item object, got {type(item).__name__}")
    return item


def _require_str(value: Any, label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"expected {label} str, got {type(value).__name__}")
    return value


def _note_error_text(out: StreamOutcome, text: str) -> None:
    """错误文本唯一入口：进展示通道，同时即时抽取 123 分类证据。

    证据在入口处一次判定，finalize 只读字段 —— 不得对拼好的
    error_message 正则回扫（0.5.1 事故的机制根源）。
    """
    out.error_message += text
    if looks_like_invalid_path_error(text):
        out.saw_invalid_path_text = True


def _fold_ok_event(
    out: StreamOutcome,
    line_dict: dict[str, Any],
    *,
    collect_messages: bool,
) -> None:
    """把一个合法事件叠到 StreamOutcome（畸形 → raise TypeError）。"""
    if collect_messages:
        out.all_messages.append(redact_tool_result_event(line_dict))

    item = _require_item(line_dict)
    item_type = item.get("type", "")
    if item_type is None or not isinstance(item_type, str):
        raise TypeError(f"expected item.type str, got {type(item_type).__name__}")

    if item_type == "agent_message":
        text = item.get("text", "")
        # None 与非 str 一律畸形（历史：不得 str() 成 success）
        if text is None or not isinstance(text, str):
            raise TypeError(
                f"expected agent_message.text str, got {type(text).__name__}"
            )
        out.text += text

    # 会话证据仅认非空白字符串：空串 / 纯空白没法用于复审 resume，视同
    # 未建会话；非字符串按畸形事件处理（与 agent_message.text 同策）。
    thread_id = line_dict.get("thread_id")
    if thread_id is not None:
        if not isinstance(thread_id, str):
            raise TypeError(
                f"expected thread_id str, got {type(thread_id).__name__}"
            )
        if thread_id.strip():
            out.session_id = thread_id

    event_type = line_dict.get("type", "")
    if event_type is None:
        event_type = ""
    event_type = _require_str(event_type, "event type")

    if "fail" in event_type:
        out.had_error = True
        err_obj = line_dict.get("error", {})
        if err_obj is None or not isinstance(err_obj, dict):
            raise TypeError(f"expected error object, got {type(err_obj).__name__}")
        fail_msg = _require_str(err_obj.get("message", ""), "error.message")
        _note_error_text(out, "\n\n[codex error] " + fail_msg)
        if is_auth_error(fail_msg):
            out.error_kind = ErrorKind.AUTH_REQUIRED
        elif out.error_kind != ErrorKind.AUTH_REQUIRED:
            out.error_kind = ErrorKind.UPSTREAM_ERROR

    if "error" in event_type:
        error_msg = _require_str(line_dict.get("message", ""), "error message")
        is_reconnecting = bool(
            re.match(r"^Reconnecting\.\.\.\s+\d+/\d+$", error_msg)
        )
        if not is_reconnecting:
            out.had_error = True
            _note_error_text(out, "\n\n[codex error] " + error_msg)
            if is_auth_error(error_msg):
                out.error_kind = ErrorKind.AUTH_REQUIRED
            elif out.error_kind != ErrorKind.AUTH_REQUIRED:
                out.error_kind = ErrorKind.UPSTREAM_ERROR


def _fold_lines(
    lines: list[str],
    *,
    collect_messages: bool = False,
) -> StreamOutcome:
    """把一行流行列折叠成结构化结局（不含终态与 finalize 判定）。"""
    out = StreamOutcome()
    processed = 0
    for line in lines:
        processed += 1

        decoded = _decode_line(line)
        if isinstance(decoded, _JsonDecode):
            out.json_decode_errors += 1
            _note_error_text(out, "\n\n[json decode error] " + decoded.line)
            continue
        if isinstance(decoded, _Malformed):
            _note_error_text(
                out,
                f"\n\n[unexpected error] {decoded.error}. Line: {decoded.line!r}",
            )
            out.had_error = True
            out.error_kind = ErrorKind.UNEXPECTED_EXCEPTION
            break

        try:
            _fold_ok_event(out, decoded.data, collect_messages=collect_messages)
        except Exception as error:  # noqa: BLE001 — 畸形事件 → unexpected_exception
            _note_error_text(out, f"\n\n[unexpected error] {error}. Line: {line!r}")
            out.had_error = True
            out.error_kind = ErrorKind.UNEXPECTED_EXCEPTION
            break

    # 诊断窗口对「已处理行」开一次（畸形 break 后的未处理行不入窗，语义不变）。
    out.last_lines = tail_window(lines[:processed])
    return out


def _finalize_stream_outcome(
    stream: StreamOutcome,
    *,
    exit_code: Optional[int],
) -> StreamOutcome:
    """在归约结果上叠加 success 判定用的错误优先级（仅 completed 终态）。"""
    # 123 证据只对「最终未取得会话标识」的行流定罪：子工具（如 rg）向
    # 合流 stdout 吐的 os error 123 纯文本命中同一特征，而已建会话的运行
    # 显然不是工作目录非法（生产事故：成功审查被盖成 invalid_path 失败，
    # wire 丢会话标识与结论）。证据由 _note_error_text 在入口抽取，
    # 此处只读字段。
    if stream.session_id is None and stream.saw_invalid_path_text:
        stream.error_kind = ErrorKind.INVALID_PATH
        stream.had_error = True

    if stream.session_id is None:
        if not stream.error_kind:
            stream.error_kind = ErrorKind.PROTOCOL_MISSING_SESSION
        stream.error_message = "未能获取 SESSION_ID。\n\n" + stream.error_message
        stream.had_error = True

    if not stream.text:
        if not stream.error_kind:
            stream.error_kind = ErrorKind.EMPTY_RESULT
        stream.error_message = (
            "未能获取 Codex 响应内容。可尝试设置 return_all_messages=True 获取详细信息。\n\n"
            + stream.error_message
        )
        stream.had_error = True

    if exit_code is not None and exit_code != 0 and not stream.had_error:
        stream.had_error = True
        if not stream.error_kind:
            stream.error_kind = ErrorKind.SUBPROCESS_ERROR
        stream.error_message = (
            f"进程退出码非零：{exit_code}\n\n" + stream.error_message
        )

    return stream


def reduce_codex_stream(
    outcome: ProcessOutcome,
    *,
    collect_messages: bool = False,
) -> StreamOutcome:
    """把一次行流结果折叠成结构化结局（终态感知单入口）。

    超时终局：正文与会话标识保持空（行为冻结），种类由终态决定
    （idle_timeout / timeout），仅诊断信息取自折叠。
    完成终局：折叠后叠加 finalize 优先级，并携带 exit_code /
    raw_output_lines。
    """
    folded = _fold_lines(outcome.lines, collect_messages=collect_messages)

    if outcome.terminal in ("timeout", "idle_timeout"):
        kind = (
            ErrorKind.IDLE_TIMEOUT
            if outcome.terminal == "idle_timeout"
            else ErrorKind.TIMEOUT
        )
        return StreamOutcome(
            had_error=True,
            error_kind=kind,
            error_message=outcome.error_message or "timeout",
            exit_code=None,
            raw_output_lines=outcome.raw_output_lines or len(outcome.lines),
            json_decode_errors=folded.json_decode_errors,
            last_lines=folded.last_lines,
            all_messages=folded.all_messages,
        )

    folded = _finalize_stream_outcome(folded, exit_code=outcome.exit_code)
    folded.exit_code = outcome.exit_code
    folded.raw_output_lines = outcome.raw_output_lines
    return folded
