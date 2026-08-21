from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from starter_agent.capabilities.models import (
    BoundedJsonObject,
    Identifier,
    Sha256,
    UtcDateTime,
)
from starter_agent.mcp.config import contains_high_confidence_secret


RunType = Literal["fixture", "smoke"]
RunStatus = Literal[
    "queued",
    "running",
    "cancelling",
    "completed",
    "passed",
    "failed",
    "blocked",
    "cancelled",
    "error",
]
ResultStatus = Literal["passed", "failed", "blocked", "skipped", "error"]
GateStatus = Literal["passed", "blocked"]

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|cookie|credential|pass(?:word|wd)?|secret|token)",
    flags=re.IGNORECASE,
)
_SECRET_TEXT = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_-]?key|authorization|cookie|password|secret|token)\s*[=:]\s*\S+)"
)
_REDACTED_VALUES = {"***", "<redacted>", "[redacted]", "redacted"}
_SAFE_NONSECRET_VALUES = {
    "none",
    "no",
    "false",
    "not_present",
    "not present",
    "absent",
}


class TrustModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


def _assert_safe_summary(value: dict[str, Any]) -> dict[str, Any]:
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
            if (
                sensitive
                and node.casefold() not in _REDACTED_VALUES
                and node.casefold() not in _SAFE_NONSECRET_VALUES
            ):
                raise ValueError("trust summaries must not contain secrets")
            if _SECRET_TEXT.search(node) or contains_high_confidence_secret(node):
                raise ValueError("trust summaries must not contain secrets")

    visit(value)
    return value


class EvalSuite(TrustModel):
    id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=200)]
    version: Annotated[str, Field(min_length=1, max_length=120)]
    created_at: UtcDateTime
    case_ids: tuple[Identifier, ...] = ()
    metadata_summary: BoundedJsonObject = Field(default_factory=dict)

    @field_validator("metadata_summary")
    @classmethod
    def _metadata_is_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _assert_safe_summary(value)


class EvalFixture(TrustModel):
    id: Identifier
    fixture_type: Annotated[str, Field(min_length=1, max_length=80)]
    version: Annotated[str, Field(min_length=1, max_length=120)]
    manifest_hash: Sha256
    content_hash: Sha256
    source_ref: Annotated[str, Field(min_length=1, max_length=500)]
    summary: BoundedJsonObject
    redaction_summary: BoundedJsonObject

    @field_validator("summary", "redaction_summary")
    @classmethod
    def _summaries_are_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _assert_safe_summary(value)


class EvalCase(TrustModel):
    id: Identifier
    suite_id: Identifier
    version: Annotated[str, Field(min_length=1, max_length=120)]
    layer: Annotated[str, Field(min_length=1, max_length=120)]
    input_summary: BoundedJsonObject
    fixture_ids: tuple[Identifier, ...] = ()
    expected_outcome: BoundedJsonObject
    expected_tool_calls: tuple[BoundedJsonObject, ...] = ()
    deterministic_assertions: tuple[Annotated[str, Field(min_length=1, max_length=300)], ...] = ()
    judge_rubric: BoundedJsonObject | None = None
    safety_level: Annotated[str, Field(min_length=1, max_length=80)] = "standard"

    @field_validator(
        "input_summary",
        "expected_outcome",
        "judge_rubric",
    )
    @classmethod
    def _case_summaries_are_safe(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        return None if value is None else _assert_safe_summary(value)

    @field_validator("expected_tool_calls")
    @classmethod
    def _tool_call_summaries_are_safe(
        cls,
        value: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        for item in value:
            _assert_safe_summary(item)
        return value


class EvalRun(TrustModel):
    id: Identifier
    suite_id: Identifier
    run_type: RunType
    status: RunStatus
    started_at: UtcDateTime
    completed_at: UtcDateTime | None = None
    code_version: Annotated[str, Field(min_length=1, max_length=160)]
    code_dirty: bool
    prompt_version: Annotated[str, Field(min_length=1, max_length=160)]
    skill_version: Annotated[str, Field(min_length=1, max_length=160)]
    tool_schema_version: Annotated[str, Field(min_length=1, max_length=160)]
    policy_version: Annotated[str, Field(min_length=1, max_length=160)]
    fixture_manifest_hash: Sha256 | None = None
    config_summary: BoundedJsonObject = Field(default_factory=dict)

    @field_validator("config_summary")
    @classmethod
    def _config_summary_is_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _assert_safe_summary(value)


class EvalCaseResult(TrustModel):
    id: Identifier
    run_id: Identifier
    case_id: Identifier
    status: ResultStatus
    outcome_summary: BoundedJsonObject
    session_id: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    turn_id: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    trace_event_ids: tuple[Identifier, ...] = ()

    @field_validator("outcome_summary")
    @classmethod
    def _outcome_summary_is_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _assert_safe_summary(value)


class EvalAssertionResult(TrustModel):
    id: Identifier
    run_id: Identifier
    case_result_id: Identifier
    assertion_id: Annotated[str, Field(min_length=1, max_length=300)]
    status: ResultStatus
    expected_summary: BoundedJsonObject
    actual_summary: BoundedJsonObject

    @field_validator("expected_summary", "actual_summary")
    @classmethod
    def _assertion_summary_is_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _assert_safe_summary(value)


class EvalMetric(TrustModel):
    id: Identifier
    run_id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=160)]
    value: float | None = None
    numerator: float | None = None
    denominator: float | None = None
    unit: Annotated[str, Field(min_length=1, max_length=80)] = "count"
    missing: bool = False
    cost_usd: float | None = Field(default=None, ge=0)


