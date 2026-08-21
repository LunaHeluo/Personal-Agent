from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from typing import Any

from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.trust.models import (
    EvalAssertionResult,
    EvalCaseResult,
    EvalFailureCluster,
    EvalMetric,
    EvalReleaseGate,
)


class FailureClusterer:
    def cluster(
        self,
        *,
        run_id: str,
        case_results: list[EvalCaseResult],
        assertion_results: list[EvalAssertionResult],
    ) -> list[EvalFailureCluster]:
        clusters: list[EvalFailureCluster] = []
        case_groups: dict[str, list[str]] = defaultdict(list)
        for result in case_results:
            if result.status not in {"failed", "blocked", "error"}:
                continue
            code = result.outcome_summary.get("error_code", result.status)
            case_groups[f"case_error:{code}"].append(result.id)
        for key, result_ids in sorted(case_groups.items()):
            clusters.append(
                EvalFailureCluster(
                    id=f"{run_id}:{key}",
                    run_id=run_id,
                    cluster_key=key,
                    title=key.replace("_", " "),
                    case_result_ids=tuple(result_ids),
                    root_cause_summary={"type": "case_error", "key": key},
                )
            )
        assertion_groups: dict[str, list[EvalAssertionResult]] = defaultdict(list)
        for result in assertion_results:
            if result.status not in {"failed", "blocked", "error"}:
                continue
            prefix = result.assertion_id.split(":", 1)[0]
            assertion_groups[f"assertion:{prefix}:{result.status}"].append(result)
        for key, results in sorted(assertion_groups.items()):
            clusters.append(
                EvalFailureCluster(
                    id=f"{run_id}:{key}",
                    run_id=run_id,
                    cluster_key=key,
                    title=key.replace("_", " "),
                    case_result_ids=tuple(
                        sorted({item.case_result_id for item in results})
                    ),
                    root_cause_summary={
                        "type": "assertion",
                        "key": key,
                        "safety_hard_gate": any(
                            item.actual_summary.get("safety_hard_gate") is True
                            for item in results
                        ),
                    },
                    evidence_trace_event_ids=tuple(
                        str(ref)
                        for item in results
                        for ref in item.actual_summary.get("evidence_refs", [])
                    ),
                )
            )
        return clusters


class ReleaseGateDecider:
    def __init__(self, *, minimum_task_success: float = 0.8) -> None:
        self.minimum_task_success = minimum_task_success

    def decide(
        self,
        *,
        run_id: str,
        metrics: list[EvalMetric],
        assertion_results: list[EvalAssertionResult],
        failure_clusters: list[EvalFailureCluster],
    ) -> EvalReleaseGate:
        blocking_reasons: list[str] = []
        safety_blocking = any(
            result.status == "blocked"
            and result.actual_summary.get("safety_hard_gate") is True
            for result in assertion_results
        )
        if safety_blocking:
            blocking_reasons.append("safety hard gate failed")
        metric_by_name = {metric.name: metric for metric in metrics}
        task_success = metric_by_name.get("Task Success")
        if (
            task_success is not None
            and task_success.value is not None
            and task_success.value < self.minimum_task_success
        ):
            blocking_reasons.append("Task Success below threshold")
        if any(cluster.root_cause_summary.get("safety_hard_gate") for cluster in failure_clusters):
            safety_blocking = True
            if "safety hard gate failed" not in blocking_reasons:
                blocking_reasons.append("safety hard gate failed")
        return EvalReleaseGate(
            id=f"{run_id}:release-gate",
            run_id=run_id,
            status="blocked" if blocking_reasons else "passed",
            safety_blocking=safety_blocking,
            blocking_reasons=tuple(blocking_reasons),
            metric_snapshot={
                metric.name: metric.value
                for metric in metrics
                if metric.value is not None
            },
        )


