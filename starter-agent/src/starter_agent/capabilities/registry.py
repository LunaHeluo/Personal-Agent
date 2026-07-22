from __future__ import annotations

from dataclasses import dataclass, replace
from threading import Lock
from typing import Any, Iterable, Literal, Mapping

from starter_agent.capabilities.models import FrozenJsonDict, Server, Snapshot
from starter_agent.capabilities.models import Tool as McpTool
from starter_agent.tools.base import Tool as BuiltinTool
from starter_agent.tools.registry import ToolRegistry


CapabilityType = Literal["builtin", "mcp"]


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class CapabilitySummary:
    name: str
    server: str
    type: CapabilityType
    enabled: bool
    review: str
    callable: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "server": self.server,
            "type": self.type,
            "enabled": self.enabled,
            "review": self.review,
            "callable": self.callable,
        }


@dataclass(frozen=True, slots=True)
class LightweightCapabilityCatalog:
    context_revision: int
    capabilities: tuple[CapabilitySummary, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "context_revision": self.context_revision,
            "capabilities": [item.as_dict() for item in self.capabilities],
        }


@dataclass(frozen=True, slots=True)
class ModelToolSnapshot:
    context_revision: int
    tools: tuple[FrozenJsonDict, ...]

    def provider_tools(self) -> list[dict[str, Any]]:
        return [_thaw(item) for item in self.tools]


@dataclass(frozen=True, slots=True)
class _BuiltinRecord:
    tool: BuiltinTool
    enabled: bool = True
    policy_allowed: bool = True


@dataclass(frozen=True, slots=True)
class _McpServerRecord:
    server: Server
    tools: tuple[McpTool, ...]
    snapshot: Snapshot | None = None
    adapters: tuple[tuple[str, object], ...] = ()
    policy_exposure: tuple[tuple[str, bool], ...] = ()


@dataclass(frozen=True, slots=True)
class _RegistryState:
    context_revision: int
    builtins: tuple[_BuiltinRecord, ...]
    servers: tuple[_McpServerRecord, ...]
    catalog: LightweightCapabilityCatalog
    model_snapshot: ModelToolSnapshot


