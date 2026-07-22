from mcp.types import (
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
)

from starter_agent.mcp.tool_adapter import McpToolResultAdapter


def test_adapter_converts_real_mcp_blocks_and_binds_trace_metadata() -> None:
    result = CallToolResult(
        content=[
            TextContent(type="text", text="Job details"),
            ImageContent(type="image", data="aGVsbG8=", mimeType="image/png"),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(
                    uri="https://jobs.example/roles/42",
                    mimeType="text/plain",
                    text="Responsibilities: build agents",
                ),
            ),
        ],
        structuredContent={"final_url": "https://jobs.example/roles/42"},
        _meta={
            "requested_url": "https://jobs.example/r/42",
            "authorization": "Bearer should-not-survive",
        },
    )

    adapted = McpToolResultAdapter().adapt(
        result,
        server_id="jobs",
        call_id="call-42",
        snapshot_id="snapshot-7",
        schema_hash="a" * 64,
    )

    assert adapted.ok is True
    assert adapted.data["content"][0] == {"type": "text", "text": "Job details"}
    assert adapted.data["content"][1]["data_omitted"] is True
    assert adapted.data["content"][1]["content_sha256"]
    assert adapted.data["content"][2]["uri"] == "https://jobs.example/roles/42"
    assert adapted.metadata == {
        "is_untrusted_external_content": True,
        "server_id": "jobs",
        "call_id": "call-42",
        "snapshot_id": "snapshot-7",
        "schema_hash": "a" * 64,
        "requested_url": "https://jobs.example/r/42",
        "final_url": "https://jobs.example/roles/42",
        "source_url": "https://jobs.example/roles/42",
    }


def test_adapter_preserves_mcp_error_without_exposing_upstream_secret_metadata() -> None:
    adapted = McpToolResultAdapter().adapt(
        CallToolResult(
            isError=True,
            content=[TextContent(type="text", text="not found")],
            _meta={"cookie": "session=secret"},
        ),
        server_id="jobs",
        call_id="call-error",
        snapshot_id="snapshot-7",
        schema_hash="b" * 64,
    )

    assert adapted.ok is False
    assert adapted.error_code == "mcp_tool_error"
    assert "secret" not in repr(adapted.model_dump())
