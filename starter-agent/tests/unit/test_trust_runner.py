import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from starter_agent.trust.fixtures import JobResearchFixtureLoader
from starter_agent.trust.models import EvalCase, EvalSuite
from starter_agent.trust.runner import (
    EvalCaseExecution,
    EvalRunner,
    EvalRunnerConfig,
)
from starter_agent.trust.store import TrustStore


PROJECT_ROOT = Path(__file__).parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "evals" / "job-research" / "fixtures"


def _suite() -> EvalSuite:
    return EvalSuite(
        id="job-research-regression",
        name="Job Research Regression",
        version="v1",
        created_at=datetime.now(UTC),
        case_ids=("case-a", "case-b"),
    )


def _case(case_id: str) -> EvalCase:
    return EvalCase(
        id=case_id,
        suite_id="job-research-regression",
        version="v1",
        layer="Happy Path",
        input_summary={"request": f"run {case_id}"},
        fixture_ids=("jd-public-ai-agent-redacted-v1",),
        expected_outcome={"status": "success"},
    )


@pytest.mark.asyncio
async def test_eval_runner_isolates_parallel_cases_and_records_versions() -> None:
    store = TrustStore("sqlite:///:memory:", PROJECT_ROOT)
    runner = EvalRunner(
        store=store,
        fixture_loader=JobResearchFixtureLoader(FIXTURE_ROOT),
        config=EvalRunnerConfig(
            run_id="run-parallel",
            seed=123,
            max_concurrency=2,
            case_timeout_seconds=1,
            work_root=PROJECT_ROOT / ".session-only-trust-runner",
            code_version="abc123",
            code_dirty=True,
            prompt_version="prompt-v1",
            skill_version="job-research@1.1.0",
            tool_schema_version="schema-v1",
            policy_version="policy-v1",
            provider="fixture-provider",
            model="fixture-model",
        ),
    )
    seen_contexts: list[EvalCaseExecution] = []

    async def handler(context: EvalCaseExecution) -> dict[str, object]:
        seen_contexts.append(context)
        await asyncio.sleep(0.01)
        return {"status": "success", "case_id": context.case.id}

    run = await runner.run_suite(_suite(), [_case("case-a"), _case("case-b")], handler)

    assert run.status == "completed"
    assert run.id == "run-parallel"
    assert run.fixture_manifest_hash
    assert run.config_summary == {
        "seed": 123,
        "provider": "fixture-provider",
        "model": "fixture-model",
        "judge": "disabled",
        "max_concurrency": 2,
    }
    assert len(seen_contexts) == 2
    assert {context.session_id for context in seen_contexts} == {
        "run-parallel:case-a:session",
        "run-parallel:case-b:session",
    }
    assert seen_contexts[0].database_url != seen_contexts[1].database_url
    assert seen_contexts[0].work_dir != seen_contexts[1].work_dir
    assert all(context.fixture_manifest.by_id("jd-public-ai-agent-redacted-v1") for context in seen_contexts)
    assert [result.status for result in store.list_case_results(run_id=run.id)] == [
        "passed",
        "passed",
    ]


@pytest.mark.asyncio
async def test_eval_runner_records_case_timeout_and_run_failure() -> None:
    store = TrustStore("sqlite:///:memory:", PROJECT_ROOT)
    runner = EvalRunner(
        store=store,
        fixture_loader=JobResearchFixtureLoader(FIXTURE_ROOT),
        config=EvalRunnerConfig(
            run_id="run-timeout",
            case_timeout_seconds=0.01,
            work_root=PROJECT_ROOT / ".session-only-trust-runner",
        ),
    )

    async def handler(_context: EvalCaseExecution) -> dict[str, object]:
        await asyncio.sleep(1)
        return {"status": "success"}

    run = await runner.run_suite(_suite(), [_case("case-a")], handler)
    results = store.list_case_results(run_id=run.id)

    assert run.status == "failed"
    assert results[0].status == "error"
    assert results[0].outcome_summary["error_code"] == "case_timeout"


@pytest.mark.asyncio
async def test_eval_runner_cancellation_stops_scheduling_new_cases() -> None:
    store = TrustStore("sqlite:///:memory:", PROJECT_ROOT)
    runner = EvalRunner(
        store=store,
        fixture_loader=JobResearchFixtureLoader(FIXTURE_ROOT),
        config=EvalRunnerConfig(
            run_id="run-cancel",
            max_concurrency=1,
            work_root=PROJECT_ROOT / ".session-only-trust-runner",
        ),
    )
    started: list[str] = []

    async def handler(context: EvalCaseExecution) -> dict[str, object]:
        started.append(context.case.id)
        runner.cancel()
        return {"status": "success"}

    run = await runner.run_suite(_suite(), [_case("case-a"), _case("case-b")], handler)
    results = store.list_case_results(run_id=run.id)

    assert run.status == "cancelled"
    assert started == ["case-a"]
    assert [(result.case_id, result.status) for result in results] == [
        ("case-a", "passed"),
        ("case-b", "skipped"),
    ]
