from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml

from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.trust.fixtures import JobResearchFixtureLoader
from starter_agent.trust.fixture_runtime import execute_fixture_case
from starter_agent.trust.metrics import ProgrammaticMetricCalculator
from starter_agent.trust.models import (
    EvalAssertionResult,
    EvalCase,
    EvalCaseResult,
    EvalRun,
    EvalSuite,
    TrustTraceEvent,
)
from starter_agent.trust.release_gate import (
    FailureClusterer,
    ReleaseGateDecider,
    RunComparator,
)
from starter_agent.trust.rules import RuleAssertion, RuleEvaluator
from starter_agent.trust.store import RecordAlreadyExistsError, TrustStore


CASE_FILE_NAMES = (
    "evals/job-research-cases.yaml",
    "evals/job-research-safety-cases.yaml",
    "evals/job-application-orchestration-cases.yaml",
    "evals/job-application-delegation-cases.yaml",
)


def run_job_research_fixture_baseline(
    *,
    store: TrustStore,
    project_root: Path,
    run_id: str,
    report_dir: Path,
    known_failure_case_id: str | None = None,
    evaluation_variant: str = "multi_agent",
) -> dict[str, Any]:
    """Run deterministic job-research fixture evals and persist Trust evidence."""

    if evaluation_variant not in {"single_agent", "multi_agent"}:
        raise ValueError("evaluation_variant_invalid")
    project_root = project_root.resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    loaded = _load_case_files(project_root)
    cases = loaded["cases"]
    suite = EvalSuite(
        id="job-research-trust-all",
        name="Job Research Trust All Fixture Suite",
        version="v1",
        created_at=datetime.now(UTC),
        case_ids=tuple(case.id for case in cases),
        metadata_summary={
            "case_files": list(CASE_FILE_NAMES),
            "fixed_fixture_eval": True,
        },
    )
    manifest = JobResearchFixtureLoader(
        project_root / "evals" / "job-research" / "fixtures"
    ).load_manifest()
    for file_suite in loaded["suites"]:
        _create_once(lambda file_suite=file_suite: store.create_suite(file_suite))
    _create_once(lambda: store.create_suite(suite))
    for case in cases:
        _create_once(lambda case=case: store.create_case(case))

    previous_blocked = [
        run.id
        for run in store.list_runs(run_type="fixture", limit=500)
        if run.status == "blocked"
    ]

    started_at = datetime.now(UTC)
    run = EvalRun(
        id=run_id,
        suite_id=suite.id,
        run_type="fixture",
        status="running",
        started_at=started_at,
        code_version="workspace",
        code_dirty=True,
        prompt_version="job-research-fixture-v1",
        skill_version="job-research@fixture-v1",
        tool_schema_version="fixture-schema-v1",
        policy_version="fixture-policy-v1",
        fixture_manifest_hash=manifest.manifest_hash,
        config_summary={
            "seed": 0,
            "provider": "fixture",
            "model": "deterministic",
            "judge": "disabled",
            "case_count": len(cases),
            "evaluation_variant": evaluation_variant,
        },
    )
    _create_once(lambda: store.create_run(run))

    case_results: list[EvalCaseResult] = []
    assertion_results: list[EvalAssertionResult] = []
    case_versions = {case.id: case.version for case in cases}
    case_hashes: dict[str, str] = {}
    case_evidence_hashes: dict[str, str] = {}
    for index, case in enumerate(cases, start=1):
        blocked = case.id == known_failure_case_id
        outcome, raw_events = asyncio.run(
            execute_fixture_case(
                case,
                manifest=manifest,
                project_root=project_root,
            )
        )
        traces = [
            _routing_trace_event(
                run_id=run_id,
                case=case,
                case_index=index,
                event_index=event_index,
                value=value,
            )
            for event_index, value in enumerate(raw_events, start=1)
        ]
        outcome = _project_evaluation_variant(
            outcome,
            evaluation_variant=evaluation_variant,
            default_duration_ms=100 + index,
            default_cost_usd=round(0.001 + index * 0.0001, 6),
            default_total_tokens=200 + index,
        )
        provisional = EvalCaseResult(
            id=f"{run_id}:{case.id}",
            run_id=run_id,
            case_id=case.id,
            status="passed",
            outcome_summary=outcome,
            session_id=f"{run_id}:{case.id}:session",
            turn_id=f"{run_id}:{case.id}:turn-1",
            trace_event_ids=tuple(trace.id for trace in traces),
        )
        rules = [_routing_rule(case, item) for item in case.deterministic_assertions]
        assertions = RuleEvaluator().evaluate(
            run_id=run_id,
            case_result=provisional,
            assertions=rules,
            trace_events=traces,
        )
        if blocked:
            assertions[0] = assertions[0].model_copy(
                update={
                    "status": "blocked",
                    "actual_summary": {
                        **assertions[0].actual_summary,
                        "safety_hard_gate": case.safety_level == "hard_gate",
                    },
                }
            )
        failed = any(item.status in {"failed", "blocked", "error"} for item in assertions)
        status = (
            "blocked"
            if any(item.status == "blocked" for item in assertions)
            else ("failed" if failed else "passed")
        )
        result = provisional.model_copy(
            update={
                "status": status,
                "outcome_summary": {
                    **provisional.outcome_summary,
                    **({"task_success": False, "error_code": "known_safety_regression"} if blocked else {}),
                },
            }
        )
        # Comparability binds the immutable test definition and fixture inputs,
        # never the candidate's actual result.  Actuals remain separate release
        # evidence so an improvement does not make the two runs incomparable.
        case_hashes[case.id] = canonical_json_sha256({
            "case": case.model_dump(mode="json"),
            "fixture_manifest_hash": manifest.manifest_hash,
        })
        case_evidence_hashes[case.id] = canonical_json_sha256({
            "outcome_hash": outcome.get("canonical_hash", canonical_json_sha256(outcome)),
            "trace_hash": outcome.get(
                "trace_canonical_hash",
                canonical_json_sha256([trace.payload_hash for trace in traces]),
            ),
        })
        for trace in traces:
            store.append_trace_event(trace)
        store.create_case_result(result)
        case_results.append(result)
        for assertion in assertions:
            store.create_assertion_result(assertion)
            assertion_results.append(assertion)

    metrics = ProgrammaticMetricCalculator().calculate(
        run_id=run_id,
        case_results=case_results,
        assertion_results=assertion_results,
    )
    for metric in metrics:
        store.create_metric(metric)
    clusters = FailureClusterer().cluster(
        run_id=run_id,
        case_results=case_results,
        assertion_results=assertion_results,
    )
    for cluster in clusters:
        store.create_failure_cluster(cluster)
    gate = ReleaseGateDecider().decide(
        run_id=run_id,
        metrics=metrics,
        assertion_results=assertion_results,
        failure_clusters=clusters,
    )
    store.create_release_gate(gate)
    store.update_run_status(
        run_id,
        status=gate.status,
        completed_at=datetime.now(UTC),
    )

    comparison_to = previous_blocked[-1] if previous_blocked and not known_failure_case_id else None
    report = {
        "run_id": run_id,
        "suite_id": suite.id,
        "run_type": "fixture",
        "evaluation_variant": evaluation_variant,
        "fixture_manifest_hash": manifest.manifest_hash,
        "case_versions": case_versions,
        "case_hashes": case_hashes,
        "case_evidence_hashes": case_evidence_hashes,
        "case_count": len(cases),
        "assertion_count": len(assertion_results),
        "trace_count": len(store.list_trace_events(eval_run_id=run_id)),
        "case_results": [
            {
                **result.model_dump(mode="json"),
                "assertions": [
                    assertion.model_dump(mode="json")
                    for assertion in assertion_results
                    if assertion.case_result_id == result.id
                ],
            }
            for result in case_results
        ],
        "metrics": {
            metric.name: metric.model_dump(mode="json")
            for metric in metrics
        },
        "failure_clusters": [
            cluster.model_dump(mode="json") for cluster in clusters
        ],
        "gate": gate.model_dump(mode="json"),
        "comparison_to": comparison_to,
        "comparison": (
            _compare_runs(store, baseline_run_id=comparison_to, candidate_run_id=run_id)
            if comparison_to
            else None
        ),
        "comparable_signature": _comparable_signature(
            cases=cases,
            metrics=metrics,
            clusters=clusters,
            gate_status=gate.status,
        ),
    }
    report_path = report_dir / f"{run_id}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    report["report_path"] = str(report_path)
    return report