class RunComparator:
    def compare(
        self,
        *,
        baseline_metrics: list[EvalMetric],
        candidate_metrics: list[EvalMetric],
        baseline_cluster_keys: set[str],
        candidate_cluster_keys: set[str],
    ) -> dict[str, Any]:
        baseline = {metric.name: metric.value for metric in baseline_metrics}
        candidate = {metric.name: metric.value for metric in candidate_metrics}
        deltas: dict[str, float | None] = {}
        for name in sorted(set(baseline) | set(candidate)):
            old = baseline.get(name)
            new = candidate.get(name)
            deltas[name] = None if old is None or new is None else round(new - old, 6)
        return {
            "metric_deltas": deltas,
            "new_failure_clusters": sorted(candidate_cluster_keys - baseline_cluster_keys),
            "resolved_failure_clusters": sorted(
                baseline_cluster_keys - candidate_cluster_keys
            ),
        }


class DelegationCandidateGate:
    """Compare frozen single-agent and candidate reports before enabling routing.

    This is deliberately report-only: it cannot run a Worker or alter legacy
    migration settings.  Callers persist its returned decision as release
    evidence and use its route_config as the sole default-route input.
    """

    _QUALITY_METRICS = (
        "Task Success",
        "Source Completeness",
        "Evidence Fidelity",
    )
    _REQUIRED_METRICS = (
        "Task Success",
        "Source Completeness",
        "Evidence Fidelity",
        "Failure Complexity",
        "Latency P95",
        "Total Tokens",
        "Cost per Successful Task",
    )

    def decide(
        self,
        *,
        baseline_report: dict[str, Any],
        candidate_report: dict[str, Any],
    ) -> dict[str, Any]:
        baseline = _report_metrics(baseline_report)
        candidate = _report_metrics(candidate_report)
        reasons: list[str] = []
        same_cases = (
            baseline_report.get("fixture_manifest_hash")
            == candidate_report.get("fixture_manifest_hash")
            and baseline_report.get("case_versions")
            == candidate_report.get("case_versions")
            and baseline_report.get("case_hashes")
            == candidate_report.get("case_hashes")
            and bool(baseline_report.get("case_versions"))
            and bool(baseline_report.get("case_hashes"))
        )
        if not same_cases:
            reasons.append("fixture manifest or case versions differ")

        values: dict[str, tuple[float | None, float | None]] = {}
        for name in self._REQUIRED_METRICS:
            old, new = baseline.get(name), candidate.get(name)
            values[name] = (old, new)
            if old is None or new is None:
                reasons.append(f"required metric missing: {name}")

        quality_improvements: dict[str, float | None] = {}
        quality_improved = False
        for name in self._QUALITY_METRICS:
            old, new = values[name]
            delta = None if old is None or new is None else round(new - old, 6)
            quality_improvements[name] = delta
            if delta is not None and delta < 0:
                reasons.append(f"quality regressed: {name}")
            if delta is not None and delta >= 0.1 - 1e-9:
                quality_improved = True
        if not quality_improved:
            reasons.append("no quality metric improved by at least 10pp")

        baseline_complexity, candidate_complexity = values["Failure Complexity"]
        if (
            baseline_complexity is not None
            and candidate_complexity is not None
            and candidate_complexity > baseline_complexity
        ):
            reasons.append("failure complexity regressed")

        p95_ratio = _ratio(values["Latency P95"])
        cost_ratio = _ratio(values["Cost per Successful Task"])
        token_ratio = _ratio(values["Total Tokens"])
        if p95_ratio is None:
            reasons.append("P95 latency is not comparable")
        elif p95_ratio > 2:
            reasons.append("P95 latency exceeds 2x baseline")
        if cost_ratio is None:
            reasons.append("cost is not comparable")
        elif cost_ratio > 1.5:
            reasons.append("cost exceeds 1.5x baseline")
        if token_ratio is None:
            reasons.append("token usage is not comparable")

        safety_regression = _has_safety_regression(candidate_report)
        if safety_regression:
            reasons.append("safety regressed")
        if _single_agent_better(candidate_report):
            reasons.append("single-agent-better case blocks candidate default")

        status = "passed" if not reasons else "blocked"
        decision = {
            "status": status,
            "baseline_run_id": baseline_report.get("run_id"),
            "candidate_run_id": candidate_report.get("run_id"),
            "same_case_versions": same_cases,
            "quality_improvements": quality_improvements,
            "failure_complexity": {
                "baseline": baseline_complexity,
                "candidate": candidate_complexity,
            },
            "token_ratio": token_ratio,
            "cost_ratio": cost_ratio,
            "p95_ratio": p95_ratio,
            "safety_regression": safety_regression,
            "blocking_reasons": tuple(dict.fromkeys(reasons)),
            "default_route_enabled": status == "passed",
            "route_config": {
                "delegated_job_research_enabled": status == "passed",
                "legacy_job_research_enabled": False,
            },
            "baseline_report_hash": canonical_json_sha256(_report_evidence(baseline_report)),
            "candidate_report_hash": canonical_json_sha256(_report_evidence(candidate_report)),
        }
        decision["decision_hash"] = canonical_json_sha256(decision)
        return decision