class EvalFailureCluster(TrustModel):
    id: Identifier
    run_id: Identifier
    cluster_key: Annotated[str, Field(min_length=1, max_length=200)]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    case_result_ids: tuple[Identifier, ...] = ()
    root_cause_summary: BoundedJsonObject
    evidence_trace_event_ids: tuple[Identifier, ...] = ()

    @field_validator("root_cause_summary")
    @classmethod
    def _root_cause_summary_is_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _assert_safe_summary(value)


class EvalReleaseGate(TrustModel):
    id: Identifier
    run_id: Identifier
    status: GateStatus
    safety_blocking: bool
    blocking_reasons: tuple[Annotated[str, Field(min_length=1, max_length=300)], ...] = ()
    metric_snapshot: BoundedJsonObject

    @field_validator("metric_snapshot")
    @classmethod
    def _metric_snapshot_is_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _assert_safe_summary(value)


class TrustTraceEvent(TrustModel):
    id: Identifier
    eval_run_id: Identifier | None = None
    case_id: Identifier | None = None
    session_id: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    turn_id: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    model_request_id: Identifier | None = None
    tool_call_id: Identifier | None = None
    policy_decision_id: Identifier | None = None
    approval_id: Identifier | None = None
    child_run_id: Identifier | None = None
    parent_run_id: Identifier | None = None
    child_task_id: Identifier | None = None
    principal: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    access_level: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    parent_event_id: Identifier | None = None
    event_type: Annotated[str, Field(min_length=1, max_length=80)]
    status: Annotated[str, Field(min_length=1, max_length=80)]
    occurred_at: UtcDateTime
    summary: BoundedJsonObject
    payload_hash: Sha256
    source_ref: Annotated[str, Field(min_length=1, max_length=500)] | None = None

    @field_validator("summary")
    @classmethod
    def _summary_is_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _assert_safe_summary(value)


class SmokeRun(TrustModel):
    id: Identifier
    run_id: Identifier
    source_url: Annotated[str, Field(min_length=1, max_length=1000)]
    source_url_hash: Sha256
    trace_event_ids: tuple[Identifier, ...] = ()
    report_summary: BoundedJsonObject

    @field_validator("report_summary")
    @classmethod
    def _report_summary_is_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _assert_safe_summary(value)


class JudgeRubric(TrustModel):
    id: Identifier
    suite_id: Identifier
    version: Annotated[str, Field(min_length=1, max_length=120)]
    criteria: tuple[Annotated[str, Field(min_length=1, max_length=120)], ...]
    prompt_template: Annotated[str, Field(min_length=1, max_length=4000)]
    golden_examples: tuple[BoundedJsonObject, ...] = ()
    created_at: UtcDateTime

    @field_validator("golden_examples")
    @classmethod
    def _golden_examples_are_safe(
        cls,
        value: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        for item in value:
            _assert_safe_summary(item)
        return value


class JudgeResult(TrustModel):
    id: Identifier
    run_id: Identifier
    case_result_id: Identifier
    rubric_id: Identifier
    rubric_version: Annotated[str, Field(min_length=1, max_length=120)]
    provider: Annotated[str, Field(min_length=1, max_length=120)]
    model: Annotated[str, Field(min_length=1, max_length=160)]
    raw_score: float
    normalized_score: float = Field(ge=0, le=1)
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    usage_summary: BoundedJsonObject
    created_at: UtcDateTime

    @field_validator("usage_summary")
    @classmethod
    def _usage_summary_is_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _assert_safe_summary(value)


class HumanReview(TrustModel):
    id: Identifier
    run_id: Identifier
    case_id: Identifier
    case_result_id: Identifier
    rubric_id: Identifier | None = None
    rubric_version: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    reviewer: Annotated[str, Field(min_length=1, max_length=160)]
    conclusion: Annotated[str, Field(min_length=1, max_length=120)]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    created_at: UtcDateTime
