from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.capabilities.registry import ModelToolSnapshot


_DELEGATION_TOOL = "delegate_task"


@dataclass(frozen=True, slots=True)
class EffectiveToolView:
    """Immutable model-facing subset backed by the shared runtime registry."""

    source: Any
    names: tuple[str, ...]
    snapshot: ModelToolSnapshot
    view_hash: str

    @property
    def context_revision(self) -> int:
        return self.snapshot.context_revision

    def model_snapshot(self) -> ModelToolSnapshot:
        return self.snapshot

    def schemas(self) -> list[dict[str, Any]]:
        return self.snapshot.provider_tools()

    def list(self) -> list[Any]:
        return [tool for name in self.names if (tool := self.get(name)) is not None]

    def get(self, name: str) -> Any | None:
        if name not in self.names:
            return None
        getter = getattr(self.source, "get", None)
        return None if not callable(getter) else getter(name)

    def adapter_for(self, name: str) -> Any | None:
        if name not in self.names:
            return None
        resolver = getattr(self.source, "adapter_for", None)
        return None if not callable(resolver) else resolver(name)

    def resolve_execution(self, name: str) -> Any | None:
        if name not in self.names:
            return None
        resolver = getattr(self.source, "resolve_execution", None)
        return None if not callable(resolver) else resolver(name)


def build_effective_tool_view(
    source: Any,
    *,
    registry_allowed: Iterable[str],
    contract_requested: Iterable[str],
    scenario_allowed: Iterable[str],
    policy_allowed: Iterable[str],
) -> EffectiveToolView:
    resolve = getattr(source, "resolve_execution", None)
    if not callable(resolve):
        requested: set[str] = set()
    else:
        def canonicalize(items: Iterable[str]) -> set[str]:
            aliases: set[str] = set()
            for name in items:
                capability = resolve(name)
                if capability is not None:
                    aliases.add(capability.model_alias)
            return aliases

        requested = (
            canonicalize(registry_allowed)
            & canonicalize(contract_requested)
            & canonicalize(scenario_allowed)
            & canonicalize(policy_allowed)
        )
    requested.discard(_DELEGATION_TOOL)
    names = tuple(
        sorted(
            name
            for name in requested
            if (capability := resolve(name)) is not None
            and capability.enabled
            and capability.connected
            and capability.review_state == "approved"
        )
    )
    source_snapshot = source.model_snapshot()
    allowed = set(names)
    definitions = tuple(
        item
        for item in source_snapshot.tools
        if item.get("function", {}).get("name") in allowed
    )
    snapshot = ModelToolSnapshot(
        context_revision=source_snapshot.context_revision,
        tools=definitions,
    )
    view_hash = canonical_json_sha256(
        {
            "context_revision": snapshot.context_revision,
            "names": names,
            "schemas": snapshot.provider_tools(),
        }
    )
    return EffectiveToolView(source, names, snapshot, view_hash)
