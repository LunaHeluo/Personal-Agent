from __future__ import annotations

from collections import defaultdict
from typing import Any

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
