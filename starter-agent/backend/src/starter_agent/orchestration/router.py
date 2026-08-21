from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field

from starter_agent.orchestration.models import (
    BudgetSnapshot,
    OrchestrationModel,
    RouteDecision,
    RouteFallback,
    RouteName,
)


class RoutingRequest(OrchestrationModel):
    session_id: str = Field(min_length=1, max_length=160)
    turn_id: str = Field(min_length=1, max_length=160)
    run_id: str | None = Field(default=None, min_length=1, max_length=160)
    user_text: str = Field(min_length=1, max_length=20_000)
    explicit_route: RouteName | None = None
    provided_inputs: dict[str, Any] = Field(default_factory=dict)
    required_inputs: tuple[str, ...] = Field(default_factory=tuple, max_length=64)


class RoutingCapabilitySnapshot(OrchestrationModel):
    revision: str = Field(min_length=1, max_length=200)
    policy_revision: str = Field(min_length=1, max_length=200)
    enabled: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    disabled: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)

    def available(self, capability: str) -> bool:
        return capability in self.enabled and capability not in self.disabled


class ModelRouteClassification(OrchestrationModel):
    route: RouteName
    confidence: float = Field(ge=0, le=1)
    reason_code: str = Field(min_length=1, max_length=200)
    required_capabilities: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    top_candidate_margin: float | None = Field(default=None, ge=0, le=1)


