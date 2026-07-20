from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass

from starter_agent.agent.token_counter import TokenCounter


_SAFE_CLASSIFICATION_METADATA = frozenset({"is_untrusted_external_content"})


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
        return self._trim_generic_payload(
            original,
            raw_tokens,
            tool_name,
            tool_call_id,
            raw_source_ref,
        )

    def _trim_generic_payload(
        self,
        original: object,
        raw_tokens: int,
        tool_name: str,
        tool_call_id: str,
        raw_source_ref: str,
    ) -> GuardedToolResult:
        """Return a bounded generic fallback without replaying source metadata."""

        metadata = {
            **_safe_classification_metadata(original),
            "is_truncated": True,
            "raw_source_ref": raw_source_ref,
            "truncation_reason": "token_budget",
            "raw_result_tokens": raw_tokens,
            "max_result_tokens": self.max_result_tokens,
        }
        partial_source = _sanitized_payload_text(original)
        ok = original.get("ok", True) if isinstance(original, dict) else True

        def full_envelope(partial_content: str) -> dict[str, object]:
            return {
                "ok": ok,
                "data": {"partial_content": partial_content},
                "display": "Tool result truncated.",
                "metadata": dict(metadata),
            }

        def metadata_only() -> dict[str, object]:
            return {"ok": ok, "data": {}, "metadata": dict(metadata)}

        compact_metadata = {
            **_safe_classification_metadata(original),
            "is_truncated": True,
        }
        for build, partial in (
            (full_envelope, partial_source),
            (lambda _partial: metadata_only(), ""),
            (lambda _partial: {"metadata": dict(compact_metadata)}, ""),
        ):
            keep_chars = len(partial)
            while True:
                envelope = build(partial[:keep_chars])
                guarded, context_tokens = self._serialize_with_context_tokens(
                    envelope, tool_name, tool_call_id
                )
                if context_tokens <= self.max_result_tokens:
                    return GuardedToolResult(
                        content=guarded,
                        raw_result_tokens=raw_tokens,
                        context_result_tokens=context_tokens,
                        is_truncated=True,
                        raw_source_ref=raw_source_ref,
                    )
                if keep_chars == 0:
                    break
                keep_chars = max(0, int(keep_chars * 0.65))

        # Tool-message framing alone can exceed a pathological budget. Empty
        # content is the smallest safe fallback in that configuration.
        context_tokens = self.counter.tool_message(
            "", tool_name, tool_call_id
        ).tokens
        return GuardedToolResult(
            content="",
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
                metadata = _safe_classification_metadata(original)
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
                candidate["metadata"] = metadata
            serialized, context_tokens = self._serialize_with_context_tokens(
                candidate, tool_name, tool_call_id
            )
            if context_tokens <= self.max_result_tokens:
                return GuardedToolResult(
                    content=serialized,
                    raw_result_tokens=raw_tokens,
                    context_result_tokens=context_tokens,
                    is_truncated=True,
                    raw_source_ref=raw_source_ref,
                )
        return None

    def _serialize_with_context_tokens(
        self,
        envelope: dict[str, object],
        tool_name: str,
        tool_call_id: str,
    ) -> tuple[str, int]:
        """Serialize an envelope after recording its final token count."""

        metadata = envelope.get("metadata")
        if not isinstance(metadata, dict):
            serialized = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
            return serialized, self.counter.tool_message(
                serialized, tool_name, tool_call_id
            ).tokens

        for _ in range(8):
            serialized = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
            context_tokens = self.counter.tool_message(
                serialized, tool_name, tool_call_id
            ).tokens
            if metadata.get("context_result_tokens") == context_tokens:
                return serialized, context_tokens
            metadata["context_result_tokens"] = context_tokens

        serialized = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        return serialized, self.counter.tool_message(
            serialized, tool_name, tool_call_id
        ).tokens


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


def _safe_classification_metadata(value: object) -> dict[str, bool]:
    """Keep only trusted, boolean classification labels across truncation."""

    if not isinstance(value, dict):
        return {}
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    return {
        key: True
        for key in _SAFE_CLASSIFICATION_METADATA
        if metadata.get(key) is True
    }


def _sanitized_payload_text(value: object) -> str:
    """Serialize a source result after dropping its untrusted envelope metadata."""

    if isinstance(value, dict):
        value = {key: item for key, item in value.items() if key != "metadata"}
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


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
