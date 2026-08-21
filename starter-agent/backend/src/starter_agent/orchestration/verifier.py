from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import Field

from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.orchestration.models import (
    BudgetSnapshot,
    JudgeResultSummary,
    ModelDecision,
    OrchestrationModel,
    VerifyFailure,
    VerifyResult,
)


class BusinessRuleFailure(OrchestrationModel):
    rule_id: str = Field(min_length=1, max_length=160)
    path: str = Field(min_length=1, max_length=200)
    expected: Mapping[str, Any] = Field(default_factory=dict)
    actual_summary: Mapping[str, Any] = Field(default_factory=dict)
    severity: str = Field(default="error", pattern="^(info|warning|error|critical)$")
    repairable: bool = True


class CitationClaim(OrchestrationModel):
    path: str = Field(min_length=1, max_length=200)
    source_ref: str | None = Field(default=None, max_length=500)


class RuntimeVerifyRequest(OrchestrationModel):
    parent_run_id: str = Field(min_length=1, max_length=160)
    plan_id: str | None = Field(default=None, max_length=160)
    step_id: str | None = Field(default=None, max_length=160)
    output_ref: str = Field(min_length=1, max_length=500)
    output: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    permission_allowed: bool
    permission_decision_ref: str | None = Field(default=None, max_length=500)
    source_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=2_000)
    authorized_source_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=2_000)
    citation_claims: tuple[CitationClaim, ...] = Field(default_factory=tuple, max_length=2_000)
    business_rule_failures: tuple[BusinessRuleFailure, ...] = Field(
        default_factory=tuple, max_length=1_000
    )
    budget_snapshot: BudgetSnapshot
    product_rubric: Mapping[str, Any] | None = None


JudgeCallable = Callable[[Mapping[str, Any], Mapping[str, Any]], JudgeResultSummary]


