from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass

from starter_agent.agent.token_counter import TokenCounter


@dataclass(frozen=True)
class GuardedToolResult:
    content: str
    raw_result_tokens: int
    context_result_tokens: int
    is_truncated: bool
    raw_source_ref: str | None = None


class ToolResultGuard:
    def __init__(self, counter: TokenCounter, max_result_tokens: int):
        self.counter = counter
        self.max_result_tokens = max_result_tokens

    def guard(
        self,
        content: str,
        tool_name: str,
        tool_call_id: str,
        raw_source_ref: str,
    ) -> GuardedToolResult:
        raw_tokens = self.counter.tool_message(
            content, tool_name, tool_call_id
        ).tokens
        if raw_tokens <= self.max_result_tokens:
            return GuardedToolResult(
                content=content,
                raw_result_tokens=raw_tokens,
                context_result_tokens=raw_tokens,
                is_truncated=False,
            )

        try:
            original = json.loads(content)
        except json.JSONDecodeError:
            original = {"ok": True, "data": content}
        original_count = _result_count(original)
        structured = self._trim_structured_list(
            original,
            original_count,
            raw_tokens,
            tool_name,
            tool_call_id,
            raw_source_ref,
        )
        if structured is not None:
            return structured
        keep_chars = max(120, int(len(content) * self.max_result_tokens / raw_tokens))
        keep_chars = min(keep_chars, len(content))

        while True:
            envelope = {
                "ok": original.get("ok", True) if isinstance(original, dict) else True,
                "data": {"partial_content": content[:keep_chars]},
                "display": "工具结果超过 Context 预算，已保留部分内容",
                "metadata": {
                    "is_truncated": True,
                    "original_count": original_count,
                    "returned_count": None,
                    "omitted_count": None,
                    "has_more": True,
                    "raw_source_ref": raw_source_ref,
                    "continuation_hint": "缩小查询范围或请求展开 raw_source_ref",
                    "truncation_reason": "token_budget",
                    "raw_result_tokens": raw_tokens,
                    "max_result_tokens": self.max_result_tokens,
                },
            }
            guarded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
            context_tokens = self.counter.tool_message(
                guarded, tool_name, tool_call_id
            ).tokens
            if context_tokens <= self.max_result_tokens or keep_chars <= 120:
                break
            keep_chars = max(120, int(keep_chars * 0.8))

        envelope["metadata"]["context_result_tokens"] = context_tokens
        guarded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        context_tokens = self.counter.tool_message(
            guarded, tool_name, tool_call_id
        ).tokens
        return GuardedToolResult(
            content=guarded,
            raw_result_tokens=raw_tokens,
            context_result_tokens=context_tokens,
            is_truncated=True,
            raw_source_ref=raw_source_ref,
        )

    def _trim_structured_list(
        self,
        original: object,
        original_count: int | None,
        raw_tokens: int,
        tool_name: str,
        tool_call_id: str,
        raw_source_ref: str,
    ) -> GuardedToolResult | None:
        location = _list_location(original)
        if location is None or original_count is None:
            return None
        for returned_count in range(original_count - 1, -1, -1):
            candidate = deepcopy(original)
            target = _list_at(candidate, location)
            del target[returned_count:]
            if isinstance(candidate, dict):
                metadata = candidate.setdefault("metadata", {})
                if not isinstance(metadata, dict):
                    metadata = {}
                    candidate["metadata"] = metadata
                metadata.update(
                    {
                        "is_truncated": True,
                        "original_count": original_count,
                        "returned_count": returned_count,
                        "omitted_count": original_count - returned_count,
                        "has_more": returned_count < original_count,
                        "raw_source_ref": raw_source_ref,
                        "continuation_hint": "缩小查询范围或请求展开 raw_source_ref",
                        "truncation_reason": "token_budget",
                        "raw_result_tokens": raw_tokens,
                        "max_result_tokens": self.max_result_tokens,
                    }
                )
            serialized = json.dumps(
                candidate, ensure_ascii=False, separators=(",", ":")
            )
            context_tokens = self.counter.tool_message(
                serialized, tool_name, tool_call_id
            ).tokens
            if context_tokens <= self.max_result_tokens:
                candidate["metadata"]["context_result_tokens"] = context_tokens
                serialized = json.dumps(
                    candidate, ensure_ascii=False, separators=(",", ":")
                )
                context_tokens = self.counter.tool_message(
                    serialized, tool_name, tool_call_id
                ).tokens
                return GuardedToolResult(
                    content=serialized,
                    raw_result_tokens=raw_tokens,
                    context_result_tokens=context_tokens,
                    is_truncated=True,
                    raw_source_ref=raw_source_ref,
                )
        return None


def _result_count(value: object) -> int | None:
    if isinstance(value, list):
        return len(value)
    if not isinstance(value, dict):
        return None
    data = value.get("data")
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for candidate in data.values():
            if isinstance(candidate, list):
                return len(candidate)
    return None


def _list_location(value: object) -> tuple[str, str | None] | None:
    if not isinstance(value, dict):
        return None
    data = value.get("data")
    if isinstance(data, list):
        return ("data", None)
    if isinstance(data, dict):
        for key, candidate in data.items():
            if isinstance(candidate, list):
                return ("data", key)
    return None


def _list_at(value: object, location: tuple[str, str | None]) -> list:
    root, key = location
    if root == "root":
        return value  # type: ignore[return-value]
    data = value["data"]  # type: ignore[index]
    if key is None:
        return data
    return data[key]
