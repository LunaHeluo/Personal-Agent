from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from starter_agent.trust.models import EvalRun, EvalSuite
from starter_agent.trust.store import TrustStore
from starter_agent.interfaces.trust_api import create_trust_router


def test_trust_api_exposes_real_store_state_and_gate_errors() -> None:
    store = TrustStore("sqlite:///:memory:", ".")
    suite = EvalSuite(
        id="job-research-regression",
        name="Job Research Regression",
        version="v1",
        created_at=datetime.now(UTC),
    )
    run = EvalRun(
        id="run-1",
        suite_id=suite.id,
        run_type="fixture",
        status="completed",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        code_version="abc123",
        code_dirty=True,
        prompt_version="prompt-v1",
        skill_version="job-research@1.1.0",
        tool_schema_version="schema-v1",
        policy_version="policy-v1",
        fixture_manifest_hash=None,
    )
    store.create_suite(suite)
    store.create_run(run)
    app = FastAPI()
    app.include_router(create_trust_router(store_provider=lambda: store))

    with TestClient(app) as client:
        suites = client.get("/v1/trust/suites")
        runs = client.get("/v1/trust/runs")
        traces = client.get("/v1/trust/traces", params={"eval_run_id": run.id})
        gate = client.get(f"/v1/trust/runs/{run.id}/gate")

    assert suites.status_code == 200
    assert suites.json()["suites"][0]["id"] == suite.id
    assert runs.status_code == 200
    assert runs.json()["runs"][0]["id"] == run.id
    assert traces.status_code == 200
    assert traces.json() == {"traces": []}
    assert gate.status_code == 404
    assert gate.json()["detail"]["code"] == "release_gate_not_found"


def test_trust_api_run_creation_uses_backend_store_not_static_success() -> None:
    store = TrustStore("sqlite:///:memory:", ".")
    store.create_suite(
        EvalSuite(
            id="job-research-regression",
            name="Job Research Regression",
            version="v1",
            created_at=datetime.now(UTC),
        )
    )
    app = FastAPI()
    app.include_router(create_trust_router(store_provider=lambda: store))

    with TestClient(app) as client:
        response = client.post(
            "/v1/trust/runs",
            json={
                "id": "run-created",
                "suite_id": "job-research-regression",
                "run_type": "fixture",
                "code_version": "abc123",
                "code_dirty": True,
                "prompt_version": "prompt-v1",
                "skill_version": "job-research@1.1.0",
                "tool_schema_version": "schema-v1",
                "policy_version": "policy-v1",
            },
        )
        listed = client.get("/v1/trust/runs")

    assert response.status_code == 201
    assert response.json()["run"]["status"] == "queued"
    assert listed.json()["runs"][0]["id"] == "run-created"