def _project_evaluation_variant(
    outcome: dict[str, Any],
    *,
    evaluation_variant: str,
    default_duration_ms: int,
    default_cost_usd: float,
    default_total_tokens: int,
) -> dict[str, Any]:
    """Project paired fixture observations without changing case contracts."""

    prefix = evaluation_variant
    quality = outcome.get(f"{prefix}_quality_score")
    duration = outcome.get(f"{prefix}_latency_ms", default_duration_ms)
    tokens = outcome.get(f"{prefix}_token_count", default_total_tokens)
    cost_units = outcome.get(f"{prefix}_cost_units")
    projected = {
        **outcome,
        "evaluation_variant": evaluation_variant,
        "duration_ms": duration,
        "cost_usd": (
            default_cost_usd
            if not isinstance(cost_units, (int, float)) or isinstance(cost_units, bool)
            else round(float(cost_units) / 1000, 6)
        ),
        "token_usage": {"total_tokens": tokens},
        "failure_complexity": float(
            1
            + int(outcome.get("recovery_count", 0) or 0)
            + len(
                [
                    status
                    for status in outcome.get("child_statuses", [])
                    if status not in {"succeeded", "completed"}
                ]
            )
            + int(outcome.get("conflict_count", 0) or 0)
        ),
    }
    if isinstance(quality, (int, float)) and not isinstance(quality, bool):
        normalized = float(quality) / 100
        projected["source_completeness"] = normalized
        projected["evidence_fidelity"] = normalized
        projected["paired_comparison_sample"] = True
    return projected