class ExecutionRouter:
    """Pure route decision policy; it has no Tool or execution dependency."""

    def __init__(
        self,
        *,
        confidence_threshold: float = 0.7,
        candidate_margin: float = 0.1,
    ) -> None:
        if not 0 <= confidence_threshold <= 1:
            raise ValueError("confidence threshold must be between zero and one")
        if not 0 <= candidate_margin <= 1:
            raise ValueError("candidate margin must be between zero and one")
        self.confidence_threshold = confidence_threshold
        self.candidate_margin = candidate_margin

    def decide(
        self,
        request: RoutingRequest,
        *,
        capability_snapshot: RoutingCapabilitySnapshot,
        budget_snapshot: BudgetSnapshot,
        decision_id: str,
        created_at: datetime,
        model_classification: ModelRouteClassification | None = None,
        model_decision_id: str | None = None,
    ) -> RouteDecision:
        text = _normalize(request.user_text)
        signals = self._signals(text, request)

        # A hard budget stop cannot be escaped by choosing another tool/model
        # path.  Human review exposes recovery options without executing work.
        if budget_snapshot.phase == "stopped":
            return self._decision(
                request,
                capability_snapshot,
                decision_id=decision_id,
                created_at=created_at,
                route="human_review",
                confidence=1,
                reason_code="budget_unavailable",
                reason_summary=(
                    f"Budget is stopped on {budget_snapshot.stop_dimension or 'a hard limit'}; "
                    "no Tool or Plan execution may start."
                ),
                required_capabilities=(),
                risk_level="medium",
                missing_inputs=("budget_recovery_choice",),
                matched_rules=("rule:budget_hard_stop",),
                fallback=RouteFallback(
                    route="direct",
                    condition_code="report_completed_and_incomplete_only",
                ),
                status="clarification_required",
            )

        # Risk rules are authoritative and outrank explicit route preferences.
        if signals.high_risk:
            missing = self._missing_inputs(request, signals.required_inputs)
            conflicting = (
                (f"rule:user_explicit_{request.explicit_route}",)
                if request.explicit_route not in {None, "human_review"}
                else ()
            )
            return self._decision(
                request,
                capability_snapshot,
                decision_id=decision_id,
                created_at=created_at,
                route="human_review",
                confidence=1,
                reason_code=(
                    "high_risk_missing_input" if missing else "high_risk_action_requires_review"
                ),
                reason_summary=(
                    "The request can create an external or irreversible side effect and "
                    "must be confirmed by the existing Approval Gate."
                ),
                required_capabilities=signals.capabilities,
                risk_level="critical" if signals.critical else "high",
                missing_inputs=missing,
                matched_rules=("rule:high_risk_external_write",),
                conflicting_rules=conflicting,
                fallback=RouteFallback(
                    route="direct",
                    condition_code="user_cancels_external_action",
                    user_prompt="是否只生成草稿或说明，而不执行外部动作？",
                ),
                status="clarification_required" if missing else "accepted",
            )

        explicit_missing = self._missing_inputs(request, request.required_inputs)
        if explicit_missing:
            return self._clarification(
                request,
                capability_snapshot,
                decision_id=decision_id,
                created_at=created_at,
                reason_code="required_input_missing",
                missing_inputs=explicit_missing,
                model_decision_id=model_decision_id,
            )

        fixed = self._fixed_route(signals)
        if fixed is None and request.explicit_route is not None:
            fixed = _Candidate(
                route=request.explicit_route,
                reason_code="user_explicit_route",
                confidence=0.95,
                capabilities=_capabilities_for_route(request.explicit_route, signals),
                rule=f"rule:user_explicit_{request.explicit_route}",
            )
        if fixed is None and model_classification is not None:
            margin_low = (
                model_classification.top_candidate_margin is not None
                and model_classification.top_candidate_margin < self.candidate_margin
            )
            if (
                model_classification.confidence < self.confidence_threshold
                or margin_low
            ):
                return self._clarification(
                    request,
                    capability_snapshot,
                    decision_id=decision_id,
                    created_at=created_at,
                    reason_code="route_confidence_low",
                    missing_inputs=("task_intent",),
                    model_decision_id=model_decision_id,
                )
            fixed = _Candidate(
                route=model_classification.route,
                reason_code=model_classification.reason_code,
                confidence=model_classification.confidence,
                capabilities=model_classification.required_capabilities,
                rule="rule:model_classification",
            )
        if fixed is None:
            # The safe default covers tool-free explanations.  Factual requests
            # needing external evidence should be identified by a domain signal
            # or the optional structured classifier.
            fixed = _Candidate(
                route="direct",
                reason_code="tool_free_explanation",
                confidence=0.75,
                capabilities=(),
                rule="rule:direct_default",
            )

        unavailable = tuple(
            capability
            for capability in fixed.capabilities
            if not capability_snapshot.available(capability)
        )
        if unavailable:
            return self._decision(
                request,
                capability_snapshot,
                decision_id=decision_id,
                created_at=created_at,
                route="human_review",
                confidence=1,
                reason_code="required_capability_unavailable",
                reason_summary=(
                    "The selected route requires disabled or unavailable capabilities; "
                    "the router will not attempt execution and guess a replacement."
                ),
                required_capabilities=fixed.capabilities,
                risk_level="medium",
                missing_inputs=tuple(f"capability:{item}" for item in unavailable),
                matched_rules=(fixed.rule, "rule:capability_unavailable"),
                fallback=RouteFallback(
                    route="direct",
                    condition_code="explain_capability_unavailable",
                    user_prompt="是否仅返回可用能力说明，或启用所需能力后重试？",
                ),
                status="clarification_required",
                model_decision_id=model_decision_id,
            )

        conflicts = tuple(
            candidate.rule
            for candidate in signals.candidates
            if candidate.route != fixed.route
        )
        return self._decision(
            request,
            capability_snapshot,
            decision_id=decision_id,
            created_at=created_at,
            route=fixed.route,
            confidence=fixed.confidence,
            reason_code=fixed.reason_code,
            reason_summary=_reason_summary(fixed.route, fixed.reason_code),
            required_capabilities=fixed.capabilities,
            risk_level="medium" if fixed.route == "plan_delegation" else "low",
            matched_rules=(fixed.rule,),
            conflicting_rules=conflicts,
            fallback=_fallback_for(fixed.route),
            model_decision_id=model_decision_id,
        )

    def _clarification(
        self,
        request: RoutingRequest,
        capability_snapshot: RoutingCapabilitySnapshot,
        *,
        decision_id: str,
        created_at: datetime,
        reason_code: str,
        missing_inputs: tuple[str, ...],
        model_decision_id: str | None,
    ) -> RouteDecision:
        return self._decision(
            request,
            capability_snapshot,
            decision_id=decision_id,
            created_at=created_at,
            route="human_review",
            confidence=0,
            reason_code=reason_code,
            reason_summary="The task cannot be routed safely until the missing intent or input is confirmed.",
            required_capabilities=(),
            risk_level="medium",
            missing_inputs=missing_inputs,
            matched_rules=(f"rule:{reason_code}",),
            fallback=RouteFallback(
                route="direct",
                condition_code="user_requests_explanation_only",
                user_prompt="请补充任务对象和期望动作，或确认只需要解释。",
            ),
            status="clarification_required",
            model_decision_id=model_decision_id,
        )

    @staticmethod
    def _decision(
        request: RoutingRequest,
        capability_snapshot: RoutingCapabilitySnapshot,
        *,
        decision_id: str,
        created_at: datetime,
        route: RouteName,
        confidence: float,
        reason_code: str,
        reason_summary: str,
        required_capabilities: tuple[str, ...],
        risk_level: str,
        fallback: RouteFallback,
        missing_inputs: tuple[str, ...] = (),
        matched_rules: tuple[str, ...] = (),
        conflicting_rules: tuple[str, ...] = (),
        status: str = "accepted",
        model_decision_id: str | None = None,
    ) -> RouteDecision:
        return RouteDecision(
            route_decision_id=decision_id,
            run_id=request.run_id,
            session_id=request.session_id,
            turn_id=request.turn_id,
            route=route,
            confidence=confidence,
            reason_code=reason_code,
            reason_summary=reason_summary,
            required_capabilities=tuple(sorted(set(required_capabilities))),
            risk_level=risk_level,
            missing_inputs=missing_inputs,
            matched_rules=matched_rules,
            conflicting_rules=conflicting_rules,
            fallback=fallback,
            capability_snapshot_revision=capability_snapshot.revision,
            policy_revision=capability_snapshot.policy_revision,
            model_decision_id=model_decision_id,
            status=status,
            created_at=created_at,
        )

    @staticmethod
    def _missing_inputs(
        request: RoutingRequest,
        required: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(item for item in required if not request.provided_inputs.get(item))

    @staticmethod
    def _fixed_route(signals: "_Signals") -> "_Candidate | None":
        if signals.candidates:
            return signals.candidates[0]
        return None

    @staticmethod
    def _signals(text: str, request: RoutingRequest) -> "_Signals":
        high_risk = bool(_HIGH_RISK.search(text))
        critical = bool(_CRITICAL_RISK.search(text))
        email = bool(_EMAIL_ACTION.search(text))
        required_inputs = ("recipient", "content_ref") if email else ()
        high_risk_capabilities = ("send_email",) if email else ()
        candidates: list[_Candidate] = []
        if _WEEKLY_REPORT.search(text):
            candidates.append(
                _Candidate(
                    "workflow",
                    "fixed_job_weekly_report",
                    0.99,
                    ("job_weekly_report",),
                    "rule:fixed_job_weekly_report",
                )
            )
        if _COMPLEX_RESEARCH.search(text):
            required = ["planner"]
            if _PARALLEL_OR_BATCH.search(text):
                required.append("delegation")
            if _RESUME_EVIDENCE.search(text):
                required.append("resume_evidence_search")
            candidates.append(
                _Candidate(
                    "plan_delegation",
                    "complex_open_job_research",
                    0.95,
                    tuple(required),
                    "rule:complex_job_research",
                )
            )
        elif _SINGLE_JD.search(text):
            candidates.append(
                _Candidate(
                    "tool_loop",
                    "single_job_description_read",
                    0.95,
                    ("job_description_reader",),
                    "rule:single_jd_read",
                )
            )
        if _DIRECT_EXPLANATION.search(text):
            candidates.append(
                _Candidate(
                    "direct",
                    "tool_free_explanation",
                    0.9,
                    (),
                    "rule:direct_explanation",
                )
            )
        return _Signals(
            high_risk=high_risk,
            critical=critical,
            required_inputs=required_inputs,
            capabilities=high_risk_capabilities,
            candidates=tuple(candidates),
        )


class _Candidate:
    def __init__(
        self,
        route: RouteName,
        reason_code: str,
        confidence: float,
        capabilities: tuple[str, ...],
        rule: str,
    ) -> None:
        self.route = route
        self.reason_code = reason_code
        self.confidence = confidence
        self.capabilities = capabilities
        self.rule = rule


class _Signals:
    def __init__(
        self,
        *,
        high_risk: bool,
        critical: bool,
        required_inputs: tuple[str, ...],
        capabilities: tuple[str, ...],
        candidates: tuple[_Candidate, ...],
    ) -> None:
        self.high_risk = high_risk
        self.critical = critical
        self.required_inputs = required_inputs
        self.capabilities = capabilities
        self.candidates = candidates


_HIGH_RISK = re.compile(
    r"(?:发送|投递|提交申请|修改外部|删除|覆盖|send\b|apply\b|submit\b|delete\b|update external)",
    re.IGNORECASE,
)
_CRITICAL_RISK = re.compile(r"(?:删除|覆盖|delete\b|overwrite\b)", re.IGNORECASE)
_EMAIL_ACTION = re.compile(r"(?:发送.*(?:邮件|email)|send.*(?:mail|email))", re.IGNORECASE)
_WEEKLY_REPORT = re.compile(r"(?:求职周报|job search weekly report)", re.IGNORECASE)
_PARALLEL_OR_BATCH = re.compile(r"(?:并行|批量|后台|三个|3\s*(?:个|份)|parallel|batch|background|three)", re.IGNORECASE)
_RESUME_EVIDENCE = re.compile(r"(?:简历证据|resume evidence|简历.*(?:匹配|证据))", re.IGNORECASE)
_COMPLEX_RESEARCH = re.compile(
    r"(?:并行|批量|后台|三个|3\s*(?:个|份)|汇合后排序|JD.*简历证据|resume evidence|parallel|batch|background|rank.*jobs)",
    re.IGNORECASE,
)
_SINGLE_JD = re.compile(
    r"(?:(?:读取|阅读|分析|read|inspect).{0,16}(?:JD|job description)|https?://\S+)",
    re.IGNORECASE,
)
_DIRECT_EXPLANATION = re.compile(
    r"(?:解释|说明|什么是|确认|你好|hello|what is|explain)", re.IGNORECASE
)


def _normalize(text: str) -> str:
    return " ".join(text.strip().split())


def _capabilities_for_route(route: RouteName, signals: _Signals) -> tuple[str, ...]:
    if route == "workflow":
        return ("job_weekly_report",)
    if route == "tool_loop":
        return ("job_description_reader",)
    if route == "plan_delegation":
        return ("planner", "delegation")
    if route == "human_review":
        return signals.capabilities
    return ()


def _fallback_for(route: RouteName) -> RouteFallback:
    values = {
        "direct": ("human_review", "task_requires_external_capability"),
        "workflow": ("direct", "workflow_unavailable_explain_only"),
        "tool_loop": ("human_review", "tool_unavailable_or_requires_approval"),
        "plan_delegation": ("tool_loop", "plan_invalid_or_parallelism_unavailable"),
        "human_review": ("direct", "user_cancels_action"),
    }
    fallback_route, condition = values[route]
    return RouteFallback(route=fallback_route, condition_code=condition)


def _reason_summary(route: RouteName, reason_code: str) -> str:
    return {
        "direct": "The request is a bounded explanation or confirmation and needs no Tool or Plan.",
        "workflow": "The request matches an enabled fixed workflow with deterministic steps.",
        "tool_loop": "The request needs external observation but not a complete plan.",
        "plan_delegation": "The request is open or multi-part and requires a validated dependency plan.",
        "human_review": "The request requires clarification or approval before execution.",
    }[route] + f" Rule: {reason_code}."

