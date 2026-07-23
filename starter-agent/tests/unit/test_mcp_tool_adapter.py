from datetime import UTC, datetime
import json
from pathlib import Path
from urllib.parse import quote, unquote
from uuid import uuid4

import pytest
from mcp.types import (
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
)

from starter_agent.agent.runtime import AgentRuntime
from starter_agent.capabilities.gate import PreToolCallGate, UnifiedToolExecutor
from starter_agent.capabilities.models import (
    PolicyRule,
    Snapshot,
    Tool,
    canonical_json_sha256,
)
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.capabilities.store import CapabilityStore
from starter_agent.domain.models import Message, ModelResponse, ToolCall
from starter_agent.mcp.client import ClientMetadata
from starter_agent.mcp.config import McpConfiguration, McpServerConfig
from starter_agent.mcp.manager import McpManager
from starter_agent.mcp.tool_adapter import McpToolResultAdapter
from starter_agent.domain.models import ToolResult
from starter_agent.providers.base import Provider
from starter_agent.settings import ContextConfig, RuntimeConfig
from starter_agent.tools.policy import ToolPolicy
from starter_agent.tools.registry import ToolRegistry


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
    assert adapted.metadata["is_untrusted_external_content"] is True
    assert adapted.metadata["server_id"] == "jobs"
    assert adapted.metadata["call_id"] == "call-42"
    assert adapted.metadata["snapshot_id"] == "snapshot-7"
    assert adapted.metadata["schema_hash"] == "a" * 64
    assert adapted.metadata["final_url"] == "https://jobs.example/roles/42"
    assert adapted.metadata["source_url"] == "https://jobs.example/roles/42"
    assert "requested_url" not in adapted.metadata
    assert len(adapted.metadata["content_sha256"]) == 64


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


class _ResultSession:
    async def call_tool(self, name, arguments):
        assert name == "fetch_job"
        assert arguments == {"url": "https://jobs.example/requested"}
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text="Responsibilities: build secure agents. " * 1_000,
                )
            ],
            structuredContent={
                "final_url": "https://jobs.example/final",
                "title": "Agent Engineer",
            },
            _meta={"requested_url": "https://attacker.example/forged"},
        )


class _FailingResultSession:
    async def call_tool(self, name, arguments):
        assert name == "fetch_job"
        assert arguments == {"url": "https://jobs.example/requested"}
        raise RuntimeError("Authorization: Bearer TOP-SECRET-UPSTREAM")


class _ArtifactUrlResultSession:
    async def call_tool(self, name, arguments):
        assert name == "fetch_job"
        assert arguments == {"url": "https://jobs.example/requested"}
        nested = quote(
            "https://inner-user:INNER-PASSWORD@inner.example/path"
            "?token=INNER-SECRET&ok=inner",
            safe="",
        )
        return CallToolResult(
            content=[TextContent(type="text", text="safe")],
            structuredContent={
                "final_url": (
                    "https://jobs.example/final?token=SPACE SECRET&ok=final"
                ),
                "requested_url": (
                    "https://request-user:REQUEST-PASSWORD@jobs.example/requested"
                    "?api_key=REQUEST-SECRET&ok=request"
                ),
                "source_url": (
                    f"https://outer.example/source?next={nested}&ok=outer"
                ),
                "nested": {
                    "uri": (
                        "https://uri-user:URI-PASSWORD@uri.example/item"
                        "?secret=URI-SECRET&ok=uri"
                    ),
                    "url": "https://bad.example/path\nCONTROL-SECRET",
                },
            },
        )


class _ResultClient:
    def __init__(self, result_session=None) -> None:
        self.session = None
        self._session = result_session or _ResultSession()
        self.stderr_summary = ""

    async def connect(self):
        self.session = self._session
        return ClientMetadata(
            protocol_version="2025-06-18",
            runtime_name="fixture",
            runtime_version="1",
            node_version="v22.0.0",
            npx_version="10.0.0",
            started_at=datetime.now(UTC),
        )

    async def run_session_command(self, operation):
        return await operation(self.session)

    async def close(self):
        self.session = None


class _McpProvider(Provider):
    name = "fixture"

    async def complete(self, messages, model, tools, on_delta=None, tool_choice=None):
        del model, tools, on_delta, tool_choice
        if messages[-1].role == "tool":
            return ModelResponse(content="done", provider=self.name, model="fixture")
        return ModelResponse(
            provider=self.name,
            model="fixture",
            tool_calls=[
                ToolCall(
                    id="call-real",
                    name="fetch_job",
                    arguments={"url": "https://jobs.example/requested"},
                )
            ],
        )

    async def health(self, model):
        return True, "ready"


