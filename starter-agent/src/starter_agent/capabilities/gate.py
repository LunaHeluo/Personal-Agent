from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any, Awaitable, Callable, Literal, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from jsonschema import SchemaError, ValidationError, validate
from pydantic import Field, computed_field

from starter_agent.capabilities.models import (
    BoundedJsonObject,
    CapabilityModel,
    ExecutionPermit,
    Tool,
    canonical_json_sha256,
)
from starter_agent.capabilities.policy import (
    BrowserScopePolicy,
    PolicyDecision,
    PolicyRequest,
    ScopeDenied,
    ToolPolicy,
    classify_tool,
    validate_serpapi_payload,
)
from starter_agent.capabilities.store import (
    CapabilityStore,
    ExecutionPermitError,
)
from starter_agent.tools.adapters.safe_web_fetcher import sanitize_public_url


class ToolCallRequest(CapabilityModel):
    caller: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=160)
    turn_id: str = Field(min_length=1, max_length=160)
    call_id: str = Field(min_length=1, max_length=160)
    server_id: str = Field(min_length=1, max_length=160)
    tool_name: str = Field(min_length=1, max_length=200)
    snapshot_id: str = Field(min_length=1, max_length=160)
    schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    arguments: BoundedJsonObject = Field(default_factory=dict)
    role: str = Field(default="user", min_length=1, max_length=100)
    data_classes: tuple[str, ...] = Field(default_factory=tuple, max_length=100)

    @computed_field
    @property
    def arguments_hash(self) -> str:
        return canonical_json_sha256(self.arguments)

    @computed_field
    @property
    def request_hash(self) -> str:
        return canonical_json_sha256(
            {
                "caller": self.caller,
                "session_id": self.session_id,
                "turn_id": self.turn_id,
                "call_id": self.call_id,
                "server_id": self.server_id,
                "tool_name": self.tool_name,
                "snapshot_id": self.snapshot_id,
                "schema_hash": self.schema_hash,
                "arguments_hash": self.arguments_hash,
            }
        )


@dataclass(frozen=True, slots=True)
class GateDecision:
    outcome: Literal["allow", "require_confirmation", "deny"]
    reason_code: str
    arguments_summary: Mapping[str, Any]
    destination_summary: str
    permit: ExecutionPermit | None = None