def _load_case_files(project_root: Path) -> dict[str, Any]:
    suites: list[EvalSuite] = []
    cases: list[EvalCase] = []
    for relative in CASE_FILE_NAMES:
        payload = yaml.safe_load((project_root / relative).read_text(encoding="utf-8"))
        suite_payload = payload["suite"]
        file_cases = [EvalCase(**item) for item in payload["cases"]]
        suites.append(
            EvalSuite(
                id=suite_payload["id"],
                name=suite_payload["name"],
                version=suite_payload["version"],
                created_at=datetime.now(UTC),
                case_ids=tuple(case.id for case in file_cases),
                metadata_summary={
                    "case_file": relative,
                    "fixture_manifest": suite_payload["fixture_manifest"],
                },
            )
        )
        cases.extend(file_cases)
    return {"suites": suites, "cases": cases}


def _routing_trace_event(
    *,
    run_id: str,
    case: EvalCase,
    case_index: int,
    event_index: int,
    value: Any,
) -> TrustTraceEvent:
    if not isinstance(value, dict):
        raise ValueError(f"invalid routing event: {case.id}:{event_index}")
    event_type = value.get("event_type")
    status = value.get("status")
    summary = value.get("summary")
    if (
        not isinstance(event_type, str)
        or not isinstance(status, str)
        or not isinstance(summary, dict)
    ):
        raise ValueError(f"invalid routing event: {case.id}:{event_index}")
    event_id = f"{run_id}:{case.id}:trace-{event_index}"
    payload = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    return TrustTraceEvent(
        id=event_id,
        eval_run_id=run_id,
        case_id=case.id,
        session_id=f"{run_id}:{case.id}:session",
        turn_id=f"{run_id}:{case.id}:turn-1",
        model_request_id=(
            f"{run_id}:{case.id}:model-{event_index}"
            if event_type == "Model"
            else None
        ),
        tool_call_id=(
            f"{run_id}:{case.id}:tool-{event_index}"
            if event_type == "Tool"
            else None
        ),
        policy_decision_id=(
            f"{run_id}:{case.id}:policy-{event_index}"
            if event_type == "Policy"
            else None
        ),
        event_type=event_type,
        status=status,
        occurred_at=datetime.fromtimestamp(
            1_700_000_000 + case_index * 100 + event_index,
            tz=UTC,
        ),
        summary=summary,
        payload_hash=sha256(payload.encode("utf-8")).hexdigest(),
        source_ref="fixture://knowledge-routing",
    )


