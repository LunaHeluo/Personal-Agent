from __future__ import annotations

import inspect
import hashlib
import asyncio
import json
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from typing import Any, Awaitable, Literal, Protocol
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field

from starter_agent.bootstrap import create_application, create_mcp_manager
from starter_agent.capabilities.confirmations import (
    ConfirmationService,
    ConfirmationStateError,
)
from starter_agent.capabilities.models import (
    AuditEvent,
    BuiltinToolOverride,
    Confirmation,
    PolicyRule,
    canonical_json_sha256,
)
from starter_agent.agent.tool_result_guard import redact_tool_result_content
from starter_agent.capabilities.store import (
    CapabilityStore,
    RecordAlreadyExistsError,
    RecordNotFoundError,
    RevisionConflictError,
)
from starter_agent.mcp.manager import McpManagerError
from starter_agent.skills.registry import (
    SkillCandidateChangedError,
    SkillReloadError,
)


Role = Literal["viewer", "operator", "admin"]
_ROLE_LEVEL: dict[Role, int] = {"viewer": 0, "operator": 1, "admin": 2}


class ManagementPrincipal(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject: str = Field(min_length=1, max_length=200)
    role: Role


class OidcSdkResolver(Protocol):
    def __call__(
        self, request: Request
    ) -> ManagementPrincipal | Awaitable[ManagementPrincipal]: ...


class PrincipalResolver:
    """Resolve local principals or delegate remote identity to an OIDC SDK.

    This class deliberately does not parse or verify bearer tokens.
    """

    def __init__(
        self,
        oidc_sdk_resolver: OidcSdkResolver | None = None,
        *,
        local_principal: ManagementPrincipal | None = None,
    ) -> None:
        self.oidc_sdk_resolver = oidc_sdk_resolver
        self.local_principal = local_principal or ManagementPrincipal(
            subject="local-user", role="admin"
        )

    async def __call__(self, request: Request) -> ManagementPrincipal:
        host = request.client.host if request.client is not None else ""
        try:
            is_loopback = ip_address(host).is_loopback
        except ValueError:
            is_loopback = host.casefold() == "localhost"
        if is_loopback:
            return self.local_principal
        if self.oidc_sdk_resolver is None:
            raise _http_error(
                503,
                "management_auth_unconfigured",
                "Remote management requires a configured standard OIDC SDK.",
            )
        resolved = self.oidc_sdk_resolver(request)
        if inspect.isawaitable(resolved):
            resolved = await resolved
        if not isinstance(resolved, ManagementPrincipal):
            raise _http_error(401, "management_identity_invalid", "Invalid identity.")
        return resolved


principal_resolver = PrincipalResolver()


async def get_management_principal(request: Request) -> ManagementPrincipal:
    cached = getattr(request.state, "management_principal", None)
    if isinstance(cached, ManagementPrincipal):
        return cached
    try:
        resolved = await principal_resolver(request)
    except HTTPException as exc:
        request.state.management_auth_error = exc
        resolved = ManagementPrincipal(subject="anonymous", role="viewer")
    except Exception:
        request.state.management_auth_error = _http_error(
            503,
            "management_identity_resolution_failed",
            "Remote management identity resolution failed.",
        )
        resolved = ManagementPrincipal(subject="anonymous", role="viewer")
    request.state.management_principal = resolved
    return resolved


def require_role(principal: ManagementPrincipal, required: Role) -> None:
    if _ROLE_LEVEL[principal.role] < _ROLE_LEVEL[required]:
        raise _http_error(
            403,
            "management_forbidden",
            f"{required} role is required.",
        )


class ManagementMutation(BaseModel):
    expected_revision: int | None = Field(default=None, ge=0)


class ReviewMutation(ManagementMutation):
    review_state: Literal["unreviewed", "approved", "review_required", "rejected"]


class PolicyMutation(ManagementMutation):
    rule_id: str | None = Field(default=None, min_length=1, max_length=160)
    effect: Literal[
        "deny",
        "always_confirm",
        "allowlist_auto",
        "confirm_once",
        "require_confirmation",
    ]
    schemes: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    domains: tuple[str, ...] = Field(default_factory=tuple, max_length=200)
    actions: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    parameter_constraints: dict[str, Any] = Field(default_factory=dict)
    data_classes: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    roles: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    enabled: bool = True


class ConfirmationDecisionRequest(ManagementMutation):
    decision: Literal["once", "allowlist", "cancel"]
    idempotency_key: str = Field(min_length=1, max_length=1000)
    session_id: str | None = Field(default=None, min_length=1, max_length=200)


class CapabilityApiServices:
    def __init__(
        self,
        *,
        manager: Any,
        registry: Any,
        skill_registry: Any,
        confirmations: ConfirmationService | None,
        store: CapabilityStore | None,
        application: Any,
    ) -> None:
        self.manager = manager
        self.registry = registry
        self.skill_registry = skill_registry
        self.confirmations = confirmations
        self.store = store
        self.application = application


def get_capability_services() -> CapabilityApiServices:
    application = create_application()
    runtime = application.runtime
    confirmations = getattr(
        getattr(runtime, "turn_coordinator", None), "confirmations", None
    )
    store = getattr(getattr(runtime, "gate", None), "store", None)
    skill_registry = getattr(application.context, "skill_registry", None)
    return CapabilityApiServices(
        manager=create_mcp_manager(),
        registry=runtime.tools,
        skill_registry=skill_registry,
        confirmations=confirmations,
        store=store,
        application=application,
    )


def _http_error(
    status_code: int,
    code: str,
    message: str,
    *,
    authoritative_state: Any | None = None,
) -> HTTPException:
    detail: dict[str, Any] = {"code": code, "message": message}
    if authoritative_state is not None:
        detail["authoritative_state"] = authoritative_state
    return HTTPException(status_code=status_code, detail=detail)


def _revision(
    body_revision: int | None,
    if_match: str | None,
) -> int:
    if body_revision is not None:
        if if_match is not None:
            try:
                header_revision = int(if_match.strip().strip('"'))
            except ValueError as exc:
                raise _http_error(400, "if_match_invalid", "Invalid If-Match.") from exc
            if header_revision != body_revision:
                raise _http_error(
                    400, "revision_ambiguous", "Body and If-Match revisions differ."
                )
        return body_revision
    if if_match is None:
        raise _http_error(
            status.HTTP_428_PRECONDITION_REQUIRED,
            "expected_revision_required",
            "expected_revision or If-Match is required.",
        )
    try:
        value = int(if_match.strip().strip('"'))
    except ValueError as exc:
        raise _http_error(400, "if_match_invalid", "Invalid If-Match.") from exc
    if value < 0:
        raise _http_error(400, "if_match_invalid", "Invalid If-Match.")
    return value


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "as_dict"):
        return value.as_dict()
    return value


def _operation_result(operation_id: str, revision: int, state: Any) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "revision": revision,
        "state": _dump(state),
    }


