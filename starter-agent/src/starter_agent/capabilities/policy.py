from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
import re
from typing import Any, Literal, Mapping, cast
from urllib.parse import parse_qsl, urlsplit

from pydantic import Field

from starter_agent.capabilities.models import (
    BoundedJsonObject,
    CapabilityModel,
    PolicyRule,
)
from starter_agent.mcp.config import contains_high_confidence_secret
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
        "input",
        "fill",
        "type",
        "upload",
        "submit",
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
    schema_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    scheme: str | None = None
    domain: str | None = None
    arguments: BoundedJsonObject = Field(default_factory=dict)
    role: str = Field(default="user", min_length=1, max_length=100)
    data_classes: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    target_scopes: tuple[tuple[str, str], ...] = Field(
        default_factory=tuple, max_length=100
    )
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

    async def validate_all(self, targets: tuple[str, ...]):
        if not targets:
            raise ScopeDenied("unsafe_url")
        validated = []
        for target in targets:
            validated.append(await self.validate_url(target))
        return tuple(validated)

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
    *,
    max_bytes: int = 2_000,
) -> None:
    allowed_fields = {"query", "keywords", "location"}
    allowed_classes = {"job_keywords", "location"}
    if set(arguments) - allowed_fields or set(data_classes) - allowed_classes:
        raise ScopeDenied("serpapi_fields")
    if ("query" in arguments) == ("keywords" in arguments):
        raise ScopeDenied("serpapi_fields")
    query = arguments.get("query", arguments.get("keywords"))
    if not _safe_job_search_phrase(query):
        raise ScopeDenied("serpapi_fields")
    location = arguments.get("location")
    if location is not None and not _safe_search_phrase(location, 80, 10):
        raise ScopeDenied("serpapi_fields")
    encoded = json_bytes(arguments)
    if len(encoded) > max_bytes:
        raise ScopeDenied("serpapi_fields")


def json_bytes(value: Any) -> bytes:
    import json

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _safe_short_text(value: Any, limit: int) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        return False
    lowered = value.casefold()
    return not (
        contains_high_confidence_secret(value)
        or any(
            marker in lowered
            for marker in ("bearer ", "token=", "password=", "cookie=", "secret=")
        )
    )


_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PHONE = re.compile(r"(?:\+?\d[\d\s().-]{8,}\d)")
_FIRST_PERSON_HISTORY = re.compile(
    r"\b(?:i|i've|my)\s+(?:led|worked|managed|built|experience|resume|cv)\b",
    re.IGNORECASE,
)
_YEAR_OR_DATE = re.compile(
    r"\b(?:19|20)\d{2}\b|\b\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?\b"
)
_EMPLOYMENT_HISTORY = re.compile(
    r"\b(?:employment|employed|worked|working|experience)\b|"
    r"\b(?:from|between)\b.{0,40}\b(?:to|and)\b",
    re.IGNORECASE,
)
_RESUME_ACTION = re.compile(
    r"\b(?:led|delivered|managed|built|achieved|worked|developed|responsible|"
    r"owned|drove|implemented|created|launched|improved|increased|reduced)\b|"
    r"(?:任职|负责|主导|交付|从事)",
    re.IGNORECASE,
)
_SENTENCE_CONNECTOR = re.compile(
    r"\b(?:and|or|but|for|with|from|to|at|during|while|where|who|that)\b",
    re.IGNORECASE,
)
_PERSON_COMPANY_HISTORY = re.compile(
    r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b.{0,80}"
    r"\b(?:at|from|with)\b.{0,40}\b[A-Z][A-Za-z0-9&.-]+\b"
)
_JOB_INTENT = re.compile(
    r"\b(?:ai|ml|pm|qa|sre|devops|product|program|project|engineering|engineer|"
    r"manager|finance|accountant|developer|architect|designer|analyst|scientist|"
    r"researcher|recruiter|hr|consultant|sales|marketing|security|frontend|backend|"
    r"fullstack|data|python|java|javascript|typescript|react|kubernetes|cloud|"
    r"platform|software|hardware|operations)\b|"
    r"(?:产品经理|财务经理|工程师|开发|分析师|设计师|运营|市场|销售|财务|会计|"
    r"招聘|人力|项目|顾问|研究员|架构师|测试|运维)",
    re.IGNORECASE,
)


def _safe_search_phrase(value: Any, limit: int, words: int) -> bool:
    return bool(
        _safe_short_text(value, limit)
        and "\n" not in value
        and "\r" not in value
        and len(value.split()) <= words
        and not _EMAIL.search(value)
        and not _PHONE.search(value)
        and not _FIRST_PERSON_HISTORY.search(value)
    )


def _safe_job_search_phrase(value: Any) -> bool:
    return bool(
        _safe_search_phrase(value, 160, 20)
        and len(value.split()) <= 6
        and len(value) <= 60
        and _JOB_INTENT.search(value)
        and not _YEAR_OR_DATE.search(value)
        and not _EMPLOYMENT_HISTORY.search(value)
        and not _PERSON_COMPANY_HISTORY.search(value)
        and not _RESUME_ACTION.search(value)
        and not _SENTENCE_CONNECTOR.search(value)
    )