class PreToolCallGate:
    def __init__(
        self,
        store: CapabilityStore,
        *,
        policy: ToolPolicy | None = None,
        browser_policy: BrowserScopePolicy | None = None,
        permit_ttl_seconds: float = 30,
        max_outbound_bytes: int = 64_000,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if permit_ttl_seconds <= 0 or permit_ttl_seconds > 300:
            raise ValueError("permit_ttl_seconds must be within (0, 300]")
        self.store = store
        self.policy = policy or ToolPolicy()
        self.browser_policy = browser_policy or BrowserScopePolicy()
        self.permit_ttl_seconds = permit_ttl_seconds
        self.max_outbound_bytes = max_outbound_bytes
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = Lock()
        self._decisions: dict[tuple[str, str, str], tuple[str, GateDecision]] = {}

    async def evaluate(self, request: ToolCallRequest) -> GateDecision:
        denial = self._resolve_tool(request)
        if isinstance(denial, GateDecision):
            return denial
        tool = denial
        try:
            schema = json.loads(json.dumps(tool.input_schema))
            validate(instance=dict(request.arguments), schema=schema)
        except (ValidationError, SchemaError):
            return self._decision("deny", "invalid_arguments", request)

        action = classify_tool(tool.metadata, tool.risk_level)
        scheme: str | None = None
        domain: str | None = None
        destination = "none"
        outbound_size = len(
            json.dumps(request.arguments, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        if _is_browser_tool(tool):
            try:
                self.browser_policy.validate_outbound(
                    request.data_classes,
                    outbound_size,
                    max_bytes=self.max_outbound_bytes,
                )
                urls = _url_arguments(request.arguments)
                if not urls:
                    raise ScopeDenied("unsafe_url")
                target = await self.browser_policy.validate_url(urls[0])
            except ScopeDenied as exc:
                return self._decision("deny", exc.code, request)
            scheme = target.scheme
            domain = target.hostname
            destination = target.hostname
        elif request.server_id.casefold() == "serpapi":
            try:
                validate_serpapi_payload(request.arguments, request.data_classes)
            except ScopeDenied as exc:
                return self._decision("deny", exc.code, request)
            destination = "serpapi"
        elif outbound_size > self.max_outbound_bytes:
            return self._decision("deny", "outbound_budget", request)

        rules = self.store.list_policy_rules(request.server_id, request.tool_name)
        policy_decision = self.policy.evaluate(
            PolicyRequest(
                server_id=request.server_id,
                tool_name=request.tool_name,
                action=action,
                scheme=scheme,
                domain=domain,
                arguments=request.arguments,
                role=request.role,
                data_classes=request.data_classes,
                reviewed=tool.review_state == "approved",
                enabled=tool.enabled,
            ),
            rules,
        )
        return self._finalize(request, policy_decision, destination, rules)

    def _resolve_tool(self, request: ToolCallRequest) -> Tool | GateDecision:
        server = self.store.get_server(request.server_id)
        if server is None:
            return self._decision("deny", "server_not_found", request)
        if not server.enabled:
            return self._decision("deny", "server_disabled", request)
        if server.connection_state != "ready":
            return self._decision("deny", "server_not_connected", request)
        snapshot = self.store.get_active_snapshot(request.server_id)
        if snapshot is None:
            return self._decision("deny", "snapshot_missing", request)
        if snapshot.stale:
            return self._decision("deny", "stale_snapshot", request)
        if snapshot.id != request.snapshot_id:
            return self._decision("deny", "snapshot_mismatch", request)
        tool = next(
            (
                item
                for item in self.store.list_tools(snapshot.id)
                if request.tool_name in {item.upstream_name, item.model_alias}
            ),
            None,
        )
        if tool is None:
            return self._decision("deny", "tool_not_found", request)
        if not tool.enabled:
            return self._decision("deny", "tool_disabled", request)
        if tool.review_state == "rejected":
            return self._decision("deny", "tool_rejected", request)
        if tool.schema_hash != request.schema_hash:
            return self._decision("deny", "schema_hash_mismatch", request)
        return tool

    def _finalize(
        self,
        request: ToolCallRequest,
        policy: PolicyDecision,
        destination: str,
        rules,
    ) -> GateDecision:
        summary = _safe_arguments_summary(request.arguments)
        if policy.outcome != "allow":
            return GateDecision(
                policy.outcome,
                policy.reason_code,
                summary,
                destination,
            )
        key = (request.session_id, request.turn_id, request.call_id)
        with self._lock:
            prior = self._decisions.get(key)
            if prior is not None:
                prior_hash, decision = prior
                if prior_hash == request.request_hash:
                    return decision
                return GateDecision("deny", "duplicate_call", summary, destination)
            permit = ExecutionPermit(
                id=f"permit-{uuid4().hex}",
                request_hash=request.request_hash,
                policy_revision=max((rule.revision for rule in rules), default=0),
                expires_at=self._now() + timedelta(seconds=self.permit_ttl_seconds),
                caller=request.caller,
                session_id=request.session_id,
                turn_id=request.turn_id,
                server_id=request.server_id,
                tool_name=request.tool_name,
                snapshot_id=request.snapshot_id,
                schema_hash=request.schema_hash,
                arguments_hash=request.arguments_hash,
                decision="allow",
            )
            self.store.create_execution_permit(permit)
            decision = GateDecision(
                "allow", policy.reason_code, summary, destination, permit
            )
            self._decisions[key] = (request.request_hash, decision)
            return decision

    @staticmethod
    def _decision(
        outcome: Literal["allow", "require_confirmation", "deny"],
        reason: str,
        request: ToolCallRequest,
    ) -> GateDecision:
        return GateDecision(
            outcome,
            reason,
            _safe_arguments_summary(request.arguments),
            "none",
        )


class ToolExecutionDenied(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


Invoker = Callable[[Mapping[str, Any]], Any | Awaitable[Any]]


class UnifiedToolExecutor:
    def __init__(
        self,
        store: CapabilityStore,
        *,
        builtin_invoker: Invoker | None = None,
        mcp_invoker: Invoker | None = None,
    ) -> None:
        self.store = store
        self.builtin_invoker = builtin_invoker
        self.mcp_invoker = mcp_invoker

    async def execute(
        self,
        request: ToolCallRequest,
        *,
        permit_id: str | None,
        invoker: Invoker | None = None,
        forced: bool = False,
        retry: bool = False,
    ) -> Any:
        del forced, retry
        if not permit_id:
            raise ToolExecutionDenied("permit_required")
        expected = {
            "request_hash": request.request_hash,
            "caller": request.caller,
            "session_id": request.session_id,
            "turn_id": request.turn_id,
            "server_id": request.server_id,
            "tool_name": request.tool_name,
            "snapshot_id": request.snapshot_id,
            "schema_hash": request.schema_hash,
            "arguments_hash": request.arguments_hash,
        }
        try:
            self.store.consume_execution_permit(permit_id, expected=expected)
        except ExecutionPermitError as exc:
            raise ToolExecutionDenied(exc.code) from exc
        selected = invoker or (
            self.builtin_invoker
            if request.server_id == "builtin"
            else self.mcp_invoker
        )
        if selected is None:
            raise ToolExecutionDenied("invoker_unavailable")
        result = selected(request.arguments)
        return await result if inspect.isawaitable(result) else result

    async def execute_builtin(self, request: ToolCallRequest, **kwargs: Any) -> Any:
        if request.server_id != "builtin":
            raise ToolExecutionDenied("wrong_executor_path")
        return await self.execute(request, **kwargs)

    async def execute_mcp(self, request: ToolCallRequest, **kwargs: Any) -> Any:
        if request.server_id == "builtin":
            raise ToolExecutionDenied("wrong_executor_path")
        return await self.execute(request, **kwargs)


def _is_browser_tool(tool: Tool) -> bool:
    return bool(tool.metadata.get("browser")) or "public_url" in tool.outbound_scope


def _url_arguments(arguments: Mapping[str, Any]) -> list[str]:
    return [
        value
        for key, value in arguments.items()
        if "url" in key.casefold() and isinstance(value, str)
    ]


def _safe_arguments_summary(arguments: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, str) and "url" in key.casefold():
            summary[key] = sanitize_public_url(value)
        elif value is None or isinstance(value, bool):
            summary[key] = value
        elif isinstance(value, (int, float)):
            summary[key] = value
        elif isinstance(value, str):
            summary[key] = {"type": "string", "length": len(value)}
        elif isinstance(value, (list, tuple, dict)):
            summary[key] = {"type": type(value).__name__, "size": len(value)}
        else:
            summary[key] = {"type": type(value).__name__}
    return summary
