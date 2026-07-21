from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import math
import re
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from starter_agent.mcp.config import contains_high_confidence_secret


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

MAX_JSON_BYTES = 256_000
MAX_JSON_DEPTH = 20
MAX_JSON_NODES = 10_000
MAX_JSON_STRING_CHARS = 200_000


class FrozenJsonDict(dict[str, Any]):
    """A recursively immutable dict containing canonical JSON-compatible values."""

    def __init__(self, value: dict[str, Any] | None = None):
        dict.__init__(self)
        for key, item in (value or {}).items():
            dict.__setitem__(self, key, _freeze_json_value(item))

    @staticmethod
    def _immutable(*_args: Any, **_kwargs: Any) -> None:
        raise TypeError("bounded JSON objects are immutable")

    __delitem__ = _immutable
    __ior__ = _immutable
    __setitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable

    def __copy__(self) -> "FrozenJsonDict":
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> "FrozenJsonDict":
        return self


def _copy_json_value(value: Any, *, depth: int, counter: list[int]) -> Any:
    if depth > MAX_JSON_DEPTH:
        raise ValueError("JSON payload exceeds maximum depth")
    counter[0] += 1
    if counter[0] > MAX_JSON_NODES:
        raise ValueError("JSON payload exceeds maximum node count")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON payload contains a non-finite number")
        return value
    if isinstance(value, str):
        if len(value) > MAX_JSON_STRING_CHARS:
            raise ValueError("JSON payload string is too large")
        return value
    if type(value) in {list, tuple}:
        return [
            _copy_json_value(item, depth=depth + 1, counter=counter)
            for item in value
        ]
    if type(value) in {dict, FrozenJsonDict}:
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            copied[key] = _copy_json_value(
                item,
                depth=depth + 1,
                counter=counter,
            )
        return copied
    raise ValueError(f"JSON payload contains unsupported type: {type(value).__name__}")


def _prepare_json_object(value: Any) -> dict[str, Any]:
    if type(value) not in {dict, FrozenJsonDict}:
        raise ValueError("JSON payload must be an object")
    copied = _copy_json_value(value, depth=0, counter=[0])
    try:
        serialized = json.dumps(
            copied,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("JSON payload cannot be serialized") from exc
    if len(serialized.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("JSON payload exceeds maximum encoded size")
    return copied


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return FrozenJsonDict(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _freeze_json_object(value: dict[str, Any]) -> FrozenJsonDict:
    return FrozenJsonDict(value)


BoundedJsonObject = Annotated[
    dict[str, Any],
    BeforeValidator(_prepare_json_object),
    AfterValidator(_freeze_json_object),
]


def canonical_json_sha256(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|cookie|credential|pass(?:word|wd)?|secret|token)",
    flags=re.IGNORECASE,
)
_SECRET_TEXT = re.compile(
    r"(?:"
    r"bearer\s+\S+"
    r"|(?:api[_-]?key|authorization|cookie|password|secret|token)\s*[=:]\s*\S+"
    r")",
    flags=re.IGNORECASE,
)
_REDACTED_VALUES = {"***", "<redacted>", "[redacted]", "redacted"}


def _validate_safe_summary(value: dict[str, Any]) -> dict[str, Any]:
    def visit(node: Any, *, sensitive: bool = False) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                visit(
                    item,
                    sensitive=sensitive or bool(_SENSITIVE_KEY.search(str(key))),
                )
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                visit(item, sensitive=sensitive)
            return
        if isinstance(node, str):
            if sensitive and node.casefold() not in _REDACTED_VALUES:
                raise ValueError("capability summaries must not contain secrets")
            if _SECRET_TEXT.search(node) or contains_high_confidence_secret(node):
                raise ValueError("capability summaries must not contain secrets")

    visit(value)
    return value


class CapabilityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


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
    input_schema: BoundedJsonObject
    schema_hash: Sha256
    metadata: BoundedJsonObject = Field(default_factory=dict)
    risk_level: RiskLevel = "external"
    outbound_scope: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    enabled: bool = False
    review_state: ReviewState = "unreviewed"

    @model_validator(mode="after")
    def bind_schema_hash_to_input_schema(self) -> "Tool":
        if self.schema_hash != canonical_json_sha256(self.input_schema):
            raise ValueError("schema_hash does not match canonical input_schema")
        return self


class Resource(CapabilityModel):
    snapshot_id: Identifier
    server_id: Identifier
    name: ShortText
    uri: str | None = Field(default=None, max_length=2_000)
    uri_template: str | None = Field(default=None, max_length=2_000)
    description: str = Field(default="", max_length=10_000)
    mime_type: str | None = Field(default=None, max_length=200)
    parameters: tuple[BoundedJsonObject, ...] = Field(
        default_factory=tuple,
        max_length=100,
    )
    metadata: BoundedJsonObject = Field(default_factory=dict)
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
    arguments: tuple[BoundedJsonObject, ...] = Field(
        default_factory=tuple,
        max_length=100,
    )
    metadata: BoundedJsonObject = Field(default_factory=dict)
    enabled: bool = False


class PolicyRule(CapabilityModel):
    id: Identifier
    server_id: Identifier
    tool_name: ShortText
    effect: PolicyEffect
    schemes: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    domains: tuple[str, ...] = Field(default_factory=tuple, max_length=200)
    actions: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    parameter_constraints: BoundedJsonObject = Field(default_factory=dict)
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
    arguments_summary: BoundedJsonObject
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

    @model_validator(mode="after")
    def validate_terminal_state_fields(self) -> "Confirmation":
        if self.status == "pending":
            if (
                self.decision is not None
                or self.idempotency_key_hash is not None
                or self.decided_at is not None
            ):
                raise ValueError(
                    "pending status cannot have decision, idempotency hash, or decided_at"
                )
            return self
        if self.decided_at is None:
            raise ValueError(f"{self.status} status requires decided_at")
        if self.status == "approved":
            if self.decision not in {"once", "allowlist"}:
                raise ValueError("approved status requires an approval decision")
            if self.idempotency_key_hash is None:
                raise ValueError("approved status requires an idempotency hash")
            return self
        if self.status == "cancelled":
            if self.decision != "cancel" or self.idempotency_key_hash is None:
                raise ValueError(
                    "cancelled status requires cancel decision and idempotency hash"
                )
            return self
        if self.decision is not None or self.idempotency_key_hash is not None:
            raise ValueError(
                f"{self.status} status cannot have decision or idempotency hash"
            )
        return self


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
    payload: BoundedJsonObject = Field(default_factory=dict)
    created_at: UtcDateTime

    @model_validator(mode="after")
    def reject_persisted_secrets(self) -> "AuditEvent":
        _validate_safe_summary(
            {
                "persisted_text": [
                    self.event_id,
                    self.actor,
                    self.action,
                    self.target,
                    self.before_hash,
                    self.after_hash,
                    self.decision,
                    self.reason_code,
                    self.session_id,
                    self.turn_id,
                    self.call_id,
                ],
                "payload": self.payload,
            }
        )
        return self


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
