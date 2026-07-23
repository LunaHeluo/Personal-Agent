from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from starter_agent.agent.token_counter import TokenCounter


_SAFE_TRACE_METADATA = frozenset(
    {
        "call_id",
        "content_sha256",
        "final_url",
        "is_untrusted_external_content",
        "requested_url",
        "schema_hash",
        "server_id",
        "snapshot_id",
        "source_content_sha256",
        "source_url",
    }
)
_URL_METADATA = frozenset({"final_url", "requested_url", "source_url"})
_HASH_METADATA = frozenset(
    {"content_sha256", "schema_hash", "source_content_sha256"}
)
_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|auth[_-]?code|cookie|credential|csrf|"
    r"pass(?:word|wd)?|secret|session|token)",
    re.IGNORECASE,
)
_SENSITIVE_CONTAINER_KEY = re.compile(
    r"^(?:authorization|cookies?|form(?:_data|_fields)?|headers?)$",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?P<key>api[_-]?key|authorization|auth[_-]?code|cookie|credential|csrf|"
    r"pass(?:word|wd)?|secret|session|token)"
    r"(?P<separator>\s*[=:]\s*)(?P<value>[^\r\n]+)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bBearer\s+[^\s,;\"']+", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


@dataclass(frozen=True)
class GuardedToolResult:
    content: str
    raw_result_tokens: int
    context_result_tokens: int
    is_truncated: bool
    raw_source_ref: str | None = None
    raw_result_bytes: int = 0
    raw_result_chars: int = 0
    kept_result_bytes: int = 0
    kept_result_chars: int = 0
    kept_result_tokens: int = 0
    content_sha256: str = ""
    truncation_reason: str | None = None
    redacted_content: str = ""


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
        redacted = _redact_content(content)
        redacted_tokens = self.counter.tool_message(
            redacted, tool_name, tool_call_id
        ).tokens
        measurements = _Measurements(
            raw_bytes=len(content.encode("utf-8")),
            raw_chars=len(content),
            raw_tokens=raw_tokens,
            content_sha256=hashlib.sha256(redacted.encode("utf-8")).hexdigest(),
        )
        if redacted_tokens <= self.max_result_tokens:
            return self._result(
                content=redacted,
                redacted_content=redacted,
                context_tokens=redacted_tokens,
                measurements=measurements,
                is_truncated=False,
            )

        try:
            original = json.loads(redacted)
        except json.JSONDecodeError:
            original = {"ok": True, "data": redacted}
        original_count = _result_count(original)
        structured = self._trim_structured_list(
            original,
            original_count,
            measurements,
            tool_name,
            tool_call_id,
            raw_source_ref,
            redacted,
        )
        if structured is not None:
            return structured
        return self._trim_generic_payload(
            original,
            measurements,
            tool_name,
            tool_call_id,
            raw_source_ref,
            redacted,
        )

    def _trim_generic_payload(
        self,
        original: object,
        measurements: "_Measurements",
        tool_name: str,
        tool_call_id: str,
        raw_source_ref: str,
        redacted_content: str,
    ) -> GuardedToolResult:
        metadata = self._truncation_metadata(
            original, measurements, raw_source_ref
        )
        partial_source = _sanitized_payload_text(original)
        ok = original.get("ok", True) if isinstance(original, dict) else True

        def full_envelope(partial_content: str) -> dict[str, object]:
            return {
                "ok": ok,
                "data": {"partial_content": partial_content},
                "display": "Tool result truncated.",
                "metadata": dict(metadata),
            }

        compact_metadata = {
            **_safe_trace_metadata(original),
            "is_truncated": True,
        }
        for build, partial in (
            (full_envelope, partial_source),
            (
                lambda _partial: {
                    "ok": ok,
                    "data": {},
                    "metadata": dict(metadata),
                },
                "",
            ),
            (lambda _partial: {"metadata": dict(compact_metadata)}, ""),
        ):
            keep_chars = len(partial)
            while True:
                envelope = build(partial[:keep_chars])
                guarded, context_tokens = self._serialize_with_context_tokens(
                    envelope, tool_name, tool_call_id
                )
                if context_tokens <= self.max_result_tokens:
                    return self._result(
                        content=guarded,
                        redacted_content=redacted_content,
                        context_tokens=context_tokens,
                        measurements=measurements,
                        is_truncated=True,
                        raw_source_ref=raw_source_ref,
                    )
                if keep_chars == 0:
                    break
                keep_chars = max(0, int(keep_chars * 0.65))

        context_tokens = self.counter.tool_message(
            "", tool_name, tool_call_id
        ).tokens
        return self._result(
            content="",
            redacted_content=redacted_content,
            context_tokens=context_tokens,
            measurements=measurements,
            is_truncated=True,
            raw_source_ref=raw_source_ref,
        )

    def _trim_structured_list(
        self,
        original: object,
        original_count: int | None,
        measurements: "_Measurements",
        tool_name: str,
        tool_call_id: str,
        raw_source_ref: str,
        redacted_content: str,
    ) -> GuardedToolResult | None:
        location = _list_location(original)
        if location is None or original_count is None:
            return None
        for returned_count in range(original_count - 1, -1, -1):
            candidate = deepcopy(original)
            target = _list_at(candidate, location)
            del target[returned_count:]
            if isinstance(candidate, dict):
                metadata = self._truncation_metadata(
                    original, measurements, raw_source_ref
                )
                metadata.update(
                    {
                        "original_count": original_count,
                        "returned_count": returned_count,
                        "omitted_count": original_count - returned_count,
                        "has_more": returned_count < original_count,
                        "continuation_hint": (
                            "Narrow the query or inspect the restricted raw_source_ref."
                        ),
                    }
                )
                candidate["metadata"] = metadata
            serialized, context_tokens = self._serialize_with_context_tokens(
                candidate, tool_name, tool_call_id
            )
            if context_tokens <= self.max_result_tokens:
                return self._result(
                    content=serialized,
                    redacted_content=redacted_content,
                    context_tokens=context_tokens,
                    measurements=measurements,
                    is_truncated=True,
                    raw_source_ref=raw_source_ref,
                )
        return None

    def _truncation_metadata(
        self,
        original: object,
        measurements: "_Measurements",
        raw_source_ref: str,
    ) -> dict[str, object]:
        return {
            **_safe_trace_metadata(original),
            "is_truncated": True,
            "raw_source_ref": raw_source_ref,
            "truncation_reason": "token_budget",
            "raw_result_tokens": measurements.raw_tokens,
            "max_result_tokens": self.max_result_tokens,
        }

    def _serialize_with_context_tokens(
        self,
        envelope: dict[str, object],
        tool_name: str,
        tool_call_id: str,
    ) -> tuple[str, int]:
        metadata = envelope.get("metadata")
        if not isinstance(metadata, dict):
            serialized = _json_dumps(envelope)
            return serialized, self.counter.tool_message(
                serialized, tool_name, tool_call_id
            ).tokens

        for _ in range(12):
            serialized = _json_dumps(envelope)
            context_tokens = self.counter.tool_message(
                serialized, tool_name, tool_call_id
            ).tokens
            final_values = {"context_result_tokens": context_tokens}
            if all(metadata.get(key) == value for key, value in final_values.items()):
                return serialized, context_tokens
            metadata.update(final_values)

        serialized = _json_dumps(envelope)
        return serialized, self.counter.tool_message(
            serialized, tool_name, tool_call_id
        ).tokens

    @staticmethod
    def _result(
        *,
        content: str,
        redacted_content: str,
        context_tokens: int,
        measurements: "_Measurements",
        is_truncated: bool,
        raw_source_ref: str | None = None,
    ) -> GuardedToolResult:
        return GuardedToolResult(
            content=content,
            raw_result_tokens=measurements.raw_tokens,
            context_result_tokens=context_tokens,
            is_truncated=is_truncated,
            raw_source_ref=raw_source_ref,
            raw_result_bytes=measurements.raw_bytes,
            raw_result_chars=measurements.raw_chars,
            kept_result_bytes=len(content.encode("utf-8")),
            kept_result_chars=len(content),
            kept_result_tokens=context_tokens,
            content_sha256=measurements.content_sha256,
            truncation_reason="token_budget" if is_truncated else None,
            redacted_content=redacted_content,
        )


@dataclass(frozen=True)
class _Measurements:
    raw_bytes: int
    raw_chars: int
    raw_tokens: int
    content_sha256: str


def _redact_content(content: str) -> str:
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return _redact_text(content)
    return _json_dumps(_redact_value(value))


def redact_tool_result_content(content: str) -> str:
    """Public persistence boundary: return content safe for restricted storage."""

    return _redact_content(content)


def _redact_value(value: Any, *, sensitive: bool = False) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_value(
                item,
                sensitive=(
                    sensitive
                    or bool(_SENSITIVE_KEY.search(str(key)))
                    or bool(_SENSITIVE_CONTAINER_KEY.fullmatch(str(key)))
                ),
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, sensitive=sensitive) for item in value]
    if sensitive and value is not None:
        return "[redacted]"
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(value: str) -> str:
    redacted = _URL.sub(lambda match: _sanitize_url(match.group(0)), value)
    redacted = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}[redacted]",
        redacted,
    )
    redacted = _BEARER.sub("Bearer [redacted]", redacted)
    return redacted


def _sanitize_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return "[invalid-url]"
    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not _SENSITIVE_KEY.search(key)
        ]
    )
    return urlunsplit((parsed.scheme, f"{hostname}{port}", parsed.path, query, ""))


def _safe_trace_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    safe: dict[str, object] = {}
    for key in _SAFE_TRACE_METADATA:
        item = metadata.get(key)
        if key == "is_untrusted_external_content":
            if item is True:
                safe[key] = True
        elif key in _URL_METADATA and isinstance(item, str) and item:
            safe[key] = _sanitize_url(item)
        elif key in _HASH_METADATA and isinstance(item, str) and re.fullmatch(
            r"[0-9a-fA-F]{64}", item
        ):
            safe[key] = item.casefold()
        elif isinstance(item, str) and _SAFE_IDENTIFIER.fullmatch(item):
            safe[key] = item
    return safe


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


def _sanitized_payload_text(value: object) -> str:
    if isinstance(value, dict):
        value = {key: item for key, item in value.items() if key != "metadata"}
    return _json_dumps(value)


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


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
