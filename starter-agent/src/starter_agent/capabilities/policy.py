from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from typing import Any, Literal, Mapping, cast

from pydantic import Field

from starter_agent.capabilities.models import (
    BoundedJsonObject,
    CapabilityModel,
    PolicyRule,
)
from starter_agent.tools.adapters.safe_web_fetcher import (
    FetchFailure,
    Resolver,
    SafeWebFetcher,
    default_resolver,
)


DecisionOutcome = Literal["allow", "require_confirmation", "deny"]
_FORBIDDEN_ACTIONS = frozenset(
    {
        "login",
        "submit_application",
        "message",
        "upload_resume",
        "bypass_access_control",
    }
)
_ALWAYS_CONFIRM_ACTIONS = frozenset(
    {"click", "input", "upload", "submit", "script", "download", "storage_write"}
)
_AUTO_ACTIONS = frozenset({"read", "snapshot", "navigate", "navigation"})
_BROWSER_SENSITIVE = frozenset(
    {
        "resume",
        "resume_text",
        "cookie",
        "token",
        "password",
        "email_authorization",
        "personal_login",
        "credentials",
    }
)


class PolicyRequest(CapabilityModel):
    server_id: str = Field(min_length=1, max_length=160)
    tool_name: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=100)
    scheme: str | None = None
    domain: str | None = None
    arguments: BoundedJsonObject = Field(default_factory=dict)
    role: str = Field(default="user", min_length=1, max_length=100)
    data_classes: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    reviewed: bool = False
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    outcome: DecisionOutcome
    reason_code: str
    matched_rule_id: str | None = None


class ScopeDenied(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BrowserScopePolicy:
    """Gate-time URL checks backed by SafeWebFetcher's fetch-time validator."""

    def __init__(self, *, resolver: Resolver = default_resolver) -> None:
        self._fetcher = SafeWebFetcher(
            client=cast(Any, None),
            resolver=resolver,
            respect_robots=True,
        )

    async def validate_url(self, url: str):
        try:
            return await self._fetcher.validate_public_url(url)
        except FetchFailure as exc:
            raise ScopeDenied("unsafe_url") from exc

    async def validate_redirects(self, source: str, redirects: tuple[str, ...]):
        current = await self.validate_url(source)
        for target in redirects:
            try:
                current = await self.validate_url(target)
            except ScopeDenied as exc:
                raise ScopeDenied("unsafe_redirect") from exc
        return current

    @staticmethod
    def validate_outbound(
        data_classes: tuple[str, ...],
        size_bytes: int,
        *,
        max_bytes: int = 64_000,
    ) -> None:
        if {item.casefold() for item in data_classes} & _BROWSER_SENSITIVE:
            raise ScopeDenied("sensitive_outbound")
        if size_bytes < 0 or size_bytes > max_bytes:
            raise ScopeDenied("outbound_budget")


def validate_serpapi_payload(
    arguments: Mapping[str, Any],
    data_classes: tuple[str, ...],
) -> None:
    allowed_fields = {"keywords", "location"}
    allowed_classes = {"job_keywords", "location"}
    if set(arguments) - allowed_fields or set(data_classes) - allowed_classes:
        raise ScopeDenied("serpapi_fields")
    if not isinstance(arguments.get("keywords"), str):
        raise ScopeDenied("serpapi_fields")
    location = arguments.get("location")
    if location is not None and not isinstance(location, str):
        raise ScopeDenied("serpapi_fields")


def classify_tool(metadata: Mapping[str, Any], risk_level: str) -> str:
    action = metadata.get("action")
    if isinstance(action, str) and action:
        return action.casefold()
    annotations = metadata.get("annotations")
    if isinstance(annotations, Mapping):
        annotated = annotations.get("action")
        if isinstance(annotated, str) and annotated:
            return annotated.casefold()
        if annotations.get("destructiveHint") is True:
            return "storage_write"
        if annotations.get("readOnlyHint") is True:
            return "read"
    if risk_level == "read":
        return "read"
    return "unknown"


class ToolPolicy:
    def evaluate(
        self,
        request: PolicyRequest,
        rules: tuple[PolicyRule, ...] | list[PolicyRule],
    ) -> PolicyDecision:
        if not request.enabled:
            return PolicyDecision("deny", "disabled")
        action = request.action.casefold()
        if action in _FORBIDDEN_ACTIONS:
            return PolicyDecision("deny", "forbidden_action")

        matches = [rule for rule in rules if self._matches(rule, request)]
        denied = next((rule for rule in matches if rule.effect == "deny"), None)
        if denied is not None:
            return PolicyDecision("deny", "policy_deny", denied.id)
        if action in _ALWAYS_CONFIRM_ACTIONS:
            return PolicyDecision("require_confirmation", "always_confirm")
        explicit_always = next(
            (rule for rule in matches if rule.effect == "always_confirm"), None
        )
        if explicit_always is not None:
            return PolicyDecision(
                "require_confirmation", "always_confirm", explicit_always.id
            )
        allow = next(
            (rule for rule in matches if rule.effect == "allowlist_auto"), None
        )
        if request.reviewed and action in _AUTO_ACTIONS and allow is not None:
            return PolicyDecision("allow", "allowlist_auto", allow.id)
        confirm_once = next(
            (rule for rule in matches if rule.effect == "confirm_once"), None
        )
        if confirm_once is not None:
            return PolicyDecision(
                "require_confirmation", "confirm_once", confirm_once.id
            )
        required = next(
            (rule for rule in matches if rule.effect == "require_confirmation"), None
        )
        if required is not None:
            return PolicyDecision(
                "require_confirmation", "require_confirmation", required.id
            )
        reason = "unreviewed_tool" if not request.reviewed else "confirmation_required"
        if action == "unknown":
            reason = "unknown_action"
        return PolicyDecision("require_confirmation", reason)

    @staticmethod
    def _matches(rule: PolicyRule, request: PolicyRequest) -> bool:
        now = datetime.now(UTC)
        if not rule.enabled or (rule.expires_at is not None and rule.expires_at <= now):
            return False
        if rule.server_id != request.server_id or rule.tool_name != request.tool_name:
            return False
        if rule.schema_hash is not None:
            schema_hash = request.arguments.get("__schema_hash")
            if schema_hash is not None and schema_hash != rule.schema_hash:
                return False
        if rule.actions and request.action.casefold() not in {
            item.casefold() for item in rule.actions
        }:
            return False
        if rule.schemes and (request.scheme or "").casefold() not in {
            item.casefold() for item in rule.schemes
        }:
            return False
        if rule.domains:
            domain = (request.domain or "").casefold()
            if not any(
                pattern == "*" or fnmatchcase(domain, pattern.casefold())
                for pattern in rule.domains
            ):
                return False
        if rule.roles and request.role not in rule.roles:
            return False
        if rule.data_classes and not set(request.data_classes).issubset(rule.data_classes):
            return False
        return _parameters_match(request.arguments, rule.parameter_constraints)


def _parameters_match(
    arguments: Mapping[str, Any], constraints: Mapping[str, Any]
) -> bool:
    for name, constraint in constraints.items():
        if name not in arguments:
            return False
        value = arguments[name]
        if not isinstance(constraint, Mapping):
            if value != constraint:
                return False
            continue
        if "enum" in constraint and value not in constraint["enum"]:
            return False
        if "minimum" in constraint and value < constraint["minimum"]:
            return False
        if "maximum" in constraint and value > constraint["maximum"]:
            return False
    return True
