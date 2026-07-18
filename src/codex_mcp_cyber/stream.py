"""行流归约：JSONL / 纯文本 → StreamOutcome。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from codex_mcp_cyber.errors import (
    ErrorKind,
    is_auth_error,
    looks_like_invalid_path_error,
    redact_tool_result_event,
)


@dataclass
class StreamOutcome:
    agent_messages: str = ""
    thread_id: Optional[str] = None
    had_error: bool = False
    err_message: str = ""
    error_kind: Optional[str] = None
    json_decode_errors: int = 0
    last_lines: list[str] = field(default_factory=list)
    all_messages: list[dict[str, Any]] = field(default_factory=list)


def reduce_codex_stream(
    lines: list[str],
    *,
    collect_messages: bool = False,
) -> StreamOutcome:
    """把一行流行列折叠成结构化结局。"""
    out = StreamOutcome()
    for line in lines:
        out.last_lines.append(line)
        if len(out.last_lines) > 50:
            out.last_lines.pop(0)

        try:
            parsed = json.loads(line.strip())
        except json.JSONDecodeError:
            out.json_decode_errors += 1
            out.err_message += "\n\n[json decode error] " + line
            if looks_like_invalid_path_error(line):
                out.had_error = True
                out.error_kind = ErrorKind.INVALID_PATH
            continue
        except Exception as error:  # noqa: BLE001 — 与历史行为一致
            out.err_message += f"\n\n[unexpected error] {error}. Line: {line!r}"
            out.had_error = True
            out.error_kind = ErrorKind.UNEXPECTED_EXCEPTION
            break

        # 合法 JSON 但形状异常：必须吞掉并映射为 wire 错误，不得抛出到调用方
        try:
            if not isinstance(parsed, dict):
                raise TypeError(
                    f"expected JSON object event, got {type(parsed).__name__}"
                )
            line_dict = parsed

            if collect_messages:
                out.all_messages.append(redact_tool_result_event(line_dict))

            item = line_dict.get("item", {})
            if item is None or not isinstance(item, dict):
                raise TypeError(
                    f"expected item object, got {type(item).__name__}"
                )

            item_type = item.get("type", "")
            if item_type is None or not isinstance(item_type, str):
                raise TypeError(
                    f"expected item.type str, got {type(item_type).__name__}"
                )

            if item_type == "agent_message":
                text = item.get("text", "")
                if text is None or not isinstance(text, str):
                    raise TypeError(
                        f"expected agent_message.text str, got {type(text).__name__}"
                    )
                out.agent_messages += text

            if line_dict.get("thread_id") is not None:
                out.thread_id = line_dict.get("thread_id")

            event_type = line_dict.get("type", "")
            if event_type is None:
                event_type = ""
            if not isinstance(event_type, str):
                raise TypeError(
                    f"expected event type str, got {type(event_type).__name__}"
                )

            if "fail" in event_type:
                out.had_error = True
                err_obj = line_dict.get("error", {})
                if err_obj is None or not isinstance(err_obj, dict):
                    raise TypeError(
                        f"expected error object, got {type(err_obj).__name__}"
                    )
                fail_msg = err_obj.get("message", "")
                if fail_msg is None:
                    fail_msg = ""
                if not isinstance(fail_msg, str):
                    raise TypeError(
                        f"expected error.message str, got {type(fail_msg).__name__}"
                    )
                out.err_message += "\n\n[codex error] " + fail_msg
                if is_auth_error(fail_msg):
                    out.error_kind = ErrorKind.AUTH_REQUIRED
                elif out.error_kind != ErrorKind.AUTH_REQUIRED:
                    out.error_kind = ErrorKind.UPSTREAM_ERROR

            if "error" in event_type:
                error_msg = line_dict.get("message", "")
                if error_msg is None:
                    error_msg = ""
                if not isinstance(error_msg, str):
                    raise TypeError(
                        f"expected error message str, got {type(error_msg).__name__}"
                    )
                is_reconnecting = bool(
                    re.match(r"^Reconnecting\.\.\.\s+\d+/\d+$", error_msg)
                )
                if not is_reconnecting:
                    out.had_error = True
                    out.err_message += "\n\n[codex error] " + error_msg
                    if is_auth_error(error_msg):
                        out.error_kind = ErrorKind.AUTH_REQUIRED
                    elif out.error_kind != ErrorKind.AUTH_REQUIRED:
                        out.error_kind = ErrorKind.UPSTREAM_ERROR
        except Exception as error:  # noqa: BLE001 — 畸形事件 → unexpected_exception
            out.err_message += f"\n\n[unexpected error] {error}. Line: {line!r}"
            out.had_error = True
            out.error_kind = ErrorKind.UNEXPECTED_EXCEPTION
            break

    return out


def finalize_stream_outcome(
    stream: StreamOutcome,
    *,
    exit_code: Optional[int],
) -> StreamOutcome:
    """在归约结果上叠加 success 判定用的错误优先级。"""
    if stream.error_kind != ErrorKind.INVALID_PATH and looks_like_invalid_path_error(
        stream.err_message
    ):
        stream.error_kind = ErrorKind.INVALID_PATH
        stream.had_error = True

    if stream.thread_id is None:
        if not stream.error_kind:
            stream.error_kind = ErrorKind.PROTOCOL_MISSING_SESSION
        stream.err_message = "未能获取 SESSION_ID。\n\n" + stream.err_message
        stream.had_error = True

    if not stream.agent_messages:
        if not stream.error_kind:
            stream.error_kind = ErrorKind.EMPTY_RESULT
        stream.err_message = (
            "未能获取 Codex 响应内容。可尝试设置 return_all_messages=True 获取详细信息。\n\n"
            + stream.err_message
        )
        stream.had_error = True

    if exit_code is not None and exit_code != 0 and not stream.had_error:
        stream.had_error = True
        if not stream.error_kind:
            stream.error_kind = ErrorKind.SUBPROCESS_ERROR
        stream.err_message = f"进程退出码非零：{exit_code}\n\n" + stream.err_message

    return stream