def _audit_mutation(
    services: CapabilityApiServices,
    *,
    actor: str,
    operation_id: str,
    action: str,
    target: str,
    decision: Literal["allow", "deny", "approved", "cancelled", "error"],
    reason_code: str,
    before_revision: int | None,
    after_revision: int | None,
    result: str,
) -> None:
    if services.store is None:
        return
    services.store.append_audit_event(
        AuditEvent(
            event_id=f"audit-{uuid4().hex}",
            actor=actor,
            action=action,
            target=target,
            decision=decision,
            reason_code=reason_code,
            call_id=operation_id,
            payload={
                "operation_id": operation_id,
                "before_revision": before_revision,
                "after_revision": after_revision,
                "result": result[:200],
            },
            created_at=datetime.now(UTC),
        )
    )


async def _mutation_exception_boundary(
    request: Request,
    services: CapabilityApiServices = Depends(get_capability_services),
    actor: ManagementPrincipal = Depends(get_management_principal),
):
    auth_error = getattr(request.state, "management_auth_error", None)
    if isinstance(auth_error, HTTPException):
        operation_id = f"operation-{uuid4().hex}"
        detail = (
            dict(auth_error.detail)
            if isinstance(auth_error.detail, dict)
            else {
                "code": "management_auth_failed",
                "message": str(auth_error.detail),
            }
        )
        detail["operation_id"] = operation_id
        auth_error.detail = detail
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            _audit_mutation(
                services,
                actor=actor.subject,
                operation_id=operation_id,
                action="management.request",
                target=request.url.path,
                decision="error",
                reason_code=str(detail.get("code", "management_auth_failed")),
                before_revision=None,
                after_revision=None,
                result="failed",
            )
        raise auth_error
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        yield
        return
    operation_id = f"operation-{uuid4().hex}"
    try:
        yield
    except asyncio.CancelledError:
        _audit_mutation(
            services,
            actor=actor.subject,
            operation_id=operation_id,
            action="management.request",
            target=request.url.path,
            decision="error",
            reason_code="operation_cancelled",
            before_revision=None,
            after_revision=None,
            result="cancelled",
        )
        raise
    except RequestValidationError as exc:
        _audit_mutation(
            services,
            actor=actor.subject,
            operation_id=operation_id,
            action="management.request",
            target=request.url.path,
            decision="error",
            reason_code="validation_error",
            before_revision=None,
            after_revision=None,
            result="validation_error",
        )
        raise _http_error(
            422,
            "validation_error",
            "Management request validation failed.",
            authoritative_state={"operation_id": operation_id},
        ) from exc
    except HTTPException as exc:
        detail = (
            dict(exc.detail)
            if isinstance(exc.detail, dict)
            else {"code": "management_error", "message": str(exc.detail)}
        )
        detail.setdefault("operation_id", operation_id)
        exc.detail = detail
        _audit_mutation(
            services,
            actor=actor.subject,
            operation_id=operation_id,
            action="management.request",
            target=request.url.path,
            decision="error",
            reason_code=str(detail.get("code", "management_error")),
            before_revision=None,
            after_revision=None,
            result="failed",
        )
        raise
    except Exception as exc:
        if isinstance(exc, TimeoutError):
            status_code, code = 504, "operation_timeout"
        elif isinstance(exc, RevisionConflictError):
            status_code, code = 409, "revision_conflict"
        elif isinstance(exc, RecordAlreadyExistsError):
            status_code, code = 409, "record_exists"
        elif isinstance(exc, RecordNotFoundError):
            status_code, code = 404, "record_not_found"
        elif isinstance(exc, ValueError):
            status_code, code = 422, "validation_error"
        elif isinstance(exc, McpManagerError):
            mapped = _manager_error(exc)
            status_code = mapped.status_code
            code = exc.code
        else:
            status_code, code = 500, "management_error"
        _audit_mutation(
            services,
            actor=actor.subject,
            operation_id=operation_id,
            action="management.request",
            target=request.url.path,
            decision="error",
            reason_code=code,
            before_revision=None,
            after_revision=None,
            result=type(exc).__name__,
        )
        raise _http_error(
            status_code,
            code,
            "Management operation failed.",
            authoritative_state={"operation_id": operation_id},
        ) from exc


def _server_state(services: CapabilityApiServices, server_id: str) -> Any:
    try:
        return services.manager.get_status(server_id)
    except (KeyError, McpManagerError) as exc:
        raise _http_error(404, "server_not_found", "Server not found.") from exc


def _registry_revision(services: CapabilityApiServices) -> int:
    return int(getattr(services.registry, "context_revision", 0))


def _tool(services: CapabilityApiServices, name: str) -> dict[str, Any]:
    capability = services.registry.resolve_execution(name)
    if capability is None:
        raise _http_error(404, "tool_not_found", "Tool not found.")
    rules = (
        []
        if services.store is None
        else services.store.list_policy_rules(
            capability.server_id, capability.canonical_name
        )
    )
    return {
        "name": capability.model_alias,
        "canonical_name": capability.canonical_name,
        "server_id": capability.server_id,
        "snapshot_id": capability.snapshot_id,
        "schema_hash": capability.schema_hash,
        "schema": dict(capability.input_schema),
        "metadata": dict(capability.metadata),
        "risk_level": capability.risk_level,
        "enabled": capability.enabled,
        "connected": capability.connected,
        "review_state": capability.review_state,
        "reviewed_at": capability.reviewed_at,
        "browser": capability.browser,
        "revision": _tool_revision(services, capability),
        "context_revision": _registry_revision(services),
        "policy_rules": [_dump(item) for item in rules],
    }


def _tool_revision(services: CapabilityApiServices, capability: Any) -> int:
    if capability.server_id == "builtin":
        if services.store is None:
            return 0
        override = services.store.get_builtin_tool_override(
            capability.canonical_name
        )
        return 0 if override is None else override.revision
    if services.store is None:
        return _registry_revision(services)
    tools = services.store.list_tools(capability.snapshot_id)
    stored = next(
        (item for item in tools if item.upstream_name == capability.canonical_name),
        None,
    )
    return _registry_revision(services) if stored is None else stored.revision


def _skill_metadata(skill: Any) -> dict[str, Any]:
    return {
        "name": skill.name,
        "description": skill.description,
        "version": skill.version,
        "source": skill.source,
        "enabled": skill.enabled,
        "dependencies": [_dump(item) for item in skill.dependencies],
        "snapshot_hash": skill.snapshot_hash,
        "dependency_state": skill.dependency_state,
        "missing_dependencies": skill.missing_dependencies,
    }


def _redact_skill_definition(value: str) -> str:
    return redact_tool_result_content(value)


def _replay_result(
    services: CapabilityApiServices,
    confirmation: Confirmation,
    request: ConfirmationDecisionRequest,
) -> tuple[int, dict[str, Any]] | None:
    if confirmation.status not in {"consumed", "cancelled"}:
        return None
    key_hash = hashlib.sha256(request.idempotency_key.encode("utf-8")).hexdigest()
    if (
        confirmation.decision != request.decision
        or confirmation.idempotency_key_hash != key_hash
    ):
        return None
    if services.store is None:
        return None
    for event in reversed(services.store.list_audit_events()):
        if (
            event.action == "management.terminal"
            and event.call_id == confirmation.call_id
        ):
            response = event.payload.get("response")
            status_code = event.payload.get("status_code")
            if isinstance(response, dict) and isinstance(status_code, int):
                return status_code, dict(response)
    return None