def validate_browser_payload(action: str, arguments: Mapping[str, Any]) -> None:
    action = action.casefold()
    if action in _FORBIDDEN_ACTIONS:
        raise ScopeDenied("forbidden_action")
    if action == "click":
        allowed = {"selector", "ref", "button", "timeout"}
        if not arguments or any(str(key).casefold() not in allowed for key in arguments):
            raise ScopeDenied("browser_payload")
        if "button" in arguments and arguments["button"] not in {"left", "middle", "right"}:
            raise ScopeDenied("browser_payload")
        for key in ("selector", "ref"):
            value = arguments.get(key)
            if value is not None and not _safe_short_text(value, 500):
                raise ScopeDenied("browser_payload")
        return
    if action == "script":
        if (
            "script" not in arguments
            or set(arguments) - {"script", "url"}
            or not _is_provably_read_only_script(
            arguments.get("script")
            )
        ):
            raise ScopeDenied("browser_script")
        return
    if action not in _AUTO_ACTIONS:
        return
    allowed = {
        "url",
        "uri",
        "selector",
        "ref",
        "timeout",
        "wait_until",
        "redirect",
        "redirects",
        "final_url",
    }
    if any(str(key).casefold() not in allowed for key in arguments):
        raise ScopeDenied("browser_payload")


_READ_ONLY_SCRIPT = re.compile(
    r"^\s*return\s+(?:"
    r"document\.(?:title|URL)|window\.location\.href|"
    r"document\.querySelector\((['\"])[^\r\n]{1,300}\1\)\."
    r"(?:textContent|innerText|getAttribute\((['\"])[A-Za-z_:][-A-Za-z0-9_:.]*\2\))"
    r")\s*;?\s*$"
)


def _is_provably_read_only_script(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 500:
        return False
    if (
        _EMAIL.search(value)
        or _PHONE.search(value)
        or _FIRST_PERSON_HISTORY.search(value)
        or any(term in value.casefold() for term in ("resume", "curriculum vitae"))
    ):
        return False
    return _READ_ONLY_SCRIPT.fullmatch(value) is not None


def reject_sensitive_url_query(url: str) -> None:
    try:
        values = [item for pair in parse_qsl(urlsplit(url).query) for item in pair]
    except ValueError as exc:
        raise ScopeDenied("unsafe_url") from exc
    text = " ".join(values)
    if (
        _EMAIL.search(text)
        or _PHONE.search(text)
        or _FIRST_PERSON_HISTORY.search(text)
        or any(term in text.casefold() for term in ("resume", "curriculum vitae"))
    ):
        raise ScopeDenied("sensitive_url_query")


def extract_url_targets(arguments: Mapping[str, Any]) -> tuple[str, ...]:
    targets: list[str] = []

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, Mapping):
            for nested_key, nested in value.items():
                visit(nested, str(nested_key))
            return
        if isinstance(value, (list, tuple)):
            for nested in value:
                visit(nested, key)
            return
        normalized = key.casefold().replace("-", "_")
        if normalized.endswith(("url", "uri")) or normalized in {
            "redirect",
            "redirects",
            "final_url",
        }:
            if not isinstance(value, str):
                raise ScopeDenied("unsafe_url")
            targets.append(value)

    visit(arguments)
    return tuple(targets)


def infer_data_classes(
    arguments: Mapping[str, Any],
    *,
    schema: Mapping[str, Any],
    metadata: Mapping[str, Any],
    claimed: tuple[str, ...],
) -> frozenset[str]:
    classes = {item.casefold() for item in claimed}
    annotated = metadata.get("data_classes", ())
    if isinstance(annotated, (list, tuple)):
        classes.update(str(item).casefold() for item in annotated)

    def visit(value: Any, key: str = "") -> None:
        normalized = key.casefold().replace("-", "_")
        if "resume" in normalized or normalized in {"cv", "cv_text"}:
            classes.add("resume")
        if any(part in normalized for part in ("cookie", "token", "authorization")):
            classes.add("token")
        if any(part in normalized for part in ("password", "credential", "secret")):
            classes.add("credentials")
        if "email" in normalized and "auth" in normalized:
            classes.add("email_authorization")
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child, key)
        elif isinstance(value, str):
            lowered = value.casefold()
            if contains_high_confidence_secret(value) or "bearer " in lowered:
                classes.add("token")

    visit(arguments)
    visit(schema)
    return frozenset(classes)


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
        if rule.schema_hash is not None and rule.schema_hash != request.schema_hash:
            return False
        if rule.actions and request.action.casefold() not in {
            item.casefold() for item in rule.actions
        }:
            return False
        scopes = request.target_scopes or (((request.scheme or ""), (request.domain or "")),)
        for scheme, domain in scopes:
            if rule.schemes and scheme.casefold() not in {
                item.casefold() for item in rule.schemes
            }:
                return False
            if rule.domains and not any(
                pattern == "*" or fnmatchcase(domain.casefold(), pattern.casefold())
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
