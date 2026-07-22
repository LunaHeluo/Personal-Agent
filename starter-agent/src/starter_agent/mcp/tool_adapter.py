from __future__ import annotations

import base64
import hashlib
import json
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
    ) -> ToolResult:
        structured = dict(result.structuredContent or {})
        blocks = [self._content_block(block) for block in result.content]
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
        metadata["content_sha256"] = hashlib.sha256(
            json.dumps(
                {"content": blocks, "structured_content": structured},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        return ToolResult(
            ok=not result.isError,
            data={"content": blocks, "structured_content": structured},
            display=("MCP tool failed." if result.isError else "MCP tool completed."),
            error_code="mcp_tool_error" if result.isError else None,
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