def _safe_response_payload(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(
        redact_tool_result_content(
            json.dumps(value, ensure_ascii=False, default=str)
        )
    )


def _record_management_terminal(
    services: CapabilityApiServices,
    confirmation: Confirmation,
    *,
    actor: str,
    status_code: int,
    response: dict[str, Any],
) -> None:
    if services.store is None:
        return
    event = AuditEvent(
        event_id=f"management-terminal-{confirmation.id}",
        actor=actor,
        action="management.terminal",
        target=confirmation.destination,
        decision=(
            "cancelled"
            if confirmation.decision == "cancel"
            else ("approved" if status_code < 400 else "error")
        ),
        reason_code=(
            "management_operation_completed"
            if status_code < 400
            else str(response.get("detail", {}).get("code", "management_failed"))
        ),
        session_id=confirmation.session_id,
        turn_id=confirmation.turn_id,
        call_id=confirmation.call_id,
        payload={
            "confirmation_id": confirmation.id,
            "decision": confirmation.decision,
            "idempotency_hash": confirmation.idempotency_key_hash,
            "status_code": status_code,
            "response": _safe_response_payload(response),
        },
        created_at=datetime.now(UTC),
    )
    try:
        services.store.append_audit_event(event)
    except RecordAlreadyExistsError:
        return


def _skill_record(services: CapabilityApiServices, name: str) -> Any:
    if services.skill_registry is None:
        raise _http_error(503, "skill_registry_unavailable", "Skill registry unavailable.")
    skill = services.skill_registry.get(name)
    if skill is None:
        raise _http_error(404, "skill_not_found", "Skill not found.")
    record = None if services.store is None else services.store.get_skill(name)
    return skill, record


def _management_confirmation(
    services: CapabilityApiServices,
    principal: ManagementPrincipal,
    *,
    operation: str,
    target: str,
    expected_revision: int,
    diff: dict[str, Any],
    risk: str,
    impact: list[str],
    payload: dict[str, Any] | None = None,
) -> Confirmation:
    if services.store is None:
        raise _http_error(503, "capability_store_unavailable", "Store unavailable.")
    operation_id = f"operation-{uuid4().hex}"
    summary = {
        "operation": operation,
        "target": target,
        "expected_revision": expected_revision,
        "diff": diff,
        "risk": risk,
        "impact": impact,
        "payload": payload or {},
    }
    confirmation = Confirmation(
        id=f"confirmation-{uuid4().hex}",
        principal=principal.subject,
        session_id="management",
        turn_id=operation_id,
        call_id=operation_id,
        request_hash=canonical_json_sha256(summary),
        server_id="management",
        tool_name=operation,
        schema_hash=canonical_json_sha256({"operation": operation}),
        arguments_hash=canonical_json_sha256(summary),
        arguments_summary=summary,
        risk="dangerous",
        destination=target,
        gate_reason_code="management_confirmation_required",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    services.store.create_confirmation(confirmation)
    services.store.append_audit_event(
        AuditEvent(
            event_id=f"audit-{uuid4().hex}",
            actor=principal.subject,
            action="management.confirmation.created",
            target=target,
            decision="require_confirmation",
            reason_code="management_confirmation_required",
            session_id=confirmation.session_id,
            turn_id=confirmation.turn_id,
            call_id=confirmation.call_id,
            payload={
                "confirmation_id": confirmation.id,
                "operation": operation,
                "expected_revision": expected_revision,
                "risk": risk,
                "impact": impact,
            },
            created_at=datetime.now(UTC),
        )
    )
    return confirmation


def _confirmation_result(confirmation: Confirmation) -> dict[str, Any]:
    return {
        "operation_id": confirmation.turn_id,
        "revision": confirmation.revision,
        "state": _dump(confirmation),
        "confirmation": _dump(confirmation),
    }


def _confirmation_authority(
    services: CapabilityApiServices,
    confirmation: Confirmation,
) -> dict[str, Any]:
    payload = dict(_dump(confirmation))
    events = (
        []
        if services.store is None
        else [
            event
            for event in services.store.list_audit_events()
            if event.session_id == confirmation.session_id
            and event.turn_id == confirmation.turn_id
            and event.call_id == confirmation.call_id
        ]
    )
    latest = events[-1] if events else None
    payload.update(
        {
            "allowlist_allowed": confirmation.gate_reason_code != "always_confirm",
            "allowlist_reason": (
                None
                if confirmation.gate_reason_code != "always_confirm"
                else "always_confirm policy requires approval for every call"
            ),
            "audit_ref": None if latest is None else latest.event_id,
            "trace_ref": (
                f"trace:{confirmation.session_id}:"
                f"{confirmation.turn_id}:{confirmation.call_id}"
            ),
            "reason_code": (
                confirmation.gate_reason_code
                if latest is None
                else latest.reason_code
            ),
            "tool_invoked": any(event.action == "tool.invoked" for event in events),
        }
    )
    return payload


def _manager_error(
    exc: McpManagerError,
    *,
    authoritative_state: Any | None = None,
) -> HTTPException:
    if exc.code == "server_not_found":
        code = 404
    elif exc.code in {"revision_conflict", "refresh_in_progress", "manager_draining"}:
        code = 409
    elif "timeout" in exc.code:
        code = 504
    else:
        code = 502
    return _http_error(
        code,
        exc.code,
        "MCP management operation failed.",
        authoritative_state=_dump(authoritative_state),
    )


async def _execute_management(
    services: CapabilityApiServices,
    confirmation: Confirmation,
) -> dict[str, Any]:
    summary = dict(confirmation.arguments_summary)
    operation = str(summary["operation"])
    target = str(summary["target"])
    expected = int(summary["expected_revision"])
    operation_id = confirmation.turn_id
    if operation.startswith("server."):
        current = _server_state(services, target)
        if current.revision != expected:
            raise _http_error(
                409,
                "revision_conflict",
                "Server revision changed before execution.",
                authoritative_state=_dump(current),
            )
        try:
            if operation == "server.connect":
                result = await services.manager.connect(target)
                if result.connection_state == "failed":
                    raise _http_error(
                        502,
                        result.error_code or "connect_failed",
                        "MCP server failed to connect.",
                        authoritative_state=_dump(result),
                    )
            elif operation == "server.disconnect":
                close_error = await services.manager.close(target)
                result = _server_state(services, target)
                if close_error is not None:
                    raise _http_error(
                        502,
                        close_error,
                        "MCP server failed to disconnect cleanly.",
                        authoritative_state=_dump(result),
                    )
            elif operation == "server.refresh":
                await services.manager.refresh_server(target, expected)
                result = _server_state(services, target)
            else:
                raise _http_error(400, "operation_invalid", "Unknown operation.")
        except McpManagerError as exc:
            raise _manager_error(
                exc, authoritative_state=_server_state(services, target)
            ) from exc
        services.registry.refresh_from_manager(services.manager)
        return _operation_result(operation_id, result.revision, result)
    if operation == "tool.enable":
        current_tool = _tool(services, target)
        if current_tool["revision"] != expected:
            raise _http_error(
                409,
                "revision_conflict",
                "Tool changed before execution.",
                authoritative_state=current_tool,
            )
        capability = services.registry.resolve_execution(target)
        if capability.server_id == "builtin":
            if services.store is None:
                raise _http_error(
                    503, "capability_store_unavailable", "Store unavailable."
                )
            updated_override = services.store.put_builtin_tool_override(
                BuiltinToolOverride(
                    tool_name=capability.canonical_name,
                    enabled=True,
                ),
                expected_revision=expected,
            )
            services.registry.set_tool_enabled(target, True)
            return _operation_result(
                operation_id,
                updated_override.revision,
                _tool(services, target),
            )
        if services.store is not None:
            updated = services.store.update_tool(
                capability.snapshot_id,
                capability.canonical_name,
                expected_revision=expected,
                enabled=True,
            )
            services.registry.refresh_from_manager(services.manager)
            return _operation_result(
                operation_id, updated.revision, _tool(services, target)
            )
        raise _http_error(
            503, "capability_store_unavailable", "Store unavailable."
        )
    if operation == "policy.create":
        current = _registry_revision(services)
        if current != expected:
            raise _http_error(
                409,
                "revision_conflict",
                "Tool policy context changed before execution.",
                authoritative_state={"revision": current},
            )
        payload = dict(summary.get("payload") or {})
        rule = PolicyRule.model_validate(payload.get("rule", payload))
        assert services.store is not None
        services.store.create_policy_rule(rule)
        snapshot = services.registry.notify_policy_changed()
        return _operation_result(operation_id, snapshot.context_revision, rule)
    if operation == "skill.reload":
        if services.skill_registry is None:
            raise _http_error(503, "skill_registry_unavailable", "Skill registry unavailable.")
        current = services.skill_registry.snapshot()
        if current.revision != expected:
            raise _http_error(
                409,
                "revision_conflict",
                "Skill registry changed before execution.",
                authoritative_state=_dump(current),
            )
        payload = dict(summary.get("payload") or {})
        snapshot = services.skill_registry.reload_one(
            target,
            expected_revision=expected,
            expected_candidate_hash=str(payload["candidate_hash"]),
        )
        skill = services.skill_registry.get(target)
        record = None if services.store is None else services.store.get_skill(target)
        return _operation_result(
            operation_id,
            snapshot.revision,
            {
                "skill": None if skill is None else _skill_metadata(skill),
                "record": _dump(record),
            },
        )
    raise _http_error(400, "operation_invalid", "Unknown management operation.")


def create_capabilities_router() -> APIRouter:
    router = APIRouter(
        prefix="/v1/capabilities",
        tags=["capabilities"],
        dependencies=[Depends(_mutation_exception_boundary)],
    )

    @router.get("/catalog/export")
    async def export_catalog(
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, object]:
        require_role(actor, "viewer")
        return services.registry.catalog_export()

    @router.get("/servers")
    async def list_servers(
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "viewer")
        values = sorted(services.manager.statuses().values(), key=lambda item: item.id)
        return {"servers": [_dump(item) for item in values]}

    @router.get("/servers/{server_id}")
    async def get_server(
        server_id: str,
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "viewer")
        state = _server_state(services, server_id)
        snapshot = services.manager.get_snapshot_summary(server_id)
        return {"server": _dump(state), "snapshot": _dump(snapshot)}

    @router.get("/servers/{server_id}/health")
    async def server_health(
        server_id: str,
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "viewer")
        state = _server_state(services, server_id)
        return {
            "server_id": server_id,
            "health_state": state.health_state,
            "last_checked_at": state.last_checked_at,
            "revision": state.revision,
            "error_code": state.error_code,
        }

    @router.post("/servers/{server_id}/health-check")
    async def check_server_health(
        server_id: str,
        mutation: ManagementMutation,
        if_match: str | None = Header(default=None, alias="If-Match"),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "operator")
        expected = _revision(mutation.expected_revision, if_match)
        current = _server_state(services, server_id)
        operation_id = f"operation-{uuid4().hex}"
        if current.revision != expected:
            _audit_mutation(
                services,
                actor=actor.subject,
                operation_id=operation_id,
                action="server.health_check",
                target=f"server:{server_id}",
                decision="error",
                reason_code="revision_conflict",
                before_revision=current.revision,
                after_revision=current.revision,
                result="conflict",
            )
            raise _http_error(
                409, "revision_conflict", "Server revision conflict.",
                authoritative_state=_dump(current),
            )
        try:
            updated = await services.manager.ping(server_id)
        except McpManagerError as exc:
            latest = _server_state(services, server_id)
            _audit_mutation(
                services,
                actor=actor.subject,
                operation_id=operation_id,
                action="server.health_check",
                target=f"server:{server_id}",
                decision="error",
                reason_code=exc.code,
                before_revision=current.revision,
                after_revision=latest.revision,
                result="failed",
            )
            raise _manager_error(exc, authoritative_state=latest) from exc
        _audit_mutation(
            services,
            actor=actor.subject,
            operation_id=operation_id,
            action="server.health_check",
            target=f"server:{server_id}",
            decision="allow",
            reason_code="health_checked",
            before_revision=current.revision,
            after_revision=updated.revision,
            result=updated.health_state,
        )
        return _operation_result(operation_id, updated.revision, updated)

    async def confirm_server_operation(
        server_id: str,
        operation: str,
        mutation: ManagementMutation,
        if_match: str | None,
        services: CapabilityApiServices,
        actor: ManagementPrincipal,
    ) -> dict[str, Any]:
        require_role(actor, "admin")
        expected = _revision(mutation.expected_revision, if_match)
        current = _server_state(services, server_id)
        if current.revision != expected:
            raise _http_error(
                409,
                "revision_conflict",
                "Server revision conflict.",
                authoritative_state=_dump(current),
            )
        desired = "closed" if operation == "disconnect" else "ready"
        confirmation = _management_confirmation(
            services,
            actor,
            operation=f"server.{operation}",
            target=server_id,
            expected_revision=expected,
            diff={"connection_state": [current.connection_state, desired]},
            risk="external_process_lifecycle",
            impact=[f"server:{server_id}", "tool_availability"],
        )
        return _confirmation_result(confirmation)

    @router.post("/servers/{server_id}/connect", status_code=202)
    async def connect_server(
        server_id: str,
        mutation: ManagementMutation,
        if_match: str | None = Header(default=None, alias="If-Match"),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        return await confirm_server_operation(
            server_id, "connect", mutation, if_match, services, actor
        )

    @router.post("/servers/{server_id}/disconnect", status_code=202)
    async def disconnect_server(
        server_id: str,
        mutation: ManagementMutation,
        if_match: str | None = Header(default=None, alias="If-Match"),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        return await confirm_server_operation(
            server_id, "disconnect", mutation, if_match, services, actor
        )

    @router.post("/servers/{server_id}/refresh", status_code=202)
    async def refresh_server(
        server_id: str,
        mutation: ManagementMutation,
        if_match: str | None = Header(default=None, alias="If-Match"),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        return await confirm_server_operation(
            server_id, "refresh", mutation, if_match, services, actor
        )

    @router.post("/servers/{server_id}/{action}")
    async def set_server_enabled(
        server_id: str,
        action: Literal["enable", "disable"],
        mutation: ManagementMutation,
        if_match: str | None = Header(default=None, alias="If-Match"),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "operator")
        expected = _revision(mutation.expected_revision, if_match)
        try:
            state = services.manager.set_enabled(
                server_id, action == "enable", expected_revision=expected
            )
        except McpManagerError as exc:
            latest = None
            try:
                latest = _server_state(services, server_id)
            except HTTPException:
                pass
            raise _manager_error(exc, authoritative_state=latest) from exc
        services.registry.refresh_from_manager(services.manager)
        operation_id = f"operation-{uuid4().hex}"
        _audit_mutation(
            services,
            actor=actor.subject,
            operation_id=operation_id,
            action=f"server.{action}",
            target=f"server:{server_id}",
            decision="allow",
            reason_code=f"server_{action}d",
            before_revision=expected,
            after_revision=state.revision,
            result=action,
        )
        return _operation_result(operation_id, state.revision, state)

    @router.get("/tools")
    async def list_tools(
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "viewer")
        catalog = services.registry.lightweight_catalog()
        return _dump(catalog)

    @router.get("/tools/{tool_name}")
    async def get_tool(
        tool_name: str,
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "viewer")
        return {"tool": _tool(services, tool_name)}

    @router.get("/tools/{tool_name}/schema")
    async def get_tool_schema(
        tool_name: str,
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "viewer")
        tool = _tool(services, tool_name)
        return {
            "name": tool["name"],
            "schema_hash": tool["schema_hash"],
            "schema": tool["schema"],
            "revision": tool["revision"],
        }

    async def change_tool_enabled(
        tool_name: str,
        enabled: bool,
        mutation: ManagementMutation,
        if_match: str | None,
        services: CapabilityApiServices,
        actor: ManagementPrincipal,
    ) -> dict[str, Any]:
        require_role(actor, "operator")
        expected = _revision(mutation.expected_revision, if_match)
        current = _tool(services, tool_name)
        if expected != current["revision"]:
            raise _http_error(
                409,
                "revision_conflict",
                "Tool registry revision conflict.",
                authoritative_state=current,
            )
        enabling_risky = enabled and current["risk_level"] != "read"
        if enabling_risky:
            require_role(actor, "admin")
            confirmation = _management_confirmation(
                services,
                actor,
                operation="tool.enable",
                target=tool_name,
                expected_revision=expected,
                diff={"enabled": [current["enabled"], True]},
                risk=f"{current['risk_level']}_tool_activation",
                impact=[f"tool:{tool_name}", "model_tool_exposure"],
            )
            return _confirmation_result(confirmation)
        capability = services.registry.resolve_execution(tool_name)
        if capability.server_id == "builtin":
            if services.store is None:
                raise _http_error(
                    503, "capability_store_unavailable", "Store unavailable."
                )
            try:
                updated_override = services.store.put_builtin_tool_override(
                    BuiltinToolOverride(
                        tool_name=capability.canonical_name,
                        enabled=enabled,
                    ),
                    expected_revision=expected,
                )
            except RevisionConflictError as exc:
                raise _http_error(
                    409,
                    "revision_conflict",
                    "Builtin tool revision conflict.",
                    authoritative_state=_tool(services, tool_name),
                ) from exc
            services.registry.set_tool_enabled(tool_name, enabled)
            operation_id = f"operation-{uuid4().hex}"
            _audit_mutation(
                services,
                actor=actor.subject,
                operation_id=operation_id,
                action="tool.enabled" if enabled else "tool.disabled",
                target=f"tool:{tool_name}",
                decision="allow",
                reason_code="tool_state_updated",
                before_revision=expected,
                after_revision=updated_override.revision,
                result="enabled" if enabled else "disabled",
            )
            return _operation_result(
                operation_id,
                updated_override.revision,
                _tool(services, tool_name),
            )
        if services.store is not None:
            try:
                updated = services.store.update_tool(
                    capability.snapshot_id,
                    capability.canonical_name,
                    expected_revision=expected,
                    enabled=enabled,
                )
            except RevisionConflictError as exc:
                raise _http_error(
                    409, "revision_conflict", "Tool revision conflict.",
                    authoritative_state=_tool(services, tool_name),
                ) from exc
            services.registry.refresh_from_manager(services.manager)
            operation_id = f"operation-{uuid4().hex}"
            _audit_mutation(
                services,
                actor=actor.subject,
                operation_id=operation_id,
                action="tool.enabled" if enabled else "tool.disabled",
                target=f"tool:{tool_name}",
                decision="allow",
                reason_code="tool_state_updated",
                before_revision=expected,
                after_revision=updated.revision,
                result="enabled" if enabled else "disabled",
            )
            return _operation_result(
                operation_id,
                updated.revision,
                _tool(services, tool_name),
            )
        raise _http_error(
            503, "capability_store_unavailable", "Store unavailable."
        )

    @router.post("/tools/{tool_name}/enable", status_code=202)
    async def enable_tool(
        tool_name: str,
        mutation: ManagementMutation,
        if_match: str | None = Header(default=None, alias="If-Match"),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        return await change_tool_enabled(
            tool_name, True, mutation, if_match, services, actor
        )

    @router.post("/tools/{tool_name}/disable")
    async def disable_tool(
        tool_name: str,
        mutation: ManagementMutation,
        if_match: str | None = Header(default=None, alias="If-Match"),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        return await change_tool_enabled(
            tool_name, False, mutation, if_match, services, actor
        )

    @router.post("/tools/{tool_name}/review")
    async def review_tool(
        tool_name: str,
        mutation: ReviewMutation,
        if_match: str | None = Header(default=None, alias="If-Match"),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "admin")
        expected = _revision(mutation.expected_revision, if_match)
        current = _tool(services, tool_name)
        if current["revision"] != expected:
            raise _http_error(
                409, "revision_conflict", "Tool registry revision conflict.",
                authoritative_state=current,
            )
        capability = services.registry.resolve_execution(tool_name)
        if capability.server_id == "builtin":
            raise _http_error(
                409,
                "builtin_review_unsupported",
                "Builtin tool review state is fixed by the application.",
                authoritative_state=current,
            )
        if services.store is not None:
            updated = services.store.update_tool(
                capability.snapshot_id,
                capability.canonical_name,
                expected_revision=expected,
                review_state=mutation.review_state,
            )
            services.registry.refresh_from_manager(services.manager)
            operation_id = f"operation-{uuid4().hex}"
            _audit_mutation(
                services,
                actor=actor.subject,
                operation_id=operation_id,
                action="tool.reviewed",
                target=f"tool:{tool_name}",
                decision="allow",
                reason_code="tool_review_updated",
                before_revision=expected,
                after_revision=updated.revision,
                result=mutation.review_state,
            )
            return _operation_result(
                operation_id,
                updated.revision,
                _tool(services, tool_name),
            )
        raise _http_error(
            503, "capability_store_unavailable", "Store unavailable."
        )

    @router.post("/tools/{tool_name}/policies")
    async def create_tool_policy(
        tool_name: str,
        mutation: PolicyMutation,
        if_match: str | None = Header(default=None, alias="If-Match"),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "operator")
        expected = _revision(mutation.expected_revision, if_match)
        tool = _tool(services, tool_name)
        if tool["revision"] != expected:
            raise _http_error(
                409, "revision_conflict", "Tool registry revision conflict.",
                authoritative_state=tool,
            )
        rule = PolicyRule(
            id=mutation.rule_id or f"policy-{uuid4().hex}",
            server_id=tool["server_id"],
            tool_name=tool["canonical_name"],
            effect=mutation.effect,
            schemes=mutation.schemes,
            domains=mutation.domains,
            actions=mutation.actions,
            parameter_constraints=mutation.parameter_constraints,
            data_classes=mutation.data_classes,
            roles=mutation.roles,
            schema_hash=tool["schema_hash"],
            enabled=mutation.enabled,
            created_by=actor.subject,
        )
        if mutation.effect == "allowlist_auto":
            require_role(actor, "admin")
            confirmation = _management_confirmation(
                services,
                actor,
                operation="policy.create",
                target=tool_name,
                expected_revision=_registry_revision(services),
                diff={"policy": [None, _dump(rule)]},
                risk="automatic_execution_scope_expansion",
                impact=[f"tool:{tool_name}", *[f"domain:{d}" for d in rule.domains]],
                payload={
                    "rule": rule.model_dump(mode="json"),
                    "tool_revision": expected,
                },
            )
            return _confirmation_result(confirmation)
        if services.store is None:
            raise _http_error(503, "capability_store_unavailable", "Store unavailable.")
        try:
            services.store.create_policy_rule(rule)
        except RecordAlreadyExistsError as exc:
            raise _http_error(409, "policy_exists", "Policy already exists.") from exc
        snapshot = services.registry.notify_policy_changed()
        operation_id = f"operation-{uuid4().hex}"
        _audit_mutation(
            services,
            actor=actor.subject,
            operation_id=operation_id,
            action="policy.created",
            target=f"policy:{rule.id}",
            decision="allow",
            reason_code="policy_created",
            before_revision=None,
            after_revision=rule.revision,
            result=rule.effect,
        )
        return _operation_result(operation_id, snapshot.context_revision, rule)

    @router.get("/tools/{tool_name}/policies")
    async def list_tool_policies(
        tool_name: str,
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "viewer")
        tool = _tool(services, tool_name)
        rules = (
            []
            if services.store is None
            else services.store.list_policy_rules(
                tool["server_id"], tool["canonical_name"]
            )
        )
        return {"policies": [_dump(item) for item in rules]}

    @router.delete("/tools/{tool_name}/policies/{rule_id}")
    async def delete_tool_policy(
        tool_name: str,
        rule_id: str,
        mutation: ManagementMutation,
        if_match: str | None = Header(default=None, alias="If-Match"),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "admin")
        expected = _revision(mutation.expected_revision, if_match)
        tool = _tool(services, tool_name)
        if services.store is None:
            raise _http_error(503, "capability_store_unavailable", "Store unavailable.")
        current = services.store.get_policy_rule(rule_id)
        if (
            current is None
            or current.server_id != tool["server_id"]
            or current.tool_name != tool["canonical_name"]
        ):
            raise _http_error(404, "policy_not_found", "Policy not found.")
        operation_id = f"operation-{uuid4().hex}"
        try:
            removed = services.store.delete_policy_rule(
                rule_id, expected_revision=expected
            )
        except RevisionConflictError as exc:
            latest = services.store.get_policy_rule(rule_id)
            raise _http_error(
                409, "revision_conflict", "Policy revision conflict.",
                authoritative_state=_dump(latest),
            ) from exc
        services.registry.notify_policy_changed()
        _audit_mutation(
            services,
            actor=actor.subject,
            operation_id=operation_id,
            action="policy.deleted",
            target=f"policy:{rule_id}",
            decision="allow",
            reason_code="policy_deleted",
            before_revision=removed.revision,
            after_revision=None,
            result="deleted",
        )
        return _operation_result(operation_id, removed.revision, {"deleted": rule_id})

    @router.get("/skills")
    async def list_skills(
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "viewer")
        if services.skill_registry is None:
            return {"revision": 0, "skills": []}
        snapshot = services.skill_registry.snapshot()
        return {
            "revision": snapshot.revision,
            "stale": snapshot.stale,
            "last_error": snapshot.last_error,
            "skills": [
                {
                    "name": item.name,
                    "description": item.description,
                    "version": item.version,
                    "source": item.source,
                    "enabled": item.enabled,
                    "dependency_state": item.dependency_state,
                    "missing_dependencies": item.missing_dependencies,
                }
                for item in snapshot.skills
            ],
        }

    @router.get("/skills/{skill_name}")
    async def get_skill(
        skill_name: str,
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "viewer")
        skill, record = _skill_record(services, skill_name)
        return {"skill": _skill_metadata(skill), "record": _dump(record)}

    @router.get("/skills/{skill_name}/raw")
    async def get_skill_raw(
        skill_name: str,
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "admin")
        skill, record = _skill_record(services, skill_name)
        return {
            "name": skill.name,
            "definition": _redact_skill_definition(skill.definition),
            "snapshot_hash": skill.snapshot_hash,
            "revision": None if record is None else record.revision,
        }

    @router.get("/skills/{skill_name}/health")
    async def get_skill_health(
        skill_name: str,
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "viewer")
        skill, record = _skill_record(services, skill_name)
        return {
            "name": skill.name,
            "dependency_state": skill.dependency_state,
            "missing_dependencies": skill.missing_dependencies,
            "load_state": None if record is None else record.load_state,
            "last_error": None if record is None else record.last_error,
            "revision": None if record is None else record.revision,
        }

    async def change_skill_enabled(
        skill_name: str,
        enabled: bool,
        mutation: ManagementMutation,
        if_match: str | None,
        services: CapabilityApiServices,
        actor: ManagementPrincipal,
    ) -> dict[str, Any]:
        require_role(actor, "operator")
        expected = _revision(mutation.expected_revision, if_match)
        _skill_record(services, skill_name)
        try:
            services.skill_registry.set_enabled(
                skill_name, enabled, expected_revision=expected
            )
        except RevisionConflictError as exc:
            _, latest = _skill_record(services, skill_name)
            raise _http_error(
                409, "revision_conflict", "Skill revision conflict.",
                authoritative_state=_dump(latest),
            ) from exc
        latest_skill, latest_record = _skill_record(services, skill_name)
        revision = (
            services.skill_registry.snapshot().revision
            if latest_record is None
            else latest_record.revision
        )
        operation_id = f"operation-{uuid4().hex}"
        _audit_mutation(
            services,
            actor=actor.subject,
            operation_id=operation_id,
            action="skill.enabled" if enabled else "skill.disabled",
            target=f"skill:{skill_name}",
            decision="allow",
            reason_code="skill_state_updated",
            before_revision=expected,
            after_revision=revision,
            result="enabled" if enabled else "disabled",
        )
        return _operation_result(
            operation_id,
            revision,
            {"skill": _dump(latest_skill), "record": _dump(latest_record)},
        )

    @router.post("/skills/{skill_name}/enable")
    async def enable_skill(
        skill_name: str,
        mutation: ManagementMutation,
        if_match: str | None = Header(default=None, alias="If-Match"),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        return await change_skill_enabled(
            skill_name, True, mutation, if_match, services, actor
        )

    @router.post("/skills/{skill_name}/disable")
    async def disable_skill(
        skill_name: str,
        mutation: ManagementMutation,
        if_match: str | None = Header(default=None, alias="If-Match"),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        return await change_skill_enabled(
            skill_name, False, mutation, if_match, services, actor
        )

    @router.post("/skills/{skill_name}/reload", status_code=202)
    async def reload_skill(
        skill_name: str,
        mutation: ManagementMutation,
        if_match: str | None = Header(default=None, alias="If-Match"),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "admin")
        expected = _revision(mutation.expected_revision, if_match)
        skill, _record = _skill_record(services, skill_name)
        snapshot = services.skill_registry.snapshot()
        if snapshot.revision != expected:
            raise _http_error(
                409, "revision_conflict", "Skill registry revision conflict.",
                authoritative_state=_dump(snapshot),
            )
        try:
            candidate = services.skill_registry.prepare_reload(skill_name)
        except (SkillReloadError, KeyError) as exc:
            operation_id = f"operation-{uuid4().hex}"
            _audit_mutation(
                services,
                actor=actor.subject,
                operation_id=operation_id,
                action="skill.reload",
                target=f"skill:{skill_name}",
                decision="error",
                reason_code=getattr(exc, "args", ["skill_reload_failed"])[0],
                before_revision=snapshot.revision,
                after_revision=snapshot.revision,
                result="candidate_invalid",
            )
            raise _http_error(
                422,
                getattr(exc, "args", ["skill_reload_failed"])[0],
                "Skill reload candidate is invalid.",
                authoritative_state=_skill_metadata(skill),
            ) from exc
        confirmation = _management_confirmation(
            services,
            actor,
            operation="skill.reload",
            target=skill_name,
            expected_revision=expected,
            diff={"snapshot_hash": [skill.snapshot_hash, candidate.snapshot_hash]},
            risk="external_skill_definition_reload",
            impact=[f"skill:{skill_name}", "prompt_context"],
            payload={"candidate_hash": candidate.snapshot_hash},
        )
        return _confirmation_result(confirmation)

    @router.get("/confirmations/pending")
    async def pending_confirmations(
        session_id: str = Query(min_length=1, max_length=200),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "viewer")
        if services.confirmations is None:
            if services.store is None:
                return {"confirmations": []}
            values = services.store.list_confirmations(
                session_id=session_id, status="pending"
            )
        else:
            values = services.confirmations.list_pending(session_id=session_id)
        if session_id == "management":
            values = [
                item
                for item in values
                if item.server_id == "management"
                and (actor.role == "admin" or item.principal == actor.subject)
            ]
        else:
            values = [
                item
                for item in values
                if item.server_id != "management"
                and item.principal == actor.subject
            ]
        return {
            "confirmations": [
                _confirmation_authority(services, item) for item in values
            ]
        }

    @router.get("/confirmations/{confirmation_id}")
    async def get_confirmation(
        confirmation_id: str,
        session_id: str = Query(min_length=1, max_length=200),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "viewer")
        if services.confirmations is None:
            raise _http_error(503, "confirmation_service_unavailable", "Unavailable.")
        services.confirmations.list_pending(session_id=session_id)
        try:
            current = services.confirmations.get(confirmation_id)
        except RecordNotFoundError as exc:
            raise _http_error(404, "confirmation_not_found", "Not found.") from exc
        if current.session_id != session_id:
            raise _http_error(
                403,
                "confirmation_session_mismatch",
                "Confirmation is bound to a different session.",
            )
        if (current.server_id == "management") != (session_id == "management"):
            raise _http_error(
                403,
                "confirmation_namespace_mismatch",
                "Chat and management confirmations use separate namespaces.",
            )
        if current.server_id == "management":
            if actor.role != "admin" and current.principal != actor.subject:
                raise _http_error(403, "confirmation_principal_mismatch", "Forbidden.")
        elif current.principal != actor.subject:
            raise _http_error(403, "confirmation_principal_mismatch", "Forbidden.")
        return {"confirmation": _confirmation_authority(services, current)}

    @router.post("/confirmations/{confirmation_id}/decisions")
    async def decide_confirmation(
        confirmation_id: str,
        mutation: ConfirmationDecisionRequest,
        if_match: str | None = Header(default=None, alias="If-Match"),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "operator")
        if mutation.decision == "allowlist":
            require_role(actor, "admin")
        expected = _revision(mutation.expected_revision, if_match)
        if services.confirmations is None:
            raise _http_error(503, "confirmation_service_unavailable", "Unavailable.")
        try:
            current = services.confirmations.get(confirmation_id)
        except RecordNotFoundError as exc:
            raise _http_error(404, "confirmation_not_found", "Not found.") from exc
        is_management = current.server_id == "management"
        if is_management != (current.session_id == "management"):
            raise _http_error(
                403,
                "confirmation_namespace_mismatch",
                "Chat and management confirmations use separate namespaces.",
            )
        if is_management:
            if current.principal != actor.subject and actor.role != "admin":
                raise _http_error(403, "confirmation_principal_mismatch", "Forbidden.")
        elif current.principal != actor.subject:
            raise _http_error(403, "confirmation_principal_mismatch", "Forbidden.")
        if not is_management and mutation.session_id != current.session_id:
            raise _http_error(
                403,
                "confirmation_session_mismatch",
                "Confirmation is bound to a different session.",
            )
        if (
            is_management
            and mutation.session_id is not None
            and mutation.session_id != "management"
        ):
            raise _http_error(
                403,
                "confirmation_namespace_mismatch",
                "Management confirmations require the management session.",
            )
        replay = _replay_result(services, current, mutation)
        if replay is not None:
            replay_status, replay_response = replay
            if replay_status >= 400:
                raise HTTPException(
                    status_code=replay_status,
                    detail=replay_response.get("detail", replay_response),
                )
            return replay_response
        if is_management:
            require_role(actor, "admin")
            if current.principal != actor.subject:
                raise _http_error(403, "confirmation_principal_mismatch", "Forbidden.")
            # Re-authorize and revalidate authoritative state before deciding.
            if mutation.decision != "cancel":
                summary = dict(current.arguments_summary)
                operation = str(summary.get("operation", ""))
                target = str(summary.get("target", ""))
                expected_target = int(summary.get("expected_revision", -1))
                if operation.startswith("server."):
                    latest = _server_state(services, target)
                    if latest.revision != expected_target:
                        raise _http_error(
                            409, "revision_conflict", "Server changed before approval.",
                            authoritative_state=_dump(latest),
                        )
                elif operation == "tool.enable":
                    latest_tool = _tool(services, target)
                    if latest_tool["revision"] != expected_target:
                        raise _http_error(
                            409, "revision_conflict", "Tool changed before approval.",
                            authoritative_state=latest_tool,
                        )
                elif operation == "policy.create":
                    latest_revision = _registry_revision(services)
                    if latest_revision != expected_target:
                        raise _http_error(
                            409, "revision_conflict", "Registry changed before approval.",
                            authoritative_state={"revision": latest_revision},
                        )
                    payload = dict(summary.get("payload") or {})
                    latest_tool = _tool(services, target)
                    if latest_tool["revision"] != int(
                        payload.get("tool_revision", -1)
                    ):
                        raise _http_error(
                            409,
                            "revision_conflict",
                            "Tool changed before policy approval.",
                            authoritative_state=latest_tool,
                        )
                elif operation == "skill.reload":
                    latest = services.skill_registry.snapshot()
                    if latest.revision != expected_target:
                        raise _http_error(
                            409, "revision_conflict", "Skills changed before approval.",
                            authoritative_state=_dump(latest),
                        )
                    try:
                        candidate = services.skill_registry.prepare_reload(target)
                    except (SkillReloadError, KeyError) as exc:
                        services.confirmations.invalidate(
                            confirmation_id, reason_code="skill_candidate_invalid"
                        )
                        raise _http_error(
                            409,
                            "skill_candidate_invalid",
                            "Skill candidate can no longer be loaded.",
                        ) from exc
                    expected_hash = str(
                        (summary.get("payload") or {}).get("candidate_hash", "")
                    )
                    if candidate.snapshot_hash != expected_hash:
                        services.confirmations.invalidate(
                            confirmation_id, reason_code="skill_candidate_changed"
                        )
                        raise _http_error(
                            409,
                            "skill_candidate_changed",
                            "Skill candidate changed after confirmation.",
                            authoritative_state={
                                "confirmed_hash": expected_hash,
                                "current_hash": candidate.snapshot_hash,
                            },
                        )
        try:
            decided = services.confirmations.decide(
                confirmation_id,
                expected_revision=expected,
                idempotency_key=mutation.idempotency_key,
                decision=mutation.decision,
                actor=actor.subject,
            )
        except (RevisionConflictError, ConfirmationStateError) as exc:
            latest = services.confirmations.get(confirmation_id)
            raise _http_error(
                409,
                getattr(exc, "args", ["confirmation_conflict"])[0],
                "Confirmation conflict.",
                authoritative_state=_dump(latest),
            ) from exc
        if not is_management:
            authority = _confirmation_authority(services, decided)
            return {
                "operation_id": decided.turn_id,
                "revision": decided.revision,
                "state": authority,
                "confirmation": authority,
            }
        if mutation.decision == "cancel":
            result = _confirmation_result(decided)
            _record_management_terminal(
                services,
                decided,
                actor=actor.subject,
                status_code=200,
                response=result,
            )
            return result
        assert services.store is not None
        try:
            consumed = services.store.consume_confirmation_execution(
                confirmation_id,
                execution_idempotency_key=f"management:{mutation.idempotency_key}",
            )
        except Exception as exc:
            latest = services.confirmations.get(confirmation_id)
            raise _http_error(
                409, getattr(exc, "code", "confirmation_consumed"),
                "Confirmation cannot be executed again.",
                authoritative_state=_dump(latest),
            ) from exc
        try:
            result = await _execute_management(services, consumed)
        except asyncio.CancelledError:
            cancelled_response = {
                "detail": {
                    "code": "management_cancelled",
                    "message": "Management operation was cancelled.",
                    "operation_id": consumed.turn_id,
                }
            }
            _record_management_terminal(
                services,
                consumed,
                actor=actor.subject,
                status_code=499,
                response=cancelled_response,
            )
            raise
        except (SkillReloadError, SkillCandidateChangedError, RevisionConflictError) as exc:
            error = _http_error(
                409,
                getattr(exc, "args", ["management_failed"])[0],
                "Management operation failed after revalidation.",
            )
            error.detail["operation_id"] = consumed.turn_id
            _audit_mutation(
                services,
                actor=actor.subject,
                operation_id=consumed.turn_id,
                action=consumed.tool_name,
                target=consumed.destination,
                decision="error",
                reason_code=getattr(exc, "args", ["management_failed"])[0],
                before_revision=int(
                    consumed.arguments_summary.get("expected_revision", 0)
                ),
                after_revision=None,
                result="failed",
            )
            _record_management_terminal(
                services,
                consumed,
                actor=actor.subject,
                status_code=error.status_code,
                response={"detail": error.detail},
            )
            raise error from exc
        except HTTPException as exc:
            code = (
                exc.detail.get("code", "management_failed")
                if isinstance(exc.detail, dict)
                else "management_failed"
            )
            detail = (
                dict(exc.detail)
                if isinstance(exc.detail, dict)
                else {"code": code, "message": "Management operation failed."}
            )
            detail["operation_id"] = consumed.turn_id
            exc.detail = detail
            _audit_mutation(
                services,
                actor=actor.subject,
                operation_id=consumed.turn_id,
                action=consumed.tool_name,
                target=consumed.destination,
                decision="error",
                reason_code=code,
                before_revision=int(
                    consumed.arguments_summary.get("expected_revision", 0)
                ),
                after_revision=None,
                result="failed",
            )
            _record_management_terminal(
                services,
                consumed,
                actor=actor.subject,
                status_code=exc.status_code,
                response={"detail": detail},
            )
            raise
        except Exception as exc:
            error = _http_error(
                500,
                "management_execution_failed",
                "Management operation failed.",
            )
            error.detail["operation_id"] = consumed.turn_id
            _audit_mutation(
                services,
                actor=actor.subject,
                operation_id=consumed.turn_id,
                action=consumed.tool_name,
                target=consumed.destination,
                decision="error",
                reason_code="management_execution_failed",
                before_revision=int(
                    consumed.arguments_summary.get("expected_revision", 0)
                ),
                after_revision=None,
                result=type(exc).__name__,
            )
            _record_management_terminal(
                services,
                consumed,
                actor=actor.subject,
                status_code=error.status_code,
                response={"detail": error.detail},
            )
            raise error from exc
        _record_management_terminal(
            services,
            consumed,
            actor=actor.subject,
            status_code=200,
            response=result,
        )
        return result

    @router.get("/traces")
    async def traces(
        turn_id: str | None = None,
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "viewer")
        values = [] if services.store is None else services.store.list_audit_events()
        if turn_id is not None:
            values = [item for item in values if item.turn_id == turn_id]
        return {"traces": [_dump(item) for item in values]}

    @router.get("/context-snapshots/{session_id}")
    async def context_snapshot(
        session_id: UUID,
        turn_id: str,
        revision: int = Query(ge=0),
        services: CapabilityApiServices = Depends(get_capability_services),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        require_role(actor, "viewer")
        if services.store is None:
            raise _http_error(
                503, "capability_store_unavailable", "Store unavailable."
            )
        matching = [
            event
            for event in services.store.list_audit_events()
            if (
                event.action == "model.context.snapshot"
                and event.session_id == str(session_id)
                and event.turn_id == turn_id
                and event.payload.get("context_revision") == revision
            )
        ]
        if not matching:
            raise _http_error(
                404,
                "context_revision_unavailable",
                "No provider request used this context revision for the requested turn.",
            )
        event = matching[-1]
        return {
            "session_id": str(session_id),
            "turn_id": turn_id,
            "revision": revision,
            "model_call": event.payload.get("model_call"),
            "provider_tools_hash": event.payload.get("provider_tools_hash"),
            "callable_tools": event.payload.get("callable_tools", ()),
            "recorded_at": event.created_at,
        }

    return router