class RuntimeVerifier:
    """Online Run verifier. It never compares versions or publishes releases."""

    def verify(
        self,
        request: RuntimeVerifyRequest,
        *,
        created_at: datetime,
        judge: JudgeCallable | None = None,
        judge_model_decision: ModelDecision | None = None,
    ) -> VerifyResult:
        failures: list[VerifyFailure] = []
        verified: list[str] = []
        if request.permission_allowed:
            verified.append("permission")
        else:
            failures.append(
                self._failure(
                    request,
                    rule_id="permission.allowed",
                    path="$",
                    severity="critical",
                    repairable=False,
                    expected={"allowed": True},
                    actual={"allowed": False},
                    refs=(request.permission_decision_ref,) if request.permission_decision_ref else (),
                )
            )

        schema_errors = tuple(
            sorted(
                Draft202012Validator(
                    request.output_schema, format_checker=FormatChecker()
                ).iter_errors(dict(request.output)),
                key=lambda item: tuple(str(part) for part in item.absolute_path),
            )
        )
        if not schema_errors:
            verified.append("schema")
        for error in schema_errors:
            path = "$" + "".join(f"[{part!r}]" for part in error.absolute_path)
            failures.append(
                self._failure(
                    request,
                    rule_id="schema.valid",
                    path=path,
                    severity="error",
                    repairable=True,
                    expected={"validator": error.validator, "value": error.validator_value},
                    actual={"message": error.message[:500]},
                )
            )

        if not request.business_rule_failures:
            verified.append("business_rules")
        for rule in request.business_rule_failures:
            failures.append(
                self._failure(
                    request,
                    rule_id=rule.rule_id,
                    path=rule.path,
                    severity=rule.severity,
                    repairable=rule.repairable,
                    expected=rule.expected,
                    actual=rule.actual_summary,
                )
            )

        unauthorized = tuple(
            item for item in request.source_refs if item not in set(request.authorized_source_refs)
        )
        if not unauthorized:
            verified.append("sources")
        for source in unauthorized:
            failures.append(
                self._failure(
                    request,
                    rule_id="source.authorized",
                    path="$.sources",
                    severity="critical",
                    repairable=False,
                    expected={"authorized": True},
                    actual={"source_ref": source},
                    refs=(source,),
                )
            )

        missing_citations = tuple(
            claim for claim in request.citation_claims if claim.source_ref is None
        )
        invalid_citations = tuple(
            claim
            for claim in request.citation_claims
            if claim.source_ref is not None and claim.source_ref not in request.source_refs
        )
        if not missing_citations and not invalid_citations:
            verified.append("citations")
        for claim in (*missing_citations, *invalid_citations):
            failures.append(
                self._failure(
                    request,
                    rule_id="citation.complete",
                    path=claim.path,
                    severity="error",
                    repairable=True,
                    expected={"source_ref": "present_and_declared"},
                    actual={"source_ref": claim.source_ref},
                    refs=(claim.source_ref,) if claim.source_ref else (),
                )
            )

        budget_ok = self._budget_consistent(request.budget_snapshot)
        if budget_ok:
            verified.append("budget")
        else:
            failures.append(
                self._failure(
                    request,
                    rule_id="budget.consistent",
                    path="$.budget",
                    severity="critical",
                    repairable=False,
                    expected={"within_limit": True},
                    actual={
                        "phase": request.budget_snapshot.phase,
                        "stop_dimension": request.budget_snapshot.stop_dimension,
                    },
                    refs=(request.budget_snapshot.budget_snapshot_id,),
                )
            )

        judge_result: JudgeResultSummary | None = None
        judge_decision_id: str | None = None
        if not failures and request.product_rubric is not None and judge is not None:
            if judge_model_decision is None:
                raise ValueError("judge_model_decision_required")
            judge_result = judge(request.product_rubric, request.output)
            judge_decision_id = judge_model_decision.model_decision_id
            if judge_result.passed:
                verified.append("product_rubric")
            else:
                failures.append(
                    self._failure(
                        request,
                        rule_id="rubric.semantic_quality",
                        path="$",
                        severity="error",
                        repairable=True,
                        expected={"passed": True},
                        actual={"scores": judge_result.rubric_scores},
                    )
                )
        elif not failures and request.product_rubric is not None:
            verified.append("product_rubric_skipped")

        decision = self._decision(failures)
        verify_id = f"verify:{canonical_json_sha256({'parent': request.parent_run_id, 'output': request.output_ref, 'failures': [item.failure_id for item in failures]})[:32]}"
        return VerifyResult(
            verify_id=verify_id,
            parent_run_id=request.parent_run_id,
            plan_id=request.plan_id,
            step_id=request.step_id,
            output_ref=request.output_ref,
            passed=not failures,
            verified_items=tuple(verified),
            failures=tuple(failures),
            deterministic_result={
                "permission": request.permission_allowed,
                "schema_error_count": len(schema_errors),
                "business_rule_failure_count": len(request.business_rule_failures),
                "unauthorized_source_count": len(unauthorized),
                "citation_failure_count": len(missing_citations) + len(invalid_citations),
                "budget_consistent": budget_ok,
            },
            judge_result=judge_result,
            judge_model_decision_id=judge_decision_id,
            decision=decision,
            budget_snapshot_id=request.budget_snapshot.budget_snapshot_id,
            created_at=created_at,
        )

    @staticmethod
    def _budget_consistent(snapshot: BudgetSnapshot) -> bool:
        if snapshot.phase == "stopped" or snapshot.stop_dimension is not None:
            return False
        for dimension in type(snapshot.limit).model_fields:
            if (
                getattr(snapshot.consumed, dimension)
                + getattr(snapshot.reserved, dimension)
                > getattr(snapshot.limit, dimension)
            ):
                return False
        return True

    @staticmethod
    def _decision(failures: list[VerifyFailure]):
        if not failures:
            return "end"
        if any(item.rule_id in {"permission.allowed", "source.authorized"} for item in failures):
            return "human_review"
        if any(item.severity == "critical" or not item.repairable for item in failures):
            return "stop"
        return "recovery"

    @staticmethod
    def _failure(
        request: RuntimeVerifyRequest,
        *,
        rule_id: str,
        path: str,
        severity: str,
        repairable: bool,
        expected: Mapping[str, Any],
        actual: Mapping[str, Any],
        refs: tuple[str, ...] = (),
    ) -> VerifyFailure:
        seed = {
            "parent": request.parent_run_id,
            "output": request.output_ref,
            "rule": rule_id,
            "path": path,
            "actual": actual,
        }
        return VerifyFailure(
            failure_id=f"failure:{canonical_json_sha256(seed)[:32]}",
            scope="runtime_output",
            path=path,
            rule_id=rule_id,
            expected=dict(expected),
            actual_summary=dict(actual),
            severity=severity,
            repairable=repairable,
            evidence_refs=refs,
        )