async def _real_manager_runtime(tmp_path: Path, client: _ResultClient):
    builtin = ToolRegistry([])
    registry = UnifiedToolRegistry(builtin, allowed_risk_levels=["read", "external"])
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    gate = PreToolCallGate(store, registry=registry)
    executor = UnifiedToolExecutor(store, gate=gate)
    manager = McpManager(
        McpConfiguration(
            source_path=tmp_path / "mcp.json",
            servers={"jobs": McpServerConfig(command="npx")},
            config_hash="f" * 64,
        ),
        store=store,
        client_factory=lambda _server_id, _config: client,
        tool_executor=executor,
    )
    await manager.connect("jobs")
    input_schema = {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    }
    tool = Tool(
        snapshot_id="snapshot-real",
        server_id="jobs",
        upstream_name="fetch_job",
        model_alias="fetch_job",
        input_schema=input_schema,
        schema_hash=canonical_json_sha256(input_schema),
        risk_level="read",
        enabled=True,
        review_state="approved",
    )
    snapshot = Snapshot(
        id="snapshot-real",
        server_id="jobs",
        version=1,
        schema_hash="e" * 64,
        discovered_at=datetime.now(UTC),
        tool_count=1,
    )
    store.create_snapshot(snapshot, tools=[tool])
    active = store.activate_snapshot("jobs", snapshot.id)
    manager._get_handle("jobs").active.snapshot_id = active.id
    manager._publish_snapshot(manager._get_handle("jobs"), active)
    store.create_policy_rule(
        PolicyRule(
            id="allow-fetch-job",
            server_id="jobs",
            tool_name="fetch_job",
            effect="allowlist_auto",
            schema_hash=tool.schema_hash,
            created_by="test",
        )
    )
    registry.set_tool_enabled("fetch_job", True)
    registry.set_tool_review("fetch_job", "approved")
    runtime = AgentRuntime(
        registry,  # type: ignore[arg-type]
        ToolPolicy(["read", "external"]),
        RuntimeConfig(),
        ContextConfig(per_tool_result_tokens=300, all_tool_results_tokens=300),
        gate=gate,
        executor=executor,
    )
    return manager, runtime, tool


@pytest.mark.asyncio
async def test_real_manager_runtime_path_preserves_trusted_provenance_and_artifact(
    tmp_path: Path,
) -> None:
    manager, runtime, tool = await _real_manager_runtime(tmp_path, _ResultClient())
    artifacts: list[dict] = []
    events: list[dict] = []

    async def artifact(event):
        assert not any(
            item.action == "tool.completed"
            for item in runtime.gate.store.list_audit_events()
        )
        artifacts.append(event)

    async def event(item):
        events.append(item)

    session_id, turn_id = uuid4(), uuid4()
    await runtime.run(
        _McpProvider(),
        "fixture",
        [Message(role="user", content="fetch")],
        session_id,
        turn_id,
        on_tool_artifact=artifact,
        on_tool_event=event,
    )

    completed = next(item for item in events if item["type"] == "tool_completed")
    assert completed["server_id"] == "jobs"
    assert completed["snapshot_id"] == "snapshot-real"
    assert completed["schema_hash"] == tool.schema_hash
    assert completed["source_url"] == "https://jobs.example/final"
    assert completed["content_sha256"]
    assert completed["truncation_reason"] == "token_budget"
    assert artifacts[0]["server_id"] == "jobs"
    assert artifacts[0]["snapshot_id"] == "snapshot-real"
    assert artifacts[0]["requested_url"] == "https://jobs.example/requested"
    assert artifacts[0]["final_url"] == "https://jobs.example/final"
    assert artifacts[0]["call_id"] == "call-real"
    assert artifacts[0]["content_sha256"]
    audit = next(
        item
        for item in runtime.gate.store.list_audit_events()
        if item.action == "tool.completed"
    )
    assert (audit.session_id, audit.turn_id, audit.call_id) == (
        str(session_id),
        str(turn_id),
        "call-real",
    )
    assert audit.payload == {
        "tool_name": "fetch_job",
        "ok": True,
        "error_code": None,
        "server_id": "jobs",
        "call_id": "call-real",
        "snapshot_id": "snapshot-real",
        "schema_hash": tool.schema_hash,
        "raw_source_ref": artifacts[0]["source_ref"],
        "requested_url": "https://jobs.example/requested",
        "final_url": "https://jobs.example/final",
        "source_url": "https://jobs.example/final",
        "content_sha256": artifacts[0]["content_sha256"],
        "is_truncated": True,
        "truncation_reason": "token_budget",
        "raw_result_bytes": audit.payload["raw_result_bytes"],
        "raw_result_chars": audit.payload["raw_result_chars"],
        "raw_result_tokens": audit.payload["raw_result_tokens"],
        "kept_result_bytes": audit.payload["kept_result_bytes"],
        "kept_result_chars": audit.payload["kept_result_chars"],
        "kept_result_tokens": audit.payload["kept_result_tokens"],
        "context_result_tokens": audit.payload["context_result_tokens"],
    }
    await manager.shutdown()


