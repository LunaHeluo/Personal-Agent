from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mcp.types import (
    AudioContent,
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)

from starter_agent.domain.models import ToolResult
from starter_agent.tools.adapters.job_description_extractor import (
    JobDescriptionExtractor,
)


_SAFE_UPSTREAM_METADATA = frozenset(
    {"final_url", "source_url", "content_sha256"}
)
_SENSITIVE_QUERY_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "cookie",
        "password",
        "secret",
        "token",
    }
)
_PLAYWRIGHT_PAGE_URL = re.compile(
    r"(?m)^-\s*Page URL:\s*(https?://\S+)\s*$"
)
_ERROR_URL = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_ERROR_AUTHORIZATION = re.compile(
    r"(?i)(\bauthorization\s*:\s*bearer\s+)\S+"
)
_ERROR_SECRET_FIELD = re.compile(
    r"(?i)(\b(?:cookie|password|secret|token|api[_-]?key)\s*[:=]\s*)\S+"
)
_PLAYWRIGHT_ERROR_PATTERNS = (
    (re.compile(r"timeout|timed out", re.IGNORECASE), "playwright_timeout"),
    (
        re.compile(r"(?:http\s*)?403|forbidden", re.IGNORECASE),
        "access_blocked_403",
    ),
    (
        re.compile(
            r"locator|selector|did not resolve|not found",
            re.IGNORECASE,
        ),
        "selector_unmatched",
    ),
    (
        re.compile(
            r"browser.*closed|context.*closed|page.*closed|crash|connection reset",
            re.IGNORECASE,
        ),
        "browser_crashed",
    ),
)


def _sanitize_mcp_error_summary(text: str, *, max_chars: int = 500) -> str:
    normalized = " ".join(text.split())
    normalized = _ERROR_AUTHORIZATION.sub(r"\1[REDACTED]", normalized)
    normalized = _ERROR_SECRET_FIELD.sub(r"\1[REDACTED]", normalized)

    def sanitize_match(match: re.Match[str]) -> str:
        value = match.group(0)
        trailing = ""
        while value and value[-1] in ".,;:)]}":
            trailing = value[-1] + trailing
            value = value[:-1]
        return f"{_sanitize_url(value)}{trailing}"

    normalized = _ERROR_URL.sub(sanitize_match, normalized)
    return normalized[:max_chars] or "Unknown Playwright failure"


def classify_playwright_error(text: str) -> tuple[str, str]:
    summary = _sanitize_mcp_error_summary(text)
    error_code = next(
        (
            code
            for pattern, code in _PLAYWRIGHT_ERROR_PATTERNS
            if pattern.search(summary)
        ),
        "mcp_unknown_error",
    )
    return error_code, summary


