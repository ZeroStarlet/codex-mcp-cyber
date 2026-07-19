"""审核执行 seam：ScriptedLines → ReviewResult / to_wire。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_mcp_cyber.errors import (
    CommandNotFoundError,
    ErrorKind,
    normalize_workdir,
)
from codex_mcp_cyber.process import ProcessOutcome, ScriptedLinesRunner, SequenceRunner
from codex_mcp_cyber.review import ReviewRequest, run_review, to_wire
from codex_mcp_cyber.tools.codex import codex_tool


class _RaiseNotFoundRunner:
    """测试 adapter：始终抛 CommandNotFoundError。"""

    def run(
        self,
        cmd: list[str],
        *,
        prompt: str,
        timeout: int,
        max_duration: int,
    ) -> ProcessOutcome:
        del cmd, prompt, timeout, max_duration
        raise CommandNotFoundError("未找到 codex CLI（测试）")

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

class _CountingNotFoundRunner:
    def __init__(self) -> None:
        self.calls = 0

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
        raise CommandNotFoundError("未找到 codex CLI（测试）")

def _fat_tool_result_event(secret: str = "SECRET-BLOB-DO-NOT-LEAK") -> dict:
    return {
        "type": "item.completed",
        "item": {
            "id": "item_tool",
            "type": "tool_result",
            "content": secret * 20,
        },
    }

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
    assert result.success is False
    assert result.error_kind == ErrorKind.INVALID_PATH
    assert result.error_kind != ErrorKind.PROTOCOL_MISSING_SESSION
    detail = result.error_detail or {}
    assert any("os error 123" in str(x) for x in detail.get("last_lines", []))

@pytest.mark.asyncio
async def test_happy_jsonl_returns_session_and_text(tmp_path: Path) -> None:
    runner = ScriptedLinesRunner(lines=_ok_lines("OK"), exit_code=0)
    result = await run_review(
        ReviewRequest(prompt="hi", cd=tmp_path, max_retries=0),
        runner=runner,
    )
    assert result.success is True
    assert result.session_id == "sess-1"
    assert result.text == "OK"
    assert isinstance(result.duration_ms, int)

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
    assert result.success is True
    assert result.text == "PONG"

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
    assert result.success is False
    assert result.error_kind == ErrorKind.INVALID_PATH
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
    assert result.success is False
    assert result.error_kind == ErrorKind.AUTH_REQUIRED
    # only one attempt despite max_retries=3
    assert runner.calls == 1

@pytest.mark.asyncio
async def test_wire_exact_keys_on_ordinary_failure(tmp_path: Path) -> None:
    runner = ScriptedLinesRunner(lines=["not-json"], exit_code=1)
    outcome = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=0),
        runner=runner,
    )
    wire = to_wire(outcome)
    assert set(wire.keys()) == {
        "success",
        "tool",
        "error",
        "error_kind",
        "error_detail",
        "duration",
    }

@pytest.mark.asyncio
async def test_wire_exact_keys_on_success(tmp_path: Path) -> None:
    outcome = await run_review(
        ReviewRequest(prompt="hi", cd=tmp_path, max_retries=0),
        runner=ScriptedLinesRunner(lines=_ok_lines("OK"), exit_code=0),
    )
    wire = to_wire(outcome)
    assert set(wire.keys()) == {
        "success",
        "tool",
        "SESSION_ID",
        "result",
        "duration",
    }
    assert wire["SESSION_ID"] == "sess-1"
    assert wire["result"] == "OK"
    assert "session_id" not in wire
    assert "text" not in wire

@pytest.mark.asyncio
async def test_wire_exact_keys_on_command_not_found(tmp_path: Path) -> None:
    """command_not_found 与其它失败路径统一含 duration（ADR-0002 规范化）。"""
    outcome = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=0),
        runner=_RaiseNotFoundRunner(),  # type: ignore[arg-type]
    )
    assert outcome.success is False
    assert outcome.error_kind == ErrorKind.COMMAND_NOT_FOUND
    wire = to_wire(outcome)
    assert set(wire.keys()) == {
        "success",
        "tool",
        "error",
        "error_kind",
        "error_detail",
        "duration",
    }
    assert wire["error_kind"] == ErrorKind.COMMAND_NOT_FOUND
    assert "duration" in wire

@pytest.mark.asyncio
async def test_to_wire_success_maps_domain_fields(tmp_path: Path) -> None:
    runner = ScriptedLinesRunner(lines=_ok_lines("OK"), exit_code=0)
    outcome = await run_review(
        ReviewRequest(prompt="hi", cd=tmp_path, max_retries=0),
        runner=runner,
    )
    wire = to_wire(outcome)
    assert wire["success"] is True
    assert wire["tool"] == "codex"
    assert wire["SESSION_ID"] == "sess-1"
    assert wire["result"] == "OK"
    assert "duration" in wire
    assert "session_id" not in wire
    assert "text" not in wire

@pytest.mark.asyncio
async def test_to_wire_auth_adds_human_copy(tmp_path: Path) -> None:
    auth_line = json.dumps(
        {"type": "error", "message": "401 unauthorized — authentication failed"}
    )
    outcome = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=0),
        runner=ScriptedLinesRunner(lines=[auth_line], exit_code=1),
    )
    wire = to_wire(outcome)
    assert wire["error_kind"] == ErrorKind.AUTH_REQUIRED
    assert "codex login" in wire["error"]
    # 领域结局不含展示长文案
    assert "codex login" not in (outcome.error_message or "")

@pytest.mark.asyncio
async def test_missing_workdir_to_wire_has_invalid_path_guidance() -> None:
    outcome = await run_review(
        ReviewRequest(
            prompt="x",
            cd='"C:/this/path/does/not/exist/codex-mcp-cyber-test"',
            max_retries=0,
        ),
        runner=ScriptedLinesRunner(lines=[], exit_code=0),
    )
    assert outcome.success is False
    assert outcome.error_kind == ErrorKind.INVALID_PATH
    assert "正确写法" not in (outcome.error_message or "")
    wire = to_wire(outcome)
    assert wire["error_kind"] == ErrorKind.INVALID_PATH
    assert "os error 123" in wire["error"] or "字面引号" in wire["error"] or "非法" in wire["error"]
    assert "常见原因" in wire["error"] or "字面引号" in wire["error"]
@pytest.mark.asyncio
async def test_metrics_and_all_messages_optional_on_wire(tmp_path: Path) -> None:
    runner = ScriptedLinesRunner(lines=_ok_lines("OK"), exit_code=0)
    off = await run_review(
        ReviewRequest(prompt="hi", cd=tmp_path, max_retries=0),
        runner=runner,
    )
    wire_off = to_wire(off)
    assert "metrics" not in wire_off
    assert "all_messages" not in wire_off

    on = await run_review(
        ReviewRequest(
            prompt="hi",
            cd=tmp_path,
            max_retries=0,
            return_metrics=True,
            return_all_messages=True,
        ),
        runner=ScriptedLinesRunner(lines=_ok_lines("OK"), exit_code=0),
    )
    assert on.metrics is not None
    assert on.all_messages is not None
    wire_on = to_wire(on)
    assert "metrics" in wire_on
    assert "all_messages" in wire_on
    assert isinstance(wire_on["metrics"], dict)
    assert isinstance(wire_on["all_messages"], list)

@pytest.mark.asyncio
async def test_codex_tool_shell_returns_wire_dict_not_review_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_run_review(req: ReviewRequest, runner=None):  # noqa: ANN001
        del runner
        return await run_review(
            req,
            runner=ScriptedLinesRunner(lines=_ok_lines("SHELL"), exit_code=0),
        )

    monkeypatch.setattr(
        "codex_mcp_cyber.tools.codex.run_review",
        _fake_run_review,
    )
    wire = await codex_tool(PROMPT="hi", cd=tmp_path, max_retries=0)
    assert isinstance(wire, dict)
    assert wire["success"] is True
    assert wire["result"] == "SHELL"
    assert "SESSION_ID" in wire

def test_server_registers_codex_tool_as_mcp_handler() -> None:
    """server 只注册 tools.codex_tool，无第二份 15 参壳（ADR-0004）。"""
    from codex_mcp_cyber.server import mcp
    from codex_mcp_cyber.tools.codex import codex_tool as shell

    tools = mcp._tool_manager.list_tools()
    by_name = {t.name: t for t in tools}
    assert "codex" in by_name
    registered = by_name["codex"]
    assert registered.name == "codex"
    assert registered.fn is shell
    assert "read-only" in (registered.description or "")
    assert "审核" in (registered.description or "")

@pytest.mark.asyncio
async def test_malformed_json_array_event_is_domain_error_not_throw(tmp_path: Path) -> None:
    """合法 JSON 但非 object 不得抛出 AttributeError，须返回失败结局。"""
    runner = ScriptedLinesRunner(lines=["[]"], exit_code=1)
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=0),
        runner=runner,
    )
    assert result.success is False
    assert result.error_kind == ErrorKind.UNEXPECTED_EXCEPTION
    assert result.error_detail is not None

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
    assert result.success is False
    assert result.error_kind == ErrorKind.UNEXPECTED_EXCEPTION
    assert result.text != "123"

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
        assert result.success is False, bad_item
        assert result.error_kind == ErrorKind.UNEXPECTED_EXCEPTION, bad_item
        assert result.text != "OK", bad_item

@pytest.mark.asyncio
async def test_timeout_after_failed_attempt_does_not_leak_prior_exit(
    tmp_path: Path,
) -> None:
    """重试后超时不得沿用上一轮 exit_code / json_decode_errors。"""
    runner = SequenceRunner(
        steps=[
            ProcessOutcome(lines=["not-json-line"], exit_code=7, raw_output_lines=1),
            ProcessOutcome(
                lines=["partial-only"],
                exit_code=None,
                raw_output_lines=1,
                terminal="idle_timeout",
                error_message="idle timeout",
            ),
        ]
    )
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=1, return_metrics=True),
        runner=runner,
    )
    assert result.success is False
    assert result.error_kind == ErrorKind.IDLE_TIMEOUT
    detail = result.error_detail or {}
    # 不得沿用第一轮 exit_code=7
    assert detail.get("exit_code") is None
    # last_lines 仅来自超时这一轮的 partial
    assert detail.get("last_lines") == ["partial-only"]
    assert runner.calls == 2
    assert result.metrics is not None
    assert result.metrics.get("exit_code") is None
    assert result.metrics.get("error_kind") == ErrorKind.IDLE_TIMEOUT

@pytest.mark.asyncio
async def test_timeout_partial_looking_successful_stays_timeout(
    tmp_path: Path,
) -> None:
    """超时 partial 即使像完整成功 JSONL，也不得 finalize 成 success。"""
    partial = _ok_lines("SHOULD-NOT-WIN", thread_id="sess-partial")
    runner = SequenceRunner(
        steps=[
            ProcessOutcome(
                lines=partial,
                exit_code=None,
                raw_output_lines=len(partial),
                terminal="idle_timeout",
                error_message="idle timeout",
            ),
        ]
    )
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=0),
        runner=runner,
    )
    assert result.success is False
    assert result.error_kind == ErrorKind.IDLE_TIMEOUT
    assert result.text == ""
    assert result.session_id is None
    # 未 finalize：不应因 partial 缺 session 被盖成 protocol_missing_session
    assert result.error_kind != ErrorKind.PROTOCOL_MISSING_SESSION
    assert runner.calls == 1

@pytest.mark.asyncio
async def test_retryable_failure_then_success_records_metrics(
    tmp_path: Path,
) -> None:
    runner = SequenceRunner(
        steps=[
            ProcessOutcome(lines=["not-json-line"], exit_code=1, raw_output_lines=1),
            ProcessOutcome(lines=_ok_lines("RECOVERED"), exit_code=0, raw_output_lines=4),
        ]
    )
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=1, return_metrics=True),
        runner=runner,
    )
    assert result.success is True
    assert result.text == "RECOVERED"
    assert result.session_id == "sess-1"
    assert runner.calls == 2
    assert result.metrics is not None
    assert result.metrics["retries"] == 1
    assert result.metrics["exit_code"] == 0
    assert result.metrics["success"] is True
    assert result.metrics["result_chars"] == len("RECOVERED")
    assert result.metrics.get("ts_end") is not None

@pytest.mark.asyncio
async def test_command_not_found_does_not_retry_when_max_retries_positive(
    tmp_path: Path,
) -> None:
    runner = _RaiseNotFoundRunner()
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=3),
        runner=runner,  # type: ignore[arg-type]
    )
    assert result.success is False
    assert result.error_kind == ErrorKind.COMMAND_NOT_FOUND
    # NotFound 不进 AttemptOutcome 重试；runner 语义上每次 raise 即一次尝试
    # SequenceRunner 才有 calls；此处用可计数的包装验证只调用一次
    # _RaiseNotFoundRunner 无 calls —— 用 SequenceRunner 风格计数包装

@pytest.mark.asyncio
async def test_command_not_found_runner_called_once_with_retries(
    tmp_path: Path,
) -> None:
    runner = _CountingNotFoundRunner()
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=3),
        runner=runner,  # type: ignore[arg-type]
    )
    assert result.error_kind == ErrorKind.COMMAND_NOT_FOUND
    assert runner.calls == 1

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
    assert result.error_kind == ErrorKind.INVALID_PATH
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
    assert result.success is True

@pytest.mark.asyncio
async def test_all_messages_path_redacts_tool_result(tmp_path: Path) -> None:
    secret = "ALLMSG-SECRET"
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "sess-r"}),
        json.dumps(_fat_tool_result_event(secret), ensure_ascii=False),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "OK"},
            }
        ),
        json.dumps({"type": "turn.completed", "usage": {}}),
    ]
    result = await run_review(
        ReviewRequest(
            prompt="x",
            cd=tmp_path,
            max_retries=0,
            return_all_messages=True,
        ),
        runner=ScriptedLinesRunner(lines=lines, exit_code=0),
    )
    assert result.success is True
    assert result.all_messages is not None
    blob = json.dumps(result.all_messages, ensure_ascii=False)
    assert secret not in blob
    assert any(
        isinstance(m.get("item"), dict)
        and m["item"].get("type") == "tool_result"
        and m["item"].get("content") == "[truncated]"
        for m in result.all_messages
    )

@pytest.mark.asyncio
async def test_failure_error_detail_redacts_tool_result_last_lines(
    tmp_path: Path,
) -> None:
    secret = "FAIL-SECRET"
    # 无 thread / agent → finalize 失败；last_lines 仍含 tool_result 行
    lines = [
        json.dumps(_fat_tool_result_event(secret), ensure_ascii=False),
        "not-json-either",
    ]
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=0),
        runner=ScriptedLinesRunner(lines=lines, exit_code=1),
    )
    assert result.success is False
    detail = result.error_detail or {}
    last = detail.get("last_lines") or []
    joined = "\n".join(str(x) for x in last)
    assert secret not in joined
    assert any("[truncated]" in str(x) for x in last)



@pytest.mark.asyncio
async def test_retry_sleep_is_injected(tmp_path: Path) -> None:
    """backoff sleep 为 internal seam：注入即时 sleep 并记录 delay。"""
    delays: list[float] = []

    async def fake_sleep(sec: float) -> None:
        delays.append(sec)

    runner = SequenceRunner(
        steps=[
            ProcessOutcome(lines=["not-json-line"], exit_code=1, raw_output_lines=1),
            ProcessOutcome(lines=_ok_lines("RECOVERED"), exit_code=0, raw_output_lines=4),
        ]
    )
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=1),
        runner=runner,
        sleep=fake_sleep,
    )
    assert result.success is True
    assert delays == [0.5]


@pytest.mark.asyncio
async def test_failure_metrics_keep_partial_result_chars(tmp_path: Path) -> None:
    """失败路径 metrics 仍统计已收到的 agent text（旧 MetricsCollector.finish 语义）。

    ReviewResult.text 失败终局保持空串（行为冻结）；partial 只进 metrics。
    """
    import json as _json

    lines = [
        _json.dumps({"type": "thread.started", "thread_id": "sess-p"}),
        _json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "PARTIAL"},
            }
        ),
        _json.dumps(
            {
                "type": "error",
                "message": "upstream blew up",
            }
        ),
    ]
    result = await run_review(
        ReviewRequest(prompt="x", cd=tmp_path, max_retries=0, return_metrics=True),
        runner=ScriptedLinesRunner(lines=lines, exit_code=1),
    )
    assert result.success is False
    assert result.text == ""  # 领域结局失败不暴露 partial text
    assert result.metrics is not None
    assert result.metrics["result_chars"] == len("PARTIAL")
    assert result.metrics["result_lines"] == 1
    # ts_end 与 duration_ms 同一终止时刻
    from datetime import datetime

    ts_start = datetime.fromisoformat(result.metrics["ts_start"])
    ts_end = datetime.fromisoformat(result.metrics["ts_end"])
    derived_ms = int((ts_end - ts_start).total_seconds() * 1000)
    assert derived_ms == result.metrics["duration_ms"]
    assert derived_ms == result.duration_ms
