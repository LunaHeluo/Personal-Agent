from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Any, Awaitable, Callable, Iterable

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from mcp.types import PaginatedRequestParams

from starter_agent.capabilities.models import (
    Prompt,
    Resource,
    Snapshot,
    Tool,
    canonical_json_sha256,
)
from starter_agent.capabilities.store import CapabilityStore


_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class DiscoveryError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CapabilityBundle:
    snapshot: Snapshot
    tools: tuple[Tool, ...]
    resources: tuple[Resource, ...]
    prompts: tuple[Prompt, ...]
    canonical_hash: str


async def _collect_pages(
    request: Callable[..., Awaitable[Any]],
    field: str,
) -> list[Any]:
    cursor: str | None = None
    seen: set[str] = set()
    items: list[Any] = []
    while True:
        try:
            result = await request(
                params=(
                    None
                    if cursor is None
                    else PaginatedRequestParams(cursor=cursor)
                )
            )
        except DiscoveryError:
            raise
        except BaseException as exc:
            raise DiscoveryError("discovery_page_failed") from exc
        page = getattr(result, field, None)
        if not isinstance(page, list):
            raise DiscoveryError("invalid_discovery_page")
        items.extend(page)
        next_cursor = getattr(result, "nextCursor", None)
        if next_cursor is None:
            return items
        if (
            not isinstance(next_cursor, str)
            or not next_cursor
            or next_cursor in seen
        ):
            raise DiscoveryError("invalid_discovery_cursor")
        seen.add(next_cursor)
        cursor = next_cursor


def _require_safe_name(value: Any, *, code: str) -> str:
    if not isinstance(value, str) or not _SAFE_NAME.fullmatch(value):
        raise DiscoveryError(code)
    return value


def _model_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    try:
        return value.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise DiscoveryError("invalid_capability_metadata") from exc


def _unique_names(items: Iterable[Any], *, code: str) -> None:
    seen: set[str] = set()
    for item in items:
        name = getattr(item, "name", None)
        if name in seen:
            raise DiscoveryError(code)
        seen.add(name)


