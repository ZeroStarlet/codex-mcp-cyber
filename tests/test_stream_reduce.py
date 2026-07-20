"""行流归约（终态感知单入口）/ 脱敏 / 谓词。"""

from __future__ import annotations

import json

from codex_mcp_cyber.errors import ErrorKind, build_error_detail
from codex_mcp_cyber.redact import filter_last_lines, redact_tool_result_event
from codex_mcp_cyber.stream import (
    ProcessOutcome,
    is_turn_completed_line,
    reduce_codex_stream,
)


def _po(
    lines: list[str],
    *,
    exit_code: int | None = 0,
    terminal: str = "completed",
    error_message: str = "",
) -> ProcessOutcome:
    return ProcessOutcome(
        lines=list(lines),
        exit_code=exit_code,
        raw_output_lines=sum(1 for x in lines if x),
        terminal=terminal,  # type: ignore[arg-type]
        error_message=error_message,
    )


def _ok_lines(text: str = "OK", thread_id: str = "sess-1") -> list[str]:
    return [
        json.dumps({"type": "thread.started", "thread_id": thread_id}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": text},
            }
        ),
        json.dumps({"type": "turn.completed", "usage": {}}),
    ]


def _fat_tool_result_event(secret: str = "SECRET-BLOB-DO-NOT-LEAK") -> dict:
    return {
        "type": "item.completed",
        "item": {
            "id": "item_tool",
            "type": "tool_result",
            "content": secret * 20,
        },
    }

def test_reduce_prioritizes_invalid_path_over_protocol() -> None:
    stream = reduce_codex_stream(
        _po(
            [
                "Error: The filename, directory name, or volume label syntax is incorrect. (os error 123)"
            ],
            exit_code=1,
        )
    )
    assert stream.error_kind == ErrorKind.INVALID_PATH
    assert stream.had_error is True

def test_reduce_completed_success_carries_exit_and_raw() -> None:
    stream = reduce_codex_stream(_po(_ok_lines("OK")))
    assert stream.had_error is False
    assert stream.text == "OK"
    assert stream.session_id == "sess-1"
    assert stream.exit_code == 0
    assert stream.raw_output_lines == 3

def test_reduce_timeout_partial_looking_successful_stays_timeout() -> None:
    """超时 partial 即使像完整成功 JSONL，也不得 finalize 成 success。

    该不变量属于 stream 实现（终态感知单入口），在接口上直测。
    """
    lines = _ok_lines("SHOULD-NOT-WIN", thread_id="sess-partial")
    stream = reduce_codex_stream(
        _po(lines, exit_code=None, terminal="idle_timeout", error_message="idle timeout")
    )
    assert stream.had_error is True
    assert stream.error_kind == ErrorKind.IDLE_TIMEOUT
    assert stream.error_kind != ErrorKind.PROTOCOL_MISSING_SESSION
    assert stream.text == ""
    assert stream.session_id is None
    assert stream.exit_code is None
    assert stream.error_message == "idle timeout"
    assert stream.last_lines == lines

def test_reduce_total_timeout_maps_to_timeout_kind() -> None:
    stream = reduce_codex_stream(
        _po(["partial"], exit_code=None, terminal="timeout", error_message="")
    )
    assert stream.error_kind == ErrorKind.TIMEOUT
    # error_message 为空时落默认 "timeout"
    assert stream.error_message == "timeout"
    assert stream.raw_output_lines == 1

def test_reduce_timeout_still_collects_diagnostics() -> None:
    """超时终局仍折叠诊断信息：last_lines / json_decode_errors / all_messages（脱敏）。"""
    secret = "TIMEOUT-SECRET"
    lines = [
        json.dumps(_fat_tool_result_event(secret), ensure_ascii=False),
        "not-json-line",
    ]
    stream = reduce_codex_stream(
        _po(lines, exit_code=None, terminal="idle_timeout", error_message="idle"),
        collect_messages=True,
    )
    assert stream.error_kind == ErrorKind.IDLE_TIMEOUT
    assert stream.json_decode_errors == 1
    assert stream.last_lines == lines
    blob = json.dumps(stream.all_messages, ensure_ascii=False)
    assert secret not in blob

def test_redact_tool_result_event_truncates_only_tool_result() -> None:
    fat = _fat_tool_result_event()
    out = redact_tool_result_event(fat)
    assert out is not fat
    assert out["item"]["content"] == "[truncated]"
    assert fat["item"]["content"] != "[truncated]"  # 不改原对象

    plain = {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "hi", "meta": {"nested": ["a"]}},
    }
    plain_out = redact_tool_result_event(plain)
    assert plain_out["item"]["text"] == "hi"
    assert plain_out is not plain
    # 深拷贝：改返回值不得污染原嵌套结构
    plain_out["item"]["meta"]["nested"].append("mutated")
    plain_out["item"]["text"] = "changed"
    assert plain["item"]["text"] == "hi"
    assert plain["item"]["meta"]["nested"] == ["a"]

def test_filter_last_lines_redacts_tool_result_content() -> None:
    secret = "TOP-SECRET-PAYLOAD"
    line = json.dumps(_fat_tool_result_event(secret), ensure_ascii=False)
    filtered = filter_last_lines([line], max_lines=50)
    assert len(filtered) == 1
    parsed = json.loads(filtered[0])
    assert parsed["item"]["type"] == "tool_result"
    assert parsed["item"]["content"] == "[truncated]"
    assert secret not in filtered[0]

def test_filter_last_lines_preserves_non_tool_result_line_bytes() -> None:
    """非 tool_result JSON 行必须原样保留（空白/键序/separators 不 re-dump）。"""
    weird = '  { "type" : "item.completed", "item" : { "type" : "agent_message", "text" : "x" } }  '
    plain_text = "not-json-line"
    filtered = filter_last_lines([weird, plain_text], max_lines=50)
    assert filtered == [weird, plain_text]
    assert filtered[0] is weird or filtered[0] == weird

def test_build_error_detail_last_lines_use_redaction() -> None:
    secret = "ERR-SECRET"
    line = json.dumps(_fat_tool_result_event(secret), ensure_ascii=False)
    detail = build_error_detail(message="fail", last_lines=[line])
    joined = json.dumps(detail, ensure_ascii=False)
    assert secret not in joined
    assert detail["last_lines"][0]
    assert "[truncated]" in detail["last_lines"][0]

def test_is_turn_completed_line_predicate() -> None:
    assert is_turn_completed_line(json.dumps({"type": "turn.completed"})) is True
    assert is_turn_completed_line(json.dumps({"type": "turn.started"})) is False
    assert is_turn_completed_line("not-json") is False
    assert is_turn_completed_line(json.dumps(["turn.completed"])) is False
    assert is_turn_completed_line(json.dumps({"type": None})) is False
