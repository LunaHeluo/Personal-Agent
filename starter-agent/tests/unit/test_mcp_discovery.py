from __future__ import annotations

import pytest
from mcp import types

from starter_agent.mcp.discovery import DiscoveryError, collect_capabilities


class _PagedSession:
    def __init__(self) -> None:
        self.cursors: dict[str, list[str | None]] = {
            "tools": [],
            "resources": [],
            "templates": [],
            "prompts": [],
        }

    async def list_tools(self, *, params=None):
        cursor = None if params is None else params.cursor
        self.cursors["tools"].append(cursor)
        if cursor is None:
            return types.ListToolsResult(
                tools=[
                    types.Tool(
                        name="search_jobs",
                        title="Search jobs",
                        description="Find matching roles",
                        inputSchema={
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                        annotations=types.ToolAnnotations(readOnlyHint=True),
                    )
                ],
                nextCursor="tools-2",
            )
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="read_job",
                    inputSchema={"type": "object", "properties": {}},
                )
            ]
        )

    async def list_resources(self, *, params=None):
        cursor = None if params is None else params.cursor
        self.cursors["resources"].append(cursor)
        return types.ListResourcesResult(
            resources=[
                types.Resource(
                    name="job-board",
                    title="Job board",
                    uri="jobs://board",
                    description="Current jobs",
                    mimeType="application/json",
                )
            ]
        )

    async def list_resource_templates(self, *, params=None):
        cursor = None if params is None else params.cursor
        self.cursors["templates"].append(cursor)
        return types.ListResourceTemplatesResult(
            resourceTemplates=[
                types.ResourceTemplate(
                    name="job-detail",
                    uriTemplate="jobs://detail/{job_id}",
                    description="One job",
                    mimeType="application/json",
                )
            ]
        )

    async def list_prompts(self, *, params=None):
        cursor = None if params is None else params.cursor
        self.cursors["prompts"].append(cursor)
        return types.ListPromptsResult(
            prompts=[
                types.Prompt(
                    name="compare-role",
                    description="Compare a role",
                    arguments=[
                        types.PromptArgument(
                            name="job_id", required=True
                        )
                    ],
                )
            ]
        )


@pytest.mark.asyncio
async def test_discovers_all_paginated_capabilities_and_rejects_alias_collision() -> None:
    session = _PagedSession()

    bundle = await collect_capabilities(
        session,
        server_id="alpha",
        snapshot_id="alpha-snapshot-1",
        version=1,
    )

    assert session.cursors == {
        "tools": [None, "tools-2"],
        "resources": [None],
        "templates": [None],
        "prompts": [None],
    }
    assert [tool.model_alias for tool in bundle.tools] == [
        "mcp__alpha__read_job",
        "mcp__alpha__search_jobs",
    ]
    assert all(
        tool.review_state == "unreviewed" and tool.enabled is False
        for tool in bundle.tools
    )
    template = next(
        resource
        for resource in bundle.resources
        if resource.name == "job-detail"
    )
    assert template.uri is None
    assert template.uri_template == "jobs://detail/{job_id}"
    assert template.metadata["capability_kind"] == "resource_template"
    assert bundle.snapshot.tool_count == 2
    assert bundle.snapshot.resource_count == 2
    assert bundle.snapshot.prompt_count == 1
    assert bundle.snapshot.schema_hash == bundle.canonical_hash

    with pytest.raises(DiscoveryError) as raised:
        await collect_capabilities(
            _PagedSession(),
            server_id="alpha",
            snapshot_id="alpha-snapshot-2",
            version=2,
            reserved_model_names={"mcp__alpha__search_jobs"},
        )
    assert raised.value.code == "model_alias_collision"
