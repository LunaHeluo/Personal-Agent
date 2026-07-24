from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from starter_agent.capabilities.models import Server, Snapshot, Tool, canonical_json_sha256
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent import capabilities
from starter_agent.tools.registry import ToolRegistry


def _server(*, enabled: bool = True, connected: bool = True) -> Server:
    return Server(
        id="browser",
        name="browser",
        config_source="tests/mcp.json",
        config_hash="a" * 64,
        enabled=enabled,
        connection_state="ready" if connected else "disconnected",
    )


def _tool(name: str, marker: int = 0) -> Tool:
    schema = {
        "type": "object",
        "properties": {"marker": {"const": marker}},
        "additionalProperties": False,
    }
    return Tool(
        snapshot_id=f"browser-snapshot-{marker}",
        server_id="browser",
        upstream_name=name,
        model_alias=f"mcp__browser__{name}",
        description=f"private description {marker}",
        input_schema=schema,
        schema_hash=canonical_json_sha256(schema),
        enabled=True,
        review_state="approved",
        reviewed_at=datetime.now(UTC),
    )


def _snapshot(marker: int = 0) -> Snapshot:
    return Snapshot(
        id=f"browser-snapshot-{marker}",
        server_id="browser",
        version=marker + 1,
        schema_hash="b" * 64,
        discovered_at=datetime.now(UTC),
        active=True,
        tool_count=2,
    )


def test_capabilities_package_preserves_model_and_registry_exports() -> None:
    expected = {
        "AuditEvent",
        "Confirmation",
        "ExecutionPermit",
        "LightweightCapabilityCatalog",
        "ModelToolSnapshot",
        "PolicyRule",
        "Prompt",
        "Resource",
        "Server",
        "SkillRecord",
        "Snapshot",
        "Tool",
        "UnifiedToolRegistry",
    }
    assert set(capabilities.__all__) == expected
    assert {name: getattr(capabilities, name) for name in expected}


def test_registry_keeps_builtin_tools_and_publishes_atomic_revisions() -> None:
    registry = UnifiedToolRegistry(ToolRegistry(["get_current_time"]))

    builtin = registry.model_snapshot()
    assert [item["function"]["name"] for item in builtin.tools] == [
        "get_current_time"
    ]

    registry.refresh_server(
        _server(),
        [_tool("one"), _tool("two")],
        snapshot=_snapshot(),
    )
    first = registry.model_snapshot()
    assert first.context_revision == builtin.context_revision + 1

    def publish(marker: int) -> None:
        registry.refresh_server(
            _server(),
            [_tool("one", marker), _tool("two", marker)],
            snapshot=_snapshot(marker),
        )

    def read_markers() -> set[int]:
        seen: set[int] = set()
        for _ in range(300):
            snapshot = registry.model_snapshot()
            markers = {
                definition["function"]["parameters"]["properties"]["marker"][
                    "const"
                ]
                for definition in snapshot.tools
                if definition["function"]["name"].startswith("mcp__")
            }
            assert len(markers) == 1
            seen.update(markers)
        return seen

    with ThreadPoolExecutor(max_workers=5) as pool:
        writes = pool.submit(lambda: [publish(marker) for marker in range(1, 80)])
        reads = [pool.submit(read_markers) for _ in range(4)]
        writes.result()
        for result in reads:
            result.result()

    with pytest.raises(TypeError):
        first.tools[1]["function"]["parameters"]["properties"]["marker"][
            "const"
        ] = 999


def test_refresh_accepts_active_snapshot_reference_without_copying_adapter() -> None:
    registry = UnifiedToolRegistry(ToolRegistry([]))
    adapter = object()
    tool = _tool("read_page")
    snapshot = Snapshot(
        id=tool.snapshot_id,
        server_id="browser",
        version=1,
        schema_hash="b" * 64,
        discovered_at=datetime.now(UTC),
        active=True,
        tool_count=1,
    )

    registry.refresh_server(
        _server(),
        [tool],
        snapshot=snapshot,
        adapters={tool.model_alias: adapter},
    )

    assert registry.adapter_for(tool.model_alias) is adapter
    assert registry.model_snapshot().tools[0]["function"]["name"] == tool.model_alias