class McpToolResultAdapter:
    """Convert an official MCP result into the agent's inert ToolResult model."""

    def adapt(
        self,
        result: CallToolResult,
        *,
        server_id: str,
        call_id: str,
        snapshot_id: str,
        schema_hash: str,
        requested_url: str | None = None,
        tool_name: str | None = None,
    ) -> ToolResult:
        structured = dict(result.structuredContent or {})
        upstream_structured_keys = sorted(
            str(key)[:80] for key in structured.keys()
        )[:30]
        blocks = [self._content_block(block) for block in result.content]
        playwright_error = result.isError and server_id.casefold() == "playwright"
        if playwright_error:
            blocks = [
                {
                    **block,
                    "text": _sanitize_mcp_error_summary(str(block["text"])),
                }
                if block.get("type") == "text" and "text" in block
                else block
                for block in blocks
            ]
        snapshot_text: str | None = None
        if (
            tool_name in {"browser_snapshot", "browser_navigate"}
            and not result.isError
        ):
            snapshot_text = _playwright_snapshot_text(
                structured,
                blocks,
            )
            normalized_jd = any(
                key in structured
                for key in ("title", "responsibilities", "requirements")
            )
            if snapshot_text and not normalized_jd:
                extracted = (
                    JobDescriptionExtractor().extract_playwright_snapshot(
                        snapshot_text
                    )
                )
                structured = {
                    "title": extracted.title,
                    "company": extracted.company,
                    "location": extracted.location,
                    "responsibilities": extracted.responsibilities,
                    "requirements": extracted.requirements,
                    "preferred_qualifications": extracted.preferred_qualifications,
                    "raw_text": extracted.raw_text,
                    "completeness": extracted.completeness,
                    "extraction_method": extracted.extraction_method,
                    "page_type": extracted.page_type,
                    "validation_state": extracted.validation_state,
                    "source_spans": [
                        asdict(item) for item in extracted.source_spans
                    ],
                }
            if snapshot_text:
                page_url = _PLAYWRIGHT_PAGE_URL.search(snapshot_text)
                if page_url:
                    structured["final_url"] = page_url.group(1)
                    structured["source_url"] = page_url.group(1)
        upstream = {
            key: value
            for key, value in (result.meta or {}).items()
            if key in _SAFE_UPSTREAM_METADATA
        }
        metadata: dict[str, Any] = {
            "is_untrusted_external_content": True,
            "server_id": server_id,
            "call_id": call_id,
            "snapshot_id": snapshot_id,
            "schema_hash": schema_hash,
        }
        if tool_name in {"browser_snapshot", "browser_navigate"}:
            metadata["upstream_structured_keys"] = upstream_structured_keys
            metadata["snapshot_chars"] = (
                len(snapshot_text) if snapshot_text is not None else 0
            )
            metadata["snapshot_line_shapes"] = (
                _snapshot_line_shapes(snapshot_text)
                if snapshot_text is not None
                else []
            )
            metadata["snapshot_headings"] = (
                _snapshot_headings(snapshot_text)
                if snapshot_text is not None
                else []
            )
            metadata["snapshot_signal_samples"] = (
                _snapshot_signal_samples(snapshot_text)
                if snapshot_text is not None
                else []
            )
        if requested_url:
            metadata["requested_url"] = _sanitize_url(requested_url)
        for key in ("final_url", "source_url"):
            value = upstream.get(key, structured.get(key))
            if isinstance(value, str) and value:
                metadata[key] = _sanitize_url(value)
        final_url = metadata.get("final_url")
        if "source_url" not in metadata and isinstance(final_url, str):
            metadata["source_url"] = final_url
        source_content_hash = upstream.get(
            "content_sha256", structured.get("content_sha256")
        )
        if (
            isinstance(source_content_hash, str)
            and len(source_content_hash) == 64
            and all(character in "0123456789abcdefABCDEF" for character in source_content_hash)
        ):
            metadata["source_content_sha256"] = source_content_hash.casefold()
        elif snapshot_text is not None:
            metadata["source_content_sha256"] = hashlib.sha256(
                snapshot_text.encode("utf-8")
            ).hexdigest()
        metadata["content_sha256"] = hashlib.sha256(
            json.dumps(
                {"content": blocks, "structured_content": structured},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        error_code: str | None = None
        display = "MCP tool completed."
        if result.isError:
            display = "MCP tool failed."
            error_code = "mcp_tool_error"
        if playwright_error:
            error_text = "\n".join(
                str(block["text"])
                for block in blocks
                if block.get("type") == "text" and block.get("text")
            )
            error_code, error_summary = classify_playwright_error(error_text)
            metadata["upstream_error_summary"] = error_summary
            display = f"Playwright failed: {error_summary}"

        return ToolResult(
            ok=not result.isError,
            data={"content": blocks, "structured_content": structured},
            display=display,
            error_code=error_code,
            metadata=metadata,
        )

    @staticmethod
    def _content_block(block: object) -> dict[str, Any]:
        if isinstance(block, TextContent):
            return {"type": "text", "text": block.text}
        if isinstance(block, (ImageContent, AudioContent)):
            raw = _decode_base64(block.data)
            return {
                "type": block.type,
                "mime_type": block.mimeType,
                "data_omitted": True,
                "decoded_bytes": len(raw),
                "content_sha256": hashlib.sha256(raw).hexdigest(),
            }
        if isinstance(block, ResourceLink):
            return {
                "type": "resource_link",
                "name": block.name,
                "title": block.title,
                "uri": _sanitize_url(str(block.uri)),
                "description": block.description,
                "mime_type": block.mimeType,
            }
        if isinstance(block, EmbeddedResource):
            resource = block.resource
            common = {
                "type": "resource",
                "uri": _sanitize_url(str(resource.uri)),
                "mime_type": resource.mimeType,
            }
            if isinstance(resource, TextResourceContents):
                return {**common, "text": resource.text}
            if isinstance(resource, BlobResourceContents):
                raw = _decode_base64(resource.blob)
                return {
                    **common,
                    "data_omitted": True,
                    "decoded_bytes": len(raw),
                    "content_sha256": hashlib.sha256(raw).hexdigest(),
                }
        return {"type": "unsupported", "data_omitted": True}


def _decode_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return value.encode("utf-8", errors="replace")


def _playwright_snapshot_text(
    structured: dict[str, Any],
    blocks: list[dict[str, Any]],
) -> str:
    def candidates(value: Any, *, depth: int = 0):
        if depth > 3:
            return
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key in ("snapshot", "text", "content", "result", "data"):
                if key in value:
                    yield from candidates(value[key], depth=depth + 1)
        elif isinstance(value, list):
            for item in value[:20]:
                yield from candidates(item, depth=depth + 1)

    structured_candidates = list(candidates(structured))
    block_candidates = [
        block["text"]
        for block in blocks
        if block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    values = [
        value
        for value in (*structured_candidates, *block_candidates)
        if "- Page URL:" in value
        or "### Snapshot" in value
        or re.search(r'-\s+heading\s+"', value)
    ]
    if values:
        return max(values, key=len)
    return "\n".join(block_candidates)


def _snapshot_line_shapes(value: str) -> list[str]:
    shapes: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r'"[^"]*"', '"<text>"', line)
        if ":" in line and not line.startswith("- Page URL:"):
            line = line.partition(":")[0] + ": <text>"
        shapes.append(line[:160])
        if len(shapes) >= 30:
            break
    return shapes


def _snapshot_headings(value: str) -> list[str]:
    headings: list[str] = []
    for match in re.finditer(r'-\s+heading\s+"([^"]+)"', value):
        headings.append(match.group(1).strip()[:160])
        if len(headings) >= 30:
            break
    return headings


def _snapshot_signal_samples(value: str) -> list[str]:
    signal = re.compile(
        r"\b(?:responsibilit(?:y|ies)|requirements?|qualifications?|"
        r"job description|what you will do|what you'll do)\b",
        re.IGNORECASE,
    )
    samples: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if signal.search(line):
            samples.append(line[:240])
        if len(samples) >= 20:
            break
    return samples


def _sanitize_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[invalid-url]"
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    netloc = f"{hostname}{port}"
    query = urlencode(
        [
            (key, "[redacted]" if key.casefold() in _SENSITIVE_QUERY_NAMES else item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))
