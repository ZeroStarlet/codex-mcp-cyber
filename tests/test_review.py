"""审核执行 seam 测试：ScriptedLines 行流 → wire 结果。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_mcp_cyber.errors import CommandTimeoutError, ErrorKind, normalize_workdir
from codex_mcp_cyber.process import ProcessOutcome, ScriptedLinesRunner, SequenceRunner
from codex_mcp_cyber.review import ReviewRequest, run_review
from codex_mcp_cyber.stream import finalize_stream_outcome, reduce_codex_stream


def _ok_lines(text: str = "PONG", thread_id: str = "sess-1") -> list[str]:
    return [
        json.dumps({"type": "thread.started", "thread_id": thread_id}),
        json.dumps({"type": "turn.started"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": text},
            }
        ),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1,
                    "cached_input_tokens": 0,
                    "output_tokens": 1,
                    "reasoning_output_tokens": 0,
                },
            }
        ),
    ]


@pytest.mark.asyncio
async def test_os_error_123_classifies_as_invalid_path(tmp_path: Path) -> None:
    """生产事故回归：纯文本 os error 123 不得变成 protocol_missing_session。"""
    runner = ScriptedLinesRunner(
        lines=["Error: 文件名、目录名或卷标语法不正确。 (os error 123)"],
        exit_code=1,
    )
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=0),
        runner=runner,
    )
    assert result["success"] is False
    assert result["error_kind"] == ErrorKind.INVALID_PATH
    assert result["error_kind"] != ErrorKind.PROTOCOL_MISSING_SESSION
    detail = result["error_detail"]
    assert any("os error 123" in str(x) for x in detail.get("last_lines", []))


@pytest.mark.asyncio
async def test_happy_jsonl_returns_session_and_text(tmp_path: Path) -> None:
    runner = ScriptedLinesRunner(lines=_ok_lines("OK"), exit_code=0)
    result = await run_review(
        ReviewRequest(prompt="hi", cd=tmp_path, max_retries=0),
        runner=runner,
    )
    assert result["success"] is True
    assert result["SESSION_ID"] == "sess-1"
    assert result["result"] == "OK"
    assert result["tool"] == "codex"
    assert "duration" in result


@pytest.mark.asyncio
async def test_quoted_workdir_is_normalized(tmp_path: Path) -> None:
    quoted = f'"{tmp_path.as_posix()}"'
    assert normalize_workdir(quoted) == tmp_path.resolve() or normalize_workdir(
        quoted
    ).exists()
    runner = ScriptedLinesRunner(lines=_ok_lines("PONG"), exit_code=0)
    result = await run_review(
        ReviewRequest(prompt="hi", cd=quoted, max_retries=0),
        runner=runner,
    )
    assert result["success"] is True
    assert result["result"] == "PONG"


@pytest.mark.asyncio
async def test_missing_workdir_is_invalid_path_without_runner() -> None:
    runner = ScriptedLinesRunner(lines=[], exit_code=0)
    result = await run_review(
        ReviewRequest(
            prompt="x",
            cd='"C:/this/path/does/not/exist/codex-mcp-cyber-test"',
            max_retries=0,
        ),
        runner=runner,
    )
    assert result["success"] is False
    assert result["error_kind"] == ErrorKind.INVALID_PATH
    assert runner.calls == 0


@pytest.mark.asyncio
async def test_auth_error_not_retryable(tmp_path: Path) -> None:
    auth_line = json.dumps(
        {
            "type": "error",
            "message": "401 unauthorized — authentication failed",
        }
    )
    runner = ScriptedLinesRunner(lines=[auth_line], exit_code=1)
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=3),
        runner=runner,
    )
    assert result["success"] is False
    assert result["error_kind"] == ErrorKind.AUTH_REQUIRED
    # only one attempt despite max_retries=3
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_wire_keys_on_failure(tmp_path: Path) -> None:
    runner = ScriptedLinesRunner(lines=["not-json"], exit_code=1)
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=0),
        runner=runner,
    )
    for key in ("success", "tool", "error", "error_kind", "error_detail", "duration"):
        assert key in result


def test_reduce_prioritizes_invalid_path_over_protocol() -> None:
    stream = reduce_codex_stream(
        ["Error: The filename, directory name, or volume label syntax is incorrect. (os error 123)"]
    )
    stream = finalize_stream_outcome(stream, exit_code=1)
    assert stream.error_kind == ErrorKind.INVALID_PATH
    assert stream.had_error is True


@pytest.mark.asyncio
async def test_malformed_json_array_event_is_wire_error_not_throw(tmp_path: Path) -> None:
    """合法 JSON 但非 object 不得抛出 AttributeError，须返回 wire dict。"""
    runner = ScriptedLinesRunner(lines=["[]"], exit_code=1)
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=0),
        runner=runner,
    )
    assert result["success"] is False
    assert result["error_kind"] == ErrorKind.UNEXPECTED_EXCEPTION
    assert "error" in result and "error_detail" in result


@pytest.mark.asyncio
async def test_non_string_agent_text_is_unexpected_not_success(tmp_path: Path) -> None:
    """agent_message.text 为非 str 时不得 str() 容错成 success。"""
    import json as _json

    lines = [
        _json.dumps({"type": "thread.started", "thread_id": "t1"}),
        _json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": 123},
            }
        ),
        _json.dumps({"type": "turn.completed", "usage": {}}),
    ]
    runner = ScriptedLinesRunner(lines=lines, exit_code=0)
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=0),
        runner=runner,
    )
    assert result["success"] is False
    assert result["error_kind"] == ErrorKind.UNEXPECTED_EXCEPTION
    assert result.get("result") != "123"


@pytest.mark.asyncio
async def test_null_item_and_null_text_are_unexpected(tmp_path: Path) -> None:
    import json as _json

    for bad_item in (
        {"type": "item.completed", "item": None},
        {"type": "item.completed", "item": {"type": "agent_message", "text": None}},
        {"type": "item.completed", "item": []},
    ):
        lines = [
            _json.dumps({"type": "thread.started", "thread_id": "t1"}),
            _json.dumps(bad_item),
            _json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "OK"},
                }
            ),
            _json.dumps({"type": "turn.completed", "usage": {}}),
        ]
        result = await run_review(
            ReviewRequest(prompt="x", cd=tmp_path, max_retries=0),
            runner=ScriptedLinesRunner(lines=lines, exit_code=0),
        )
        assert result["success"] is False, bad_item
        assert result["error_kind"] == ErrorKind.UNEXPECTED_EXCEPTION, bad_item
        assert result.get("result") != "OK", bad_item


@pytest.mark.asyncio
async def test_timeout_after_failed_attempt_does_not_leak_prior_exit(
    tmp_path: Path,
) -> None:
    """重试后超时不得沿用上一轮 exit_code / json_decode_errors。"""
    runner = SequenceRunner(
        steps=[
            ProcessOutcome(lines=["not-json-line"], exit_code=7, raw_output_lines=1),
            CommandTimeoutError(
                "idle timeout",
                is_idle=True,
                partial_lines=["partial-only"],
                raw_output_lines=1,
            ),
        ]
    )
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=1),
        runner=runner,
    )
    assert result["success"] is False
    assert result["error_kind"] == ErrorKind.IDLE_TIMEOUT
    detail = result["error_detail"]
    # 不得沿用第一轮 exit_code=7
    assert detail.get("exit_code") is None
    # last_lines 仅来自超时这一轮的 partial
    assert detail.get("last_lines") == ["partial-only"]
    assert runner.calls == 2


@pytest.mark.asyncio
async def test_invalid_path_stream_does_not_retry(tmp_path: Path) -> None:
    runner = ScriptedLinesRunner(
        lines=["Error: 文件名、目录名或卷标语法不正确。 (os error 123)"],
        exit_code=1,
    )
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=3),
        runner=runner,
    )
    assert result["error_kind"] == ErrorKind.INVALID_PATH
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_quoted_workdir_normalizes_to_tmp(tmp_path: Path) -> None:
    quoted = f'"{tmp_path}"'
    normalized = normalize_workdir(quoted)
    assert normalized == Path(str(tmp_path).strip('"')) or normalized.exists()
    # stronger: string form without quotes
    assert '"' not in str(normalized)
    runner = ScriptedLinesRunner(lines=_ok_lines("PONG"), exit_code=0)
    result = await run_review(
        ReviewRequest(prompt="hi", cd=quoted, max_retries=0),
        runner=runner,
    )
    assert result["success"] is True
