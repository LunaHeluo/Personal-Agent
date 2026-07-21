from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("capability timestamps must include a UTC offset")
    return value.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(_as_utc)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]
Identifier = Annotated[
    str,
    Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]

ConnectionState = Literal[
    "disconnected",
    "connecting",
    "ready",
    "degraded",
    "failed",
    "closed",
]
HealthState = Literal["unknown", "healthy", "unhealthy"]
OperationState = Literal[
    "idle",
    "validating_config",
    "starting_candidate",
    "initializing",
    "discovering",
    "validating_snapshot",
    "swapping",
    "ready",
    "draining",
    "degraded",
]
RiskLevel = Literal["read", "write", "external", "dangerous"]
ReviewState = Literal["unreviewed", "approved", "review_required", "rejected"]
PolicyEffect = Literal[
    "deny",
    "always_confirm",
    "allowlist_auto",
    "confirm_once",
    "require_confirmation",
]
ConfirmationDecision = Literal["once", "allowlist", "cancel"]
ConfirmationStatus = Literal[
    "pending",
    "approved",
    "cancelled",
    "expired",
    "invalidated",
]
SkillLoadState = Literal[
    "loaded",
    "stale",
    "error",
    "dependency_unavailable",
    "disabled",
]

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|cookie|credential|pass(?:word|wd)?|secret|token)",
    flags=re.IGNORECASE,
)
_SECRET_TEXT = re.compile(
    r"(?:bearer\s+\S+|sk-[A-Za-z0-9_-]{8,}|"
    r"(?:api[_-]?key|authorization|cookie|password|secret|token)\s*[=:]\s*\S+)",
    flags=re.IGNORECASE,
)
_REDACTED_VALUES = {"***", "<redacted>", "[redacted]", "redacted"}


def _validate_safe_summary(value: dict[str, Any]) -> dict[str, Any]:
    def visit(node: Any, *, sensitive: bool = False) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                visit(item, sensitive=bool(_SENSITIVE_KEY.search(str(key))))
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                visit(item, sensitive=sensitive)
            return
        if isinstance(node, str):
            if sensitive and node.casefold() not in _REDACTED_VALUES:
                raise ValueError("capability summaries must not contain secrets")
            if _SECRET_TEXT.search(node):
                raise ValueError("capability summaries must not contain secrets")

    visit(value)
    return value


class CapabilityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Server(CapabilityModel):
    id: Identifier
    name: ShortText
    config_source: str = Field(min_length=1, max_length=500)
    config_hash: Sha256
    enabled: bool = True
    connection_state: ConnectionState = "disconnected"
    health_state: HealthState = "unknown"
    operation_state: OperationState = "idle"
    protocol_version: str | None = Field(default=None, max_length=100)
    runtime_name: str | None = Field(default=None, max_length=200)
    runtime_version: str | None = Field(default=None, max_length=100)
    transport: Literal["stdio"] = "stdio"
    pid: int | None = Field(default=None, ge=1)
    exit_code: int | None = None
    last_error: str | None = Field(default=None, max_length=2_000)
    last_checked_at: UtcDateTime | None = None
    revision: int = Field(default=0, ge=0)


class Snapshot(CapabilityModel):
    id: Identifier
    server_id: Identifier
    version: int = Field(ge=1)
    schema_hash: Sha256
    discovered_at: UtcDateTime
    stale: bool = False
    active: bool = False
    tool_count: int = Field(default=0, ge=0, le=100_000)
    resource_count: int = Field(default=0, ge=0, le=100_000)
    prompt_count: int = Field(default=0, ge=0, le=100_000)
    error: str | None = Field(default=None, max_length=2_000)


class Tool(CapabilityModel):
    snapshot_id: Identifier
    server_id: Identifier
    upstream_name: ShortText
    model_alias: ShortText
    title: str | None = Field(default=None, max_length=500)
    description: str = Field(default="", max_length=10_000)
    input_schema: dict[str, Any]
    schema_hash: Sha256
    risk_level: RiskLevel = "external"
    outbound_scope: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    enabled: bool = False
    review_state: ReviewState = "unreviewed"


class Resource(CapabilityModel):
    snapshot_id: Identifier
    server_id: Identifier
    name: ShortText
    uri: str | None = Field(default=None, max_length=2_000)
    uri_template: str | None = Field(default=None, max_length=2_000)
    description: str = Field(default="", max_length=10_000)
    mime_type: str | None = Field(default=None, max_length=200)
    parameters: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        max_length=100,
    )
    enabled: bool = False

    @model_validator(mode="after")
    def require_uri_or_template(self) -> "Resource":
        if not self.uri and not self.uri_template:
            raise ValueError("MCP resource requires a uri or uri_template")
        return self


class Prompt(CapabilityModel):
    snapshot_id: Identifier
    server_id: Identifier
    name: ShortText
    description: str = Field(default="", max_length=10_000)
    arguments: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        max_length=100,
    )
    enabled: bool = False


class PolicyRule(CapabilityModel):
    id: Identifier
    server_id: Identifier
    tool_name: ShortText
    effect: PolicyEffect
    schemes: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    domains: tuple[str, ...] = Field(default_factory=tuple, max_length=200)
    actions: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    parameter_constraints: dict[str, Any] = Field(default_factory=dict)
    data_classes: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    schema_hash: Sha256 | None = None
    expires_at: UtcDateTime | None = None
    enabled: bool = True
    created_by: ShortText
    revision: int = Field(default=0, ge=0)


class Confirmation(CapabilityModel):
    id: Identifier
    principal: ShortText
    session_id: Identifier
    turn_id: Identifier
    call_id: Identifier
    request_hash: Sha256
    server_id: Identifier
    tool_name: ShortText
    schema_hash: Sha256
    arguments_summary: dict[str, Any]
    risk: RiskLevel
    destination: str = Field(min_length=1, max_length=500)
    decision: ConfirmationDecision | None = None
    status: ConfirmationStatus = "pending"
    expires_at: UtcDateTime
    idempotency_key_hash: Sha256 | None = None
    decided_at: UtcDateTime | None = None
    revision: int = Field(default=0, ge=0)

    @field_validator("arguments_summary")
    @classmethod
    def reject_unredacted_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_safe_summary(value)


class AuditEvent(CapabilityModel):
    event_id: Identifier
    actor: ShortText
    action: ShortText
    target: str = Field(min_length=1, max_length=500)
    before_hash: Sha256 | None = None
    after_hash: Sha256 | None = None
    decision: Literal[
        "allow",
        "deny",
        "require_confirmation",
        "approved",
        "cancelled",
        "error",
    ]
    reason_code: ShortText
    session_id: Identifier | None = None
    turn_id: Identifier | None = None
    call_id: Identifier | None = None
    created_at: UtcDateTime


class ExecutionPermit(CapabilityModel):
    id: Identifier
    confirmation_id: Identifier | None = None
    request_hash: Sha256
    policy_revision: int = Field(ge=0)
    expires_at: UtcDateTime
    consumed_at: UtcDateTime | None = None


class SkillRecord(CapabilityModel):
    name: Identifier
    source_path: str = Field(min_length=1, max_length=500)
    version: str = Field(min_length=1, max_length=100)
    updated_at: UtcDateTime
    enabled: bool = False
    load_state: SkillLoadState
    snapshot_hash: Sha256
    dependencies: tuple[str, ...] = Field(default_factory=tuple, max_length=200)
    last_error: str | None = Field(default=None, max_length=2_000)
    revision: int = Field(default=0, ge=0)
