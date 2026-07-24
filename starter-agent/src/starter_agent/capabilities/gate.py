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
    AuditEvent,
    BoundedJsonObject,
    CapabilityModel,
    ExecutionPermit,
    Tool,
    canonical_json_sha256,
)
from starter_agent.capabilities.registry import (
    ExecutionCapability,
    UnifiedToolRegistry,
    is_mcp_tool_callable,
)
from starter_agent.capabilities.policy import (
    BrowserScopePolicy,
    PolicyDecision,
    PolicyRequest,
    ScopeDenied,
    ToolPolicy,
    classify_tool,
    extract_url_targets,
    infer_data_classes,
    reject_sensitive_url_query,
    validate_browser_payload,
    validate_serpapi_payload,
)
from starter_agent.capabilities.store import (
    CapabilityStore,
    ConfirmationExecutionError,
    ExecutionPermitError,
)
from starter_agent.tools.adapters.safe_web_fetcher import sanitize_public_url
from starter_agent.tools.base import ToolContext


class ToolCallRequest(CapabilityModel):
    principal: str = Field(default="local-user", min_length=1, max_length=200)
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
    def confirmation_arguments_hash(self) -> str:
        # Transport retry keys are not part of the approved business action.
        business_arguments = {
            key: value
            for key, value in self.arguments.items()
            if key.casefold() != "idempotency_key"
        }
        return canonical_json_sha256(business_arguments)

    @computed_field
    @property
    def confirmation_request_hash(self) -> str:
        return canonical_json_sha256(
            {
                "principal": self.principal,
                "session_id": self.session_id,
                "turn_id": self.turn_id,
                "call_id": self.call_id,
                "server_id": self.server_id,
                "tool_name": self.tool_name,
                "snapshot_id": self.snapshot_id,
                "schema_hash": self.schema_hash,
                "arguments_hash": self.confirmation_arguments_hash,
            }
        )

    @computed_field
    @property
    def request_hash(self) -> str:
        return canonical_json_sha256(
            {
                "caller": self.caller,
                "principal": self.principal,
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
        registry: UnifiedToolRegistry | None = None,
        policy: ToolPolicy | None = None,
        browser_policy: BrowserScopePolicy | None = None,
        permit_ttl_seconds: float = 30,
        max_outbound_bytes: int = 64_000,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if permit_ttl_seconds <= 0 or permit_ttl_seconds > 300:
            raise ValueError("permit_ttl_seconds must be within (0, 300]")
        self.store = store
        self.registry = registry
        self.policy = policy or ToolPolicy()
        self.browser_policy = browser_policy or BrowserScopePolicy()
        self.permit_ttl_seconds = permit_ttl_seconds
        self.max_outbound_bytes = max_outbound_bytes
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = Lock()
        self._decisions: dict[tuple[str, str, str], tuple[str, GateDecision]] = {}

    async def evaluate(
        self,
        request: ToolCallRequest,
        *,
        issue_permit: bool = True,
    ) -> GateDecision:
        request = self.canonicalize(request)
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
        inferred_classes = tuple(
            sorted(
                infer_data_classes(
                    request.arguments,
                    schema=tool.input_schema,
                    metadata=tool.metadata,
                    claimed=request.data_classes,
                )
            )
        )
        if _is_browser_tool(tool):
            try:
                self.browser_policy.validate_outbound(
                    inferred_classes,
                    outbound_size,
                    max_bytes=self.max_outbound_bytes,
                )
                validate_browser_payload(action, request.arguments)
                urls = extract_url_targets(request.arguments)
                targets = await self.browser_policy.validate_all(urls)
                for raw_url in urls:
                    reject_sensitive_url_query(raw_url)
                target = targets[0]
            except ScopeDenied as exc:
                return self._decision("deny", exc.code, request)
            scheme = target.scheme
            domain = target.hostname
            destination = target.hostname
        elif request.server_id.casefold() == "serpapi" or request.tool_name == (
            "search_jobs_serpapi"
        ):
            try:
                validate_serpapi_payload(
                    request.arguments,
                    inferred_classes,
                    max_bytes=self.max_outbound_bytes,
                )
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
                schema_hash=request.schema_hash,
                scheme=scheme,
                domain=domain,
                target_scopes=tuple(
                    (target.scheme, target.hostname)
                    for target in (targets if _is_browser_tool(tool) else ())
                ),
                arguments=request.arguments,
                role=request.role,
                data_classes=inferred_classes,
                reviewed=(
                    tool.review_state == "approved"
                    and (
                        request.server_id == "builtin"
                        or tool.reviewed_at is not None
                    )
                ),
                enabled=tool.enabled,
            ),
            rules,
        )
        if policy_decision.outcome == "allow" and not issue_permit:
            return GateDecision(
                "allow",
                policy_decision.reason_code,
                _safe_arguments_summary(request.arguments),
                destination,
            )
        return self._finalize(request, policy_decision, destination, rules)

    def request_for_tool(
        self,
        *,
        caller: str,
        principal: str = "local-user",
        session_id: str,
        turn_id: str,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        role: str = "user",
    ) -> ToolCallRequest:
        if self.registry is None:
            raise ValueError("registry_required")
        capability = self.registry.resolve_execution(tool_name)
        if capability is None:
            raise ValueError("tool_not_found")
        return ToolCallRequest(
            caller=caller,
            principal=principal,
            session_id=session_id,
            turn_id=turn_id,
            call_id=call_id,
            server_id=capability.server_id,
            tool_name=capability.canonical_name,
            snapshot_id=capability.snapshot_id,
            schema_hash=capability.schema_hash,
            arguments=dict(arguments),
            role=role,
        )

    def canonicalize(self, request: ToolCallRequest) -> ToolCallRequest:
        if self.registry is not None:
            capability = self.registry.resolve_execution(request.tool_name)
            if capability is not None:
                return request.model_copy(
                    update={
                        "server_id": capability.server_id,
                        "tool_name": capability.canonical_name,
                        "snapshot_id": capability.snapshot_id,
                        "schema_hash": capability.schema_hash,
                    }
                )
        if request.server_id != "builtin":
            snapshot = self.store.get_active_snapshot(request.server_id)
            if snapshot is not None:
                tool = next(
                    (
                        item
                        for item in self.store.list_tools(snapshot.id)
                        if request.tool_name in {item.upstream_name, item.model_alias}
                    ),
                    None,
                )
                if tool is not None:
                    return request.model_copy(
                        update={
                            "tool_name": tool.upstream_name,
                            "snapshot_id": snapshot.id,
                            "schema_hash": tool.schema_hash,
                        }
                    )
        return request

    async def revalidate(
        self, request: ToolCallRequest, permit: ExecutionPermit
    ) -> ToolCallRequest:
        canonical = self.canonicalize(request)
        decision = await self.evaluate(canonical, issue_permit=False)
        confirmed = False
        if permit.confirmation_id:
            confirmation = self.store.get_confirmation(permit.confirmation_id)
            confirmed = self._confirmation_matches(canonical, confirmation)
        if decision.outcome == "deny" or (
            decision.outcome == "require_confirmation" and not confirmed
        ):
            raise ToolExecutionDenied(decision.reason_code)
        rules = self.store.list_policy_rules(canonical.server_id, canonical.tool_name)
        revision = _policy_revision(rules)
        if revision != permit.policy_revision:
            raise ToolExecutionDenied("policy_revision_mismatch")
        return canonical

    async def evaluate_approved(
        self,
        request: ToolCallRequest,
        *,
        confirmation_id: str,
    ) -> GateDecision:
        if not confirmation_id or len(confirmation_id) > 160:
            return self._decision("deny", "confirmation_invalid", request)
        canonical = self.canonicalize(request)
        # Deny/disabled/stale policy state always wins over an old approval.
        decision = await self.evaluate(canonical, issue_permit=False)
        if decision.outcome == "deny":
            return decision
        confirmation = self.store.get_confirmation(confirmation_id)
        if confirmation is not None and confirmation.status == "consumed":
            return self._decision("deny", "confirmation_consumed", canonical)
        if not self._confirmation_matches(canonical, confirmation):
            return self._decision(
                "deny", "confirmation_binding_mismatch", canonical
            )
        rules = self.store.list_policy_rules(
            canonical.server_id, canonical.tool_name
        )
        if not _confirmation_policy_matches(confirmation, rules, canonical):
            return self._decision(
                "deny", "confirmation_policy_changed", canonical
            )
        if decision.outcome != "require_confirmation":
            return self._finalize(
                canonical,
                PolicyDecision("allow", "verified_confirmation"),
                decision.destination_summary,
                rules,
                confirmation_id=confirmation_id,
            )
        return self._finalize(
            canonical,
            PolicyDecision("allow", "verified_confirmation"),
            decision.destination_summary,
            rules,
            confirmation_id=confirmation_id,
        )

    def _confirmation_matches(self, request: ToolCallRequest, confirmation: Any) -> bool:
        if confirmation is None:
            return False
        if confirmation.status != "approved":
            return False
        if confirmation.expires_at <= self._now():
            return False
        expected_request_hashes = {
            request.confirmation_request_hash,
            # Backward-compatible only when no transport key was excluded.
            request.request_hash
            if request.arguments_hash == request.confirmation_arguments_hash
            else "",
        }
        return (
            confirmation.principal == request.principal
            and confirmation.session_id == request.session_id
            and confirmation.turn_id == request.turn_id
            and confirmation.call_id == request.call_id
            and confirmation.server_id == request.server_id
            and confirmation.tool_name == request.tool_name
            and confirmation.schema_hash == request.schema_hash
            and confirmation.snapshot_id == request.snapshot_id
            and confirmation.arguments_hash == request.confirmation_arguments_hash
            and (
                confirmation.data_classes is None
                or confirmation.data_classes == request.data_classes
            )
            and confirmation.request_hash in expected_request_hashes
        )

    def _resolve_tool(
        self, request: ToolCallRequest
    ) -> Tool | ExecutionCapability | GateDecision:
        if request.server_id == "builtin":
            capability = (
                None
                if self.registry is None
                else self.registry.resolve_execution(request.tool_name)
            )
            if capability is None:
                return self._decision("deny", "tool_not_found", request)
            if not capability.enabled:
                return self._decision("deny", "tool_disabled", request)
            if capability.schema_hash != request.schema_hash:
                return self._decision("deny", "schema_hash_mismatch", request)
            return capability
        server = self.store.get_server(request.server_id)
        if server is None:
            return self._decision("deny", "server_not_found", request)
        if not server.enabled:
            return self._decision("deny", "server_disabled", request)
        if server.connection_state != "ready":
            return self._decision("deny", "server_not_connected", request)
        snapshot = self.store.get_active_snapshot(request.server_id)
        if snapshot is None or not snapshot.active:
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
        if tool.review_state != "approved" or tool.reviewed_at is None:
            return self._decision("deny", "tool_review_required", request)
        if tool.schema_hash != request.schema_hash:
            return self._decision("deny", "schema_hash_mismatch", request)
        if not is_mcp_tool_callable(server, snapshot, tool):
            return self._decision("deny", "tool_review_required", request)
        return tool

    def _finalize(
        self,
        request: ToolCallRequest,
        policy: PolicyDecision,
        destination: str,
        rules,
        *,
        confirmation_id: str | None = None,
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
                    prior_permit = decision.permit
                    if confirmation_id and prior_permit is not None:
                        stored = self.store.get_execution_permit(prior_permit.id)
                        if stored is not None and stored.consumed_at is not None:
                            del self._decisions[key]
                        else:
                            return decision
                    else:
                        return decision
                elif key in self._decisions:
                    return GateDecision("deny", "duplicate_call", summary, destination)
            permit = ExecutionPermit(
                id=f"permit-{uuid4().hex}",
                confirmation_id=confirmation_id,
                request_hash=request.request_hash,
                policy_revision=_policy_revision(rules),
                expires_at=self._now() + timedelta(seconds=self.permit_ttl_seconds),
                caller=request.caller,
                principal=request.principal,
                session_id=request.session_id,
                turn_id=request.turn_id,
                server_id=request.server_id,
                tool_name=request.tool_name,
                snapshot_id=request.snapshot_id,
                schema_hash=request.schema_hash,
                arguments_hash=request.arguments_hash,
                data_classes=request.data_classes,
                decision="allow",
                invoker_id=_invoker_id(request.server_id, request.tool_name),
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


@dataclass(frozen=True, slots=True)
class NetworkGuardAttestation:
    targets: tuple[str, ...]
    dns_pinned: bool
    redirects_enforced: bool
    peer_verified: bool


@dataclass(frozen=True, slots=True)
class _InvokerRegistration:
    invoke: Callable[[Mapping[str, Any], Any], Any]
    context_factory: Callable[[ToolCallRequest], Any] | None = None
    network_guard: Callable[[ToolCallRequest], Any] | None = None


class UnifiedToolExecutor:
    def __init__(
        self,
        store: CapabilityStore,
        *,
        gate: PreToolCallGate,
    ) -> None:
        self.store = store
        self.gate = gate
        self._invokers: dict[str, _InvokerRegistration] = {}

    def has_invoker(self, server_id: str, tool_name: str) -> bool:
        """Return whether a concrete execution path is registered."""
        return _invoker_id(server_id, tool_name) in self._invokers

    def register_invoker(
        self,
        *,
        server_id: str,
        tool_name: str,
        invoker: Callable[[Mapping[str, Any], Any], Any],
        context_factory: Callable[[ToolCallRequest], Any] | None = None,
        network_guard: Callable[[ToolCallRequest], Any] | None = None,
    ) -> None:
        if self._registration_is_browser(server_id, tool_name) and network_guard is None:
            raise ToolExecutionDenied("network_guard_required")
        self._invokers[_invoker_id(server_id, tool_name)] = _InvokerRegistration(
            invoker, context_factory, network_guard
        )

    async def execute(
        self,
        request: ToolCallRequest,
        *,
        permit_id: str | None,
        forced: bool = False,
        retry: bool = False,
    ) -> Any:
        del forced, retry
        if not permit_id:
            raise ToolExecutionDenied("permit_required")
        permit = self.store.get_execution_permit(permit_id)
        if permit is None:
            raise ToolExecutionDenied("permit_not_found")
        if permit.expires_at <= datetime.now(UTC):
            raise ToolExecutionDenied("permit_expired")
        request = await self.gate.revalidate(request, permit)
        invoker_id = _invoker_id(request.server_id, request.tool_name)
        if permit.invoker_id != invoker_id:
            raise ToolExecutionDenied("permit_binding_mismatch")
        registration = self._invokers.get(invoker_id)
        if registration is None:
            raise ToolExecutionDenied("invoker_unavailable")
        capability = (
            None
            if self.gate.registry is None
            else self.gate.registry.resolve_execution(request.tool_name)
        )
        if capability is not None and capability.browser:
            targets = extract_url_targets(request.arguments)
            await self.gate.browser_policy.validate_all(targets)
            if registration.network_guard is None:
                raise ToolExecutionDenied("network_guard_required")
            attestation = registration.network_guard(request)
            if inspect.isawaitable(attestation):
                attestation = await attestation
            if not isinstance(attestation, NetworkGuardAttestation) or (
                attestation.targets != targets
                or not attestation.dns_pinned
                or not attestation.redirects_enforced
                or not attestation.peer_verified
            ):
                raise ToolExecutionDenied("network_guard_attestation_invalid")
        expected = {
            "request_hash": request.request_hash,
            "caller": request.caller,
            "principal": request.principal,
            "session_id": request.session_id,
            "turn_id": request.turn_id,
            "server_id": request.server_id,
            "tool_name": request.tool_name,
            "snapshot_id": request.snapshot_id,
            "schema_hash": request.schema_hash,
            "arguments_hash": request.arguments_hash,
            "invoker_id": invoker_id,
        }
        if permit.data_classes is not None:
            expected["data_classes"] = request.data_classes
        try:
            self.store.consume_execution_permit(permit_id, expected=expected)
        except ExecutionPermitError as exc:
            raise ToolExecutionDenied(exc.code) from exc
        self._audit_execution(
            request,
            permit_id=permit_id,
            confirmation_id=permit.confirmation_id,
            action="permit.consumed",
            decision="allow",
            reason_code="permit_consumed",
        )
        if permit.confirmation_id:
            execution_key = request.arguments.get("idempotency_key", request.call_id)
            if not isinstance(execution_key, str):
                raise ToolExecutionDenied("confirmation_execution_key_invalid")
            try:
                self.store.consume_confirmation_execution(
                    permit.confirmation_id,
                    execution_idempotency_key=execution_key,
                )
            except ConfirmationExecutionError as exc:
                raise ToolExecutionDenied(exc.code) from exc
        context = (
            None
            if registration.context_factory is None
            else registration.context_factory(request)
        )
        try:
            result = registration.invoke(request.arguments, context)
            result = await result if inspect.isawaitable(result) else result
        except BaseException:
            self._audit_execution(
                request,
                permit_id=permit_id,
                confirmation_id=permit.confirmation_id,
                action="tool.invoked",
                decision="error",
                reason_code="tool_invocation_failed",
            )
            raise
        self._audit_execution(
            request,
            permit_id=permit_id,
            confirmation_id=permit.confirmation_id,
            action="tool.invoked",
            decision="allow",
            reason_code="tool_invocation_completed",
        )
        return result

    def _audit_execution(
        self,
        request: ToolCallRequest,
        *,
        permit_id: str,
        confirmation_id: str | None,
        action: str,
        decision: Literal["allow", "error"],
        reason_code: str,
    ) -> None:
        self.store.append_audit_event(
            AuditEvent(
                event_id=f"audit-{uuid4().hex}",
                actor=request.principal,
                action=action,
                target=f"tool:{request.server_id}:{request.tool_name}",
                decision=decision,
                reason_code=reason_code,
                session_id=request.session_id,
                turn_id=request.turn_id,
                call_id=request.call_id,
                payload={
                    "permit_id": permit_id,
                    "confirmation_id": confirmation_id,
                    "request_hash": request.request_hash,
                    "schema_hash": request.schema_hash,
                    "arguments_hash": request.arguments_hash,
                },
                created_at=datetime.now(UTC),
            )
        )

    def _registration_is_browser(self, server_id: str, tool_name: str) -> bool:
        if self.gate.registry is not None:
            capability = self.gate.registry.resolve_execution(tool_name)
            if capability is not None and capability.server_id == server_id:
                return capability.browser
        if server_id != "builtin":
            snapshot = self.store.get_active_snapshot(server_id)
            if snapshot is not None:
                tool = next(
                    (item for item in self.store.list_tools(snapshot.id)
                     if tool_name in {item.upstream_name, item.model_alias}),
                    None,
                )
                return bool(tool and _is_browser_tool(tool))
        return False

    async def execute_builtin(self, request: ToolCallRequest, **kwargs: Any) -> Any:
        if request.server_id != "builtin":
            raise ToolExecutionDenied("wrong_executor_path")
        return await self.execute(request, **kwargs)

    async def execute_mcp(self, request: ToolCallRequest, **kwargs: Any) -> Any:
        if request.server_id == "builtin":
            raise ToolExecutionDenied("wrong_executor_path")
        return await self.execute(request, **kwargs)


def _is_browser_tool(tool: Tool) -> bool:
    if isinstance(tool, ExecutionCapability):
        return tool.browser
    return bool(tool.metadata.get("browser")) or "public_url" in tool.outbound_scope


def _url_arguments(arguments: Mapping[str, Any]) -> list[str]:
    return [
        value
        for key, value in arguments.items()
        if "url" in key.casefold() and isinstance(value, str)
    ]


def _invoker_id(server_id: str, tool_name: str) -> str:
    return f"{server_id}:{tool_name}"


def _policy_revision(rules) -> int:
    fingerprint = canonical_json_sha256(
        [rule.model_dump(mode="json") for rule in sorted(rules, key=lambda item: item.id)]
    )
    return int(fingerprint[:15], 16)


def _confirmation_allowlist_rule_id(confirmation_id: str) -> str:
    digest = canonical_json_sha256({"id": confirmation_id})
    return f"confirm-allow-{digest[:32]}"


def _confirmation_policy_matches(
    confirmation: Any,
    rules,
    request: ToolCallRequest,
) -> bool:
    if confirmation.policy_revision is None:
        return True
    if _policy_revision(rules) == confirmation.policy_revision:
        return True
    if confirmation.decision != "allowlist":
        return False
    generated_id = _confirmation_allowlist_rule_id(confirmation.id)
    generated = next((rule for rule in rules if rule.id == generated_id), None)
    if generated is None or not (
        generated.enabled
        and generated.effect == "allowlist_auto"
        and generated.server_id == request.server_id
        and generated.tool_name == request.tool_name
        and generated.schema_hash == request.schema_hash
        and generated.created_by == confirmation.principal
        and generated.data_classes == (confirmation.data_classes or ())
        and generated.roles == (request.role,)
    ):
        return False
    prior_rules = [rule for rule in rules if rule.id != generated_id]
    return _policy_revision(prior_rules) == confirmation.policy_revision


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
