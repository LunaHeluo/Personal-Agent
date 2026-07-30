from __future__ import annotations

import json
from datetime import UTC, datetime

from starter_agent.capabilities.models import (
    Server,
    Snapshot,
    Tool,
    canonical_json_sha256,
)
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.tools.registry import ToolRegistry


def _tool(
    *,
    enabled: bool,
    review_state: str,
    reviewed_at: datetime | None = None,
) -> Tool:
    schema = {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    }
    return Tool(
        snapshot_id="browser-snapshot-1",
        server_id="browser",
        upstream_name="read_page",
        model_alias="mcp__browser__read_page",
        description="Read the complete private page schema",
        input_schema=schema,
        schema_hash=canonical_json_sha256(schema),
        enabled=enabled,
        review_state=review_state,
        reviewed_at=reviewed_at,
    )


def _server(*, enabled: bool = True, connected: bool = True) -> Server:
    return Server(
        id="browser",
        name="browser",
        config_source="tests/mcp.json",
        config_hash="a" * 64,
        enabled=enabled,
        connection_state="ready" if connected else "closed",
    )


def _snapshot(*, stale: bool = False) -> Snapshot:
    return Snapshot(
        id="browser-snapshot-1",
        server_id="browser",
        version=1,
        schema_hash="b" * 64,
        discovered_at=datetime.now(UTC),
        active=True,
        stale=stale,
        tool_count=1,
    )


def test_lightweight_catalog_never_contains_descriptions_or_schemas() -> None:
    registry = UnifiedToolRegistry(ToolRegistry(["get_current_time"]))
    registry.refresh_server(
        _server(enabled=False, connected=False),
        [_tool(enabled=False, review_state="unreviewed")],
        snapshot=_snapshot(),
    )

    payload = registry.lightweight_catalog().as_dict()
    serialized = json.dumps(payload)

    assert payload["capabilities"][-1] == {
        "name": "mcp__browser__read_page",
        "server": "browser",
        "type": "mcp",
        "enabled": False,
        "review": "unreviewed",
        "callable": False,
    }
    assert "description" not in serialized
    assert "schema" not in serialized
    assert "properties" not in serialized


def test_only_connected_enabled_and_approved_mcp_tools_are_exposed() -> None:
    registry = UnifiedToolRegistry(ToolRegistry([]))
    registry.refresh_server(
        _server(),
        [_tool(enabled=True, review_state="unreviewed")],
        snapshot=_snapshot(),
    )
    assert registry.model_snapshot().tools == ()


    registry.set_tool_review("mcp__browser__read_page", "approved")
    assert registry.model_snapshot().tools == ()

    registry.refresh_server(
        _server(),
        [
            _tool(
                enabled=True,
                review_state="approved",
                reviewed_at=datetime.now(UTC),
            )
        ],
        snapshot=_snapshot(),
    )
    approved_revision = registry.context_revision
    assert registry.model_snapshot().tools[0]["function"]["description"].startswith(
        "Read the complete"
    )

    registry.set_tool_enabled("mcp__browser__read_page", False)
    assert registry.context_revision == approved_revision + 1
    assert registry.model_snapshot().tools == ()

    registry.set_tool_enabled("mcp__browser__read_page", True)
    registry.set_server_enabled("browser", False)
    assert registry.model_snapshot().tools == ()

    registry.set_server_enabled("browser", True)
    registry.set_server_connected("browser", False)
    assert registry.model_snapshot().tools == ()

    registry.set_server_connected("browser", True)
    registry.set_policy_exposure("mcp__browser__read_page", False)
    assert registry.model_snapshot().tools == ()

    registry.set_policy_exposure("mcp__browser__read_page", True)
    assert len(registry.model_snapshot().tools) == 1

    registry.refresh_server(
        _server(),
        [
            _tool(
                enabled=True,
                review_state="approved",
                reviewed_at=datetime.now(UTC),
            )
        ],
        snapshot=_snapshot(stale=True),
    )
    assert registry.model_snapshot().tools == ()


def test_disabled_resume_evidence_tool_keeps_only_lightweight_name_until_reenabled() -> None:
    class _KnowledgeService:
        class _Scope:
            user_id = "user"
            project_id = "project"

        scope = _Scope()

        def retrieve(self, *_args, **_kwargs):
            return []

    registry = UnifiedToolRegistry(
        ToolRegistry(
            ["retrieve_resume_evidence"],
            knowledge_service=_KnowledgeService(),
        )
    )
    first_snapshot = registry.model_snapshot().provider_tools()
    assert first_snapshot[0]["function"]["name"] == "retrieve_resume_evidence"
    assert first_snapshot[0]["function"]["description"]
    assert first_snapshot[0]["function"]["parameters"]["required"] == ["query"]

    registry.set_tool_enabled("retrieve_resume_evidence", False)
    lightweight = registry.lightweight_catalog().as_dict()
    serialized_catalog = json.dumps(lightweight, ensure_ascii=False)

    assert lightweight["capabilities"][0] == {
        "name": "retrieve_resume_evidence",
        "server": "builtin",
        "type": "builtin",
        "enabled": False,
        "review": "approved",
        "callable": False,
    }
    assert registry.model_snapshot().provider_tools() == []
    assert "Retrieve quoted evidence" not in serialized_catalog
    assert "parameters" not in serialized_catalog
    assert "query" not in serialized_catalog

    registry.set_tool_enabled("retrieve_resume_evidence", True)
    restored = registry.model_snapshot().provider_tools()

    assert restored == first_snapshot