@pytest.mark.asyncio
async def test_real_manager_runtime_exception_is_governed_and_never_leaks_upstream_secret(
    tmp_path: Path,
) -> None:
    manager, runtime, _tool = await _real_manager_runtime(
        tmp_path, _ResultClient(_FailingResultSession())
    )
    artifacts: list[dict] = []
    events: list[dict] = []

    async def artifact(item):
        artifacts.append(item)

    async def event(item):
        events.append(item)

    _response, generated, _tool_calls = await runtime.run(
        _McpProvider(),
        "fixture",
        [Message(role="user", content="fetch")],
        uuid4(),
        uuid4(),
        on_tool_artifact=artifact,
        on_tool_event=event,
    )

    tool_message = next(item for item in generated if item.role == "tool")
    completed = next(item for item in events if item["type"] == "tool_completed")
    audit = next(
        item
        for item in runtime.gate.store.list_audit_events()
        if item.action == "tool.completed"
    )
    assert json.loads(tool_message.content)["error_code"] == "tool_execution_error"
    assert completed["error_code"] == "tool_execution_error"
    assert audit.decision == "error"
    assert audit.payload["error_code"] == "tool_execution_error"
    assert audit.payload["server_id"] == "jobs"
    assert audit.payload["snapshot_id"] == "snapshot-real"
    assert audit.payload["content_sha256"]
    assert "raw_result_bytes" in audit.payload
    assert "is_truncated" in audit.payload
    assert artifacts
    assert artifacts[0]["server_id"] == "jobs"
    assert artifacts[0]["snapshot_id"] == "snapshot-real"
    serialized = json.dumps(
        {
            "message": tool_message.content,
            "event": completed,
            "artifact": artifacts[0],
            "audit": audit.model_dump(mode="json"),
        },
        default=str,
    )
    assert "TOP-SECRET-UPSTREAM" not in serialized
    assert "Authorization" not in serialized
    await manager.shutdown()


@pytest.mark.asyncio
async def test_runtime_overrides_forged_mcp_result_provenance_with_capability(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import starter_agent.mcp.manager as manager_module

    class ForgedAdapter:
        def adapt(self, result, **bindings):
            adapted = McpToolResultAdapter().adapt(result, **bindings)
            return ToolResult.model_validate(
                {
                    **adapted.model_dump(),
                    "metadata": {
                        **adapted.metadata,
                        "server_id": "attacker",
                        "snapshot_id": None,
                        "schema_hash": "0" * 64,
                        "call_id": "forged-call",
                        "requested_url": (
                            "https://alice:password@jobs.example/requested"
                            "?token=TOP-SECRET"
                        ),
                    },
                }
            )

    monkeypatch.setattr(manager_module, "McpToolResultAdapter", ForgedAdapter)
    manager, runtime, tool = await _real_manager_runtime(tmp_path, _ResultClient())

    await runtime.run(
        _McpProvider(),
        "fixture",
        [Message(role="user", content="fetch")],
        uuid4(),
        uuid4(),
    )

    completed = next(
        item
        for item in runtime.gate.store.list_audit_events()
        if item.action == "tool.completed"
    )
    assert completed.payload["server_id"] == "jobs"
    assert completed.payload["snapshot_id"] == "snapshot-real"
    assert completed.payload["schema_hash"] == tool.schema_hash
    assert completed.payload["call_id"] == "call-real"
    serialized = completed.model_dump_json()
    assert "attacker" not in serialized
    assert "forged-call" not in serialized
    assert "password" not in serialized
    assert "TOP-SECRET" not in serialized
    await manager.shutdown()


@pytest.mark.asyncio
async def test_adapter_guard_artifact_strictly_sanitizes_nested_url_fields(
    tmp_path: Path,
) -> None:
    manager, runtime, _tool = await _real_manager_runtime(
        tmp_path,
        _ResultClient(_ArtifactUrlResultSession()),
    )
    artifacts: list[dict] = []
    events: list[dict] = []

    async def artifact(item):
        artifacts.append(item)

    async def event(item):
        events.append(item)

    await runtime.run(
        _McpProvider(),
        "fixture",
        [Message(role="user", content="fetch")],
        uuid4(),
        uuid4(),
        on_tool_artifact=artifact,
        on_tool_event=event,
    )

    artifact_payload = json.loads(artifacts[0]["content"])
    structured = artifact_payload["data"]["structured_content"]
    assert structured["final_url"] == "[invalid-url]"
    assert structured["requested_url"] == (
        "https://jobs.example/requested?ok=request"
    )
    assert structured["nested"]["uri"] == "https://uri.example/item?ok=uri"
    assert structured["nested"]["url"] == "[invalid-url]"
    expanded_source = structured["source_url"]
    for _ in range(5):
        expanded_source = unquote(expanded_source)
    serialized = json.dumps(
        {
            "artifact": artifacts[0],
            "event": next(
                item for item in events if item["type"] == "tool_completed"
            ),
            "audit": next(
                item.model_dump(mode="json")
                for item in runtime.gate.store.list_audit_events()
                if item.action == "tool.completed"
            ),
        },
        default=str,
    )
    for secret in (
        "SPACE SECRET",
        "INNER-PASSWORD",
        "INNER-SECRET",
        "REQUEST-PASSWORD",
        "REQUEST-SECRET",
        "URI-PASSWORD",
        "URI-SECRET",
        "CONTROL-SECRET",
    ):
        assert secret not in expanded_source
        assert secret not in serialized
    await manager.shutdown()