async def collect_capabilities(
    session: Any,
    *,
    server_id: str,
    snapshot_id: str,
    version: int,
    reserved_model_names: Iterable[str] = (),
) -> CapabilityBundle:
    _require_safe_name(server_id, code="invalid_server_name")
    tools_page = await _collect_pages(session.list_tools, "tools")
    resources_page = await _collect_pages(session.list_resources, "resources")
    templates_page = await _collect_pages(
        session.list_resource_templates,
        "resourceTemplates",
    )
    prompts_page = await _collect_pages(session.list_prompts, "prompts")
    _unique_names(tools_page, code="duplicate_tool_name")
    _unique_names(
        (*resources_page, *templates_page),
        code="duplicate_resource_name",
    )
    _unique_names(prompts_page, code="duplicate_prompt_name")

    reserved = set(reserved_model_names)
    tools: list[Tool] = []
    for upstream in tools_page:
        name = _require_safe_name(
            getattr(upstream, "name", None),
            code="invalid_tool_name",
        )
        alias = f"mcp__{server_id}__{name}"
        if alias in reserved:
            raise DiscoveryError("model_alias_collision")
        reserved.add(alias)
        schema = getattr(upstream, "inputSchema", None)
        if not isinstance(schema, dict):
            raise DiscoveryError("invalid_tool_schema")
        try:
            validator_for(schema).check_schema(schema)
        except SchemaError as exc:
            raise DiscoveryError("invalid_tool_schema") from exc
        annotations = _model_dict(getattr(upstream, "annotations", None))
        title = getattr(upstream, "title", None) or annotations.get("title")
        tools.append(
            Tool(
                snapshot_id=snapshot_id,
                server_id=server_id,
                upstream_name=name,
                model_alias=alias,
                title=title,
                description=getattr(upstream, "description", None) or "",
                input_schema=schema,
                schema_hash=canonical_json_sha256(schema),
                metadata={"annotations": annotations},
                review_state="unreviewed",
                enabled=False,
            )
        )

    resources: list[Resource] = []
    for upstream in resources_page:
        name = _require_safe_name(
            getattr(upstream, "name", None),
            code="invalid_resource_name",
        )
        resources.append(
            Resource(
                snapshot_id=snapshot_id,
                server_id=server_id,
                name=name,
                uri=str(upstream.uri),
                description=getattr(upstream, "description", None) or "",
                mime_type=getattr(upstream, "mimeType", None),
                metadata={
                    "capability_kind": "resource",
                    "title": getattr(upstream, "title", None),
                    "annotations": _model_dict(
                        getattr(upstream, "annotations", None)
                    ),
                },
                enabled=False,
            )
        )
    for upstream in templates_page:
        name = _require_safe_name(
            getattr(upstream, "name", None),
            code="invalid_resource_name",
        )
        uri_template = getattr(upstream, "uriTemplate", None)
        if not isinstance(uri_template, str) or not uri_template:
            raise DiscoveryError("invalid_resource_template")
        resources.append(
            Resource(
                snapshot_id=snapshot_id,
                server_id=server_id,
                name=name,
                uri_template=uri_template,
                description=getattr(upstream, "description", None) or "",
                mime_type=getattr(upstream, "mimeType", None),
                metadata={
                    "capability_kind": "resource_template",
                    "title": getattr(upstream, "title", None),
                    "annotations": _model_dict(
                        getattr(upstream, "annotations", None)
                    ),
                },
                enabled=False,
            )
        )

    prompts: list[Prompt] = []
    for upstream in prompts_page:
        name = _require_safe_name(
            getattr(upstream, "name", None),
            code="invalid_prompt_name",
        )
        prompts.append(
            Prompt(
                snapshot_id=snapshot_id,
                server_id=server_id,
                name=name,
                description=getattr(upstream, "description", None) or "",
                arguments=tuple(
                    _model_dict(argument)
                    for argument in (getattr(upstream, "arguments", None) or ())
                ),
                metadata={"title": getattr(upstream, "title", None)},
                enabled=False,
            )
        )

    tools.sort(key=lambda item: item.upstream_name)
    resources.sort(key=lambda item: item.name)
    prompts.sort(key=lambda item: item.name)
    canonical = {
        "tools": [
            item.model_dump(
                mode="json", exclude={"snapshot_id", "server_id"}
            )
            for item in tools
        ],
        "resources": [
            item.model_dump(
                mode="json", exclude={"snapshot_id", "server_id"}
            )
            for item in resources
        ],
        "prompts": [
            item.model_dump(
                mode="json", exclude={"snapshot_id", "server_id"}
            )
            for item in prompts
        ],
    }
    canonical_hash = canonical_json_sha256(canonical)
    snapshot = Snapshot(
        id=snapshot_id,
        server_id=server_id,
        version=version,
        schema_hash=canonical_hash,
        discovered_at=datetime.now(UTC),
        tool_count=len(tools),
        resource_count=len(resources),
        prompt_count=len(prompts),
    )
    return CapabilityBundle(
        snapshot=snapshot,
        tools=tuple(tools),
        resources=tuple(resources),
        prompts=tuple(prompts),
        canonical_hash=canonical_hash,
    )


async def discover_and_activate(
    store: CapabilityStore,
    session: Any,
    *,
    server_id: str,
    reserved_model_names: Iterable[str] = (),
) -> Snapshot:
    snapshot = await discover_candidate(
        store,
        session,
        server_id=server_id,
        reserved_model_names=reserved_model_names,
    )
    return store.activate_snapshot(server_id, snapshot.id)


async def discover_candidate(
    store: CapabilityStore,
    session: Any,
    *,
    server_id: str,
    reserved_model_names: Iterable[str] = (),
) -> Snapshot:
    """Validate and persist an inactive snapshot for a candidate client."""
    version = store.next_snapshot_version(server_id)
    bundle = await collect_capabilities(
        session,
        server_id=server_id,
        snapshot_id=f"{server_id}-snapshot-{version}",
        version=version,
        reserved_model_names=reserved_model_names,
    )
    store.create_snapshot(
        bundle.snapshot,
        tools=bundle.tools,
        resources=bundle.resources,
        prompts=bundle.prompts,
    )
    return bundle.snapshot