class UnifiedToolRegistry:
    """Publish immutable, atomically replaceable views of builtin and MCP tools."""

    def __init__(
        self,
        builtins: ToolRegistry,
        *,
        allowed_risk_levels: Iterable[str] | None = None,
    ) -> None:
        self._builtins = builtins
        self._write_lock = Lock()
        allowed = None if allowed_risk_levels is None else set(allowed_risk_levels)
        builtin_records = tuple(
            _BuiltinRecord(
                tool=tool,
                policy_allowed=allowed is None or tool.risk_level in allowed,
            )
            for tool in builtins.list()
        )
        self._state = self._build_state(0, builtin_records, ())

    @property
    def email_manager(self):
        return self._builtins.email_manager

    @property
    def context_revision(self) -> int:
        return self._state.context_revision

    def lightweight_catalog(self) -> LightweightCapabilityCatalog:
        return self._state.catalog

    def model_snapshot(self) -> ModelToolSnapshot:
        return self._state.model_snapshot

    def schemas(self) -> list[dict[str, Any]]:
        return self.model_snapshot().provider_tools()

    def list(self) -> list[BuiltinTool]:
        return self._builtins.list()

    def get(self, name: str) -> BuiltinTool | None:
        state = self._state
        for record in state.builtins:
            if record.tool.name == name:
                return record.tool if record.enabled and record.policy_allowed else None
        return None

    def adapter_for(self, model_alias: str) -> object | None:
        state = self._state
        for server in state.servers:
            for alias, adapter in server.adapters:
                if alias == model_alias:
                    return adapter
        return None

    def refresh_server(
        self,
        server: Server,
        tools: Iterable[McpTool],
        *,
        snapshot: Snapshot | None = None,
        adapters: Mapping[str, object] | None = None,
    ) -> ModelToolSnapshot:
        tool_items = tuple(tools)
        if any(tool.server_id != server.id for tool in tool_items):
            raise ValueError("MCP tool belongs to a different server")
        with self._write_lock:
            current = self._state
            previous = next(
                (item for item in current.servers if item.server.id == server.id),
                None,
            )
            record = _McpServerRecord(
                server=server,
                tools=tool_items,
                snapshot=snapshot,
                adapters=tuple((adapters or {}).items()),
                policy_exposure=(() if previous is None else previous.policy_exposure),
            )
            servers = tuple(
                record if item.server.id == server.id else item
                for item in current.servers
            )
            if previous is None:
                servers = (*servers, record)
            return self._publish(current.builtins, servers)

    publish_server = refresh_server

    def set_server_enabled(self, server_id: str, enabled: bool) -> ModelToolSnapshot:
        return self._update_server(
            server_id,
            lambda item: replace(
                item,
                server=item.server.model_copy(update={"enabled": enabled}),
            ),
        )

    def set_server_connected(
        self, server_id: str, connected: bool
    ) -> ModelToolSnapshot:
        return self._update_server(
            server_id,
            lambda item: replace(
                item,
                server=item.server.model_copy(
                    update={"connection_state": "ready" if connected else "closed"}
                ),
            ),
        )

    def set_tool_enabled(self, name: str, enabled: bool) -> ModelToolSnapshot:
        with self._write_lock:
            current = self._state
            builtins = tuple(
                replace(item, enabled=enabled) if item.tool.name == name else item
                for item in current.builtins
            )
            found = any(item.tool.name == name for item in current.builtins)
            servers: list[_McpServerRecord] = []
            for server in current.servers:
                tools = tuple(
                    tool.model_copy(update={"enabled": enabled})
                    if tool.model_alias == name
                    else tool
                    for tool in server.tools
                )
                found = found or any(tool.model_alias == name for tool in server.tools)
                servers.append(replace(server, tools=tools))
            if not found:
                raise KeyError(name)
            return self._publish(builtins, tuple(servers))

    def set_tool_review(self, name: str, review_state: str) -> ModelToolSnapshot:
        with self._write_lock:
            current = self._state
            found = False
            servers: list[_McpServerRecord] = []
            for server in current.servers:
                tools = tuple(
                    tool.model_copy(update={"review_state": review_state})
                    if tool.model_alias == name
                    else tool
                    for tool in server.tools
                )
                found = found or any(tool.model_alias == name for tool in server.tools)
                servers.append(replace(server, tools=tools))
            if not found:
                raise KeyError(name)
            return self._publish(current.builtins, tuple(servers))

    set_tool_review_state = set_tool_review

    def set_policy_exposure(self, name: str, allowed: bool) -> ModelToolSnapshot:
        with self._write_lock:
            current = self._state
            builtins = tuple(
                replace(item, policy_allowed=allowed)
                if item.tool.name == name
                else item
                for item in current.builtins
            )
            found = any(item.tool.name == name for item in current.builtins)
            servers: list[_McpServerRecord] = []
            for server in current.servers:
                exposure = dict(server.policy_exposure)
                if any(tool.model_alias == name for tool in server.tools):
                    exposure[name] = allowed
                    found = True
                servers.append(
                    replace(server, policy_exposure=tuple(exposure.items()))
                )
            if not found:
                raise KeyError(name)
            return self._publish(builtins, tuple(servers))

    def notify_policy_changed(self) -> ModelToolSnapshot:
        with self._write_lock:
            current = self._state
            return self._publish(current.builtins, current.servers)

    def refresh_from_manager(self, manager: object) -> ModelToolSnapshot:
        statuses = getattr(manager, "statuses", None)
        store = getattr(manager, "store", None)
        if not callable(statuses) or store is None:
            return self.model_snapshot()
        records: list[_McpServerRecord] = []
        for server in statuses().values():
            snapshot = store.get_active_snapshot(server.id)
            tools = () if snapshot is None else tuple(store.list_tools(snapshot.id))
            records.append(
                _McpServerRecord(server=server, tools=tools, snapshot=snapshot)
            )
        with self._write_lock:
            return self._publish(self._state.builtins, tuple(records))

    def _update_server(self, server_id: str, update) -> ModelToolSnapshot:
        with self._write_lock:
            current = self._state
            if not any(item.server.id == server_id for item in current.servers):
                raise KeyError(server_id)
            servers = tuple(
                update(item) if item.server.id == server_id else item
                for item in current.servers
            )
            return self._publish(current.builtins, servers)

    def _publish(
        self,
        builtins: tuple[_BuiltinRecord, ...],
        servers: tuple[_McpServerRecord, ...],
    ) -> ModelToolSnapshot:
        next_state = self._build_state(
            self._state.context_revision + 1,
            builtins,
            servers,
        )
        self._state = next_state
        return next_state.model_snapshot

    @staticmethod
    def _build_state(
        revision: int,
        builtins: tuple[_BuiltinRecord, ...],
        servers: tuple[_McpServerRecord, ...],
    ) -> _RegistryState:
        summaries: list[CapabilitySummary] = []
        definitions: list[FrozenJsonDict] = []
        for record in builtins:
            callable_now = record.enabled and record.policy_allowed
            summaries.append(
                CapabilitySummary(
                    name=record.tool.name,
                    server="builtin",
                    type="builtin",
                    enabled=record.enabled,
                    review="approved",
                    callable=callable_now,
                )
            )
            if callable_now:
                definitions.append(FrozenJsonDict(record.tool.schema()))
        for server_record in servers:
            server = server_record.server
            server_callable = server.enabled and server.connection_state == "ready"
            policy = dict(server_record.policy_exposure)
            for tool in server_record.tools:
                callable_now = (
                    server_callable
                    and tool.enabled
                    and tool.review_state == "approved"
                    and policy.get(tool.model_alias, True)
                )
                summaries.append(
                    CapabilitySummary(
                        name=tool.model_alias,
                        server=server.id,
                        type="mcp",
                        enabled=server.enabled and tool.enabled,
                        review=tool.review_state,
                        callable=callable_now,
                    )
                )
                if callable_now:
                    definitions.append(
                        FrozenJsonDict(
                            {
                                "type": "function",
                                "function": {
                                    "name": tool.model_alias,
                                    "description": tool.description,
                                    "parameters": tool.input_schema,
                                },
                            }
                        )
                    )
        catalog = LightweightCapabilityCatalog(revision, tuple(summaries))
        snapshot = ModelToolSnapshot(revision, tuple(definitions))
        return _RegistryState(revision, builtins, servers, catalog, snapshot)