class DelegationReleaseDecisionService:
    """Persist and consume a candidate-route decision without touching Legacy."""

    def __init__(self, store) -> None:
        self.store = store
        self.gate = DelegationCandidateGate()

    def compare_and_persist(
        self,
        *,
        baseline_report: dict[str, Any],
        candidate_report: dict[str, Any],
        decision_id: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> dict[str, Any]:
        decision = self.gate.decide(
            baseline_report=baseline_report, candidate_report=candidate_report
        )
        decision = {
            **decision,
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        decision["decision_hash"] = canonical_json_sha256(
            {key: value for key, value in decision.items() if key != "decision_hash"}
        )
        return self.store.save_delegation_release_decision(
            decision_id=decision_id,
            decision=decision,
            created_at=created_at,
            expires_at=expires_at,
        )

    def route_config(
        self,
        *,
        decision_id: str,
        now: datetime,
        baseline_report_hash: str,
        candidate_report_hash: str,
    ) -> dict[str, bool]:
        decision = self.store.get_delegation_release_decision(decision_id)
        disabled = {
            "delegated_job_research_enabled": False,
            "legacy_job_research_enabled": False,
        }
        if decision is None or decision.get("status") != "passed":
            return disabled
        try:
            expires_at = datetime.fromisoformat(str(decision["expires_at"]))
        except (KeyError, ValueError):
            return disabled
        if (
            now >= expires_at
            or decision.get("baseline_report_hash") != baseline_report_hash
            or decision.get("candidate_report_hash") != candidate_report_hash
        ):
            return disabled
        return {
            "delegated_job_research_enabled": True,
            "legacy_job_research_enabled": False,
        }


def _report_metrics(report: dict[str, Any]) -> dict[str, float | None]:
    metrics = report.get("metrics", {})
    if not isinstance(metrics, dict):
        return {}
    parsed: dict[str, float | None] = {}
    for name, value in metrics.items():
        if not isinstance(value, dict) or value.get("missing") is True:
            parsed[str(name)] = None
            continue
        raw = value.get("value")
        parsed[str(name)] = float(raw) if isinstance(raw, (int, float)) else None
    return parsed


def _ratio(values: tuple[float | None, float | None]) -> float | None:
    baseline, candidate = values
    if baseline is None or candidate is None or baseline <= 0:
        return None
    return round(candidate / baseline, 6)


def _has_safety_regression(report: dict[str, Any]) -> bool:
    gate = report.get("gate", {})
    if isinstance(gate, dict) and gate.get("safety_blocking") is True:
        return True
    clusters = report.get("failure_clusters", [])
    return any(
        isinstance(item, dict)
        and isinstance(item.get("root_cause_summary"), dict)
        and item["root_cause_summary"].get("safety_hard_gate") is True
        for item in clusters
    )


def _single_agent_better(report: dict[str, Any]) -> bool:
    for result in report.get("case_results", []):
        if not isinstance(result, dict) or result.get("case_id") != "delegation-single-agent-better":
            continue
        summary = result.get("outcome_summary", {})
        if isinstance(summary, dict) and summary.get("error_code") == "single_agent_preferred":
            return True
    return False


def _report_evidence(report: dict[str, Any]) -> dict[str, Any]:
    """Stable subset: report paths and timestamps cannot change a decision hash."""
    return {
        key: deepcopy(report.get(key))
        for key in (
            "run_id", "fixture_manifest_hash", "case_versions", "case_hashes",
            "case_evidence_hashes", "case_results", "metrics", "failure_clusters", "gate",
        )
    }