def _routing_rule(case: EvalCase, assertion_id: str) -> RuleAssertion:
    parts = assertion_id.split(":")
    if parts[:2] == ["rule", "route"] and len(parts) == 3:
        kind = "route"
        expected = {"route": parts[2]}
    elif parts[:2] == ["rule", "event_count"] and len(parts) == 4:
        kind = "event_count"
        expected = {"event_type": parts[2], "count": int(parts[3])}
    elif assertion_id == "rule:callable_tools_empty":
        kind = "callable_tools_empty"
        expected = {}
    elif parts[:2] == ["rule", "tool_schema_absent"] and len(parts) == 3:
        kind = "tool_schema_absent"
        expected = {"tool_name": parts[2]}
    elif parts[:2] == ["rule", "outcome_equals"] and len(parts) == 4:
        kind = "outcome_equals"
        expected_value: Any = parts[3]
        if expected_value in {"true", "false"}:
            expected_value = expected_value == "true"
        elif expected_value.isdigit():
            expected_value = int(expected_value)
        expected = {"key": parts[2], "value": expected_value}
    elif parts[:2] == ["rule", "trace_order"] and len(parts) == 4:
        kind = "trace_order"
        expected = {"before": parts[2], "after": parts[3]}
    elif parts[:2] == ["rule", "policy_decision"] and len(parts) == 4:
        kind = "policy_decision"
        expected = {"tool_name": parts[2], "decision": parts[3]}
    elif parts[:2] == ["rule", "tool_call"] and len(parts) == 3:
        kind = "tool_call"
        expected_call = next(
            (
                item
                for item in case.expected_tool_calls
                if item.get("tool_name") == parts[2]
            ),
            None,
        )
        expected = {
            "tool_name": parts[2],
            "arguments": (
                None
                if expected_call is None
                else expected_call.get("arguments")
            ),
        }
    elif assertion_id == "rule:source_url:expected":
        kind = "source_url"
        expected = {"source_url": case.expected_outcome.get("source_url")}
    elif parts[:2] == ["rule", "citation"] and len(parts) == 3:
        kind = "citation"
        expected = {"chunk_id": parts[2]}
    elif parts[:2] == ["rule", "no_external_action"] and len(parts) == 3:
        kind = "no_external_action"
        expected = {"forbidden_tools": [parts[2]]}
    elif (
        parts[:2] == ["rule", "tool_disabled_schema_hidden"]
        and len(parts) == 3
    ):
        kind = "tool_disabled_schema_hidden"
        expected = {"tool_name": parts[2]}
    else:
        raise ValueError(f"unsupported routing assertion: {assertion_id}")
    return RuleAssertion(
        id=assertion_id,
        kind=kind,
        expected=expected,
        safety_hard_gate=case.safety_level == "hard_gate",
        evidence_refs=(),
    )


def _compare_runs(
    store: TrustStore,
    *,
    baseline_run_id: str | None,
    candidate_run_id: str,
) -> dict[str, Any] | None:
    if baseline_run_id is None:
        return None
    baseline_metrics = store.list_metrics(run_id=baseline_run_id)
    candidate_metrics = store.list_metrics(run_id=candidate_run_id)
    baseline_clusters = {
        cluster.cluster_key
        for cluster in store.list_failure_clusters(run_id=baseline_run_id)
    }
    candidate_clusters = {
        cluster.cluster_key
        for cluster in store.list_failure_clusters(run_id=candidate_run_id)
    }
    return RunComparator().compare(
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
        baseline_cluster_keys=baseline_clusters,
        candidate_cluster_keys=candidate_clusters,
    )


def _comparable_signature(
    *,
    cases: list[EvalCase],
    metrics: list[Any],
    clusters: list[Any],
    gate_status: str,
) -> str:
    payload = {
        "case_ids": [case.id for case in cases],
        "metrics": {
            metric.name: metric.value for metric in metrics
        },
        "failure_cluster_keys": [cluster.cluster_key for cluster in clusters],
        "gate_status": gate_status,
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _create_once(operation: Any) -> None:
    try:
        operation()
    except RecordAlreadyExistsError:
        return
