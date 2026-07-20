"""脱敏：把行流事件里的大块工具输出截断后才允许外流。

脱敏是安全关切，不是错误处理 —— 它同时服务成功路径（all_messages）
与失败路径（error_detail.last_lines），两条路径共用同一套规则。
"""

from __future__ import annotations

import copy
import json
from typing import Any

# last_lines 诊断窗口上限。归约期（stream）与错误详情装配期（build_error_detail）
# 共用同一策略 —— 三处字面值曾各写一遍，改一处不会让另两处变红。
LAST_LINES_LIMIT = 50


def redact_tool_result_event(event: dict[str, Any]) -> dict[str, Any]:
    """对已解析 JSON 事件做 tool_result 脱敏（deepcopy）。

    仅当 item.type == tool_result 且存在 content 键时，将 content 置为 "[truncated]"。
    """
    safe = copy.deepcopy(event)
    item = safe.get("item", {})
    if isinstance(item, dict) and item.get("type") == "tool_result" and "content" in item:
        item["content"] = "[truncated]"
    return safe


def tail_window(lines: list[str], max_lines: int = LAST_LINES_LIMIT) -> list[str]:
    """诊断窗口的唯一算法：保尾 max_lines 行（返回新列表）。

    ``max_lines <= 0`` 视为零窗口返回空列表 —— ``lines[-0:]`` 的「返回全量」
    是 Python 切片陷阱，不是本窗口的契约。
    归约期（stream）与错误详情装配期（filter_last_lines）都调它 ——
    此前两处各写一遍同一语义，共享的只有常量。
    """
    if max_lines <= 0:
        return []
    return list(lines[-max_lines:])


def filter_last_lines(
    lines: list[str], max_lines: int = LAST_LINES_LIMIT
) -> list[str]:
    """过滤 last_lines，脱敏 tool_result 中的大内容。

    非 tool_result 行按原样保留（不重新序列化），以免改变原始字节。
    """
    filtered: list[str] = []
    for line in lines:
        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                filtered.append(line)
                continue
            item = data.get("item", {})
            if isinstance(item, dict) and item.get("type") == "tool_result":
                redacted = redact_tool_result_event(data)
                filtered.append(json.dumps(redacted, ensure_ascii=False))
                continue
            filtered.append(line)
        except (json.JSONDecodeError, TypeError, AttributeError):
            filtered.append(line)
    return tail_window(filtered, max_lines)
