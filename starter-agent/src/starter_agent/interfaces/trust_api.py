from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from starter_agent.bootstrap import get_settings
from starter_agent.trust.models import EvalRun
from starter_agent.trust.store import RecordAlreadyExistsError, TrustStore


_trust_store: TrustStore | None = None


def get_trust_store() -> TrustStore:
    global _trust_store
    if _trust_store is None:
        settings = get_settings()
        _trust_store = TrustStore(settings.app.database_url, settings.project_root)
    return _trust_store


class TrustRunCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    suite_id: str = Field(min_length=1, max_length=160)
    run_type: Literal["fixture", "smoke"]
    code_version: str = Field(min_length=1, max_length=160)
    code_dirty: bool
    prompt_version: str = Field(min_length=1, max_length=160)
    skill_version: str = Field(min_length=1, max_length=160)
    tool_schema_version: str = Field(min_length=1, max_length=160)
    policy_version: str = Field(min_length=1, max_length=160)
    fixture_manifest_hash: str | None = Field(default=None, min_length=64, max_length=64)


def create_trust_router(
    *,
    store_provider: Callable[[], TrustStore] = get_trust_store,
) -> APIRouter:
    router = APIRouter(prefix="/v1/trust", tags=["trust"])

    @router.get("/suites")
    async def suites(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        store = store_provider()
        return {
            "suites": [
                suite.model_dump(mode="json") for suite in store.list_suites(limit=limit)
            ]
        }

    @router.get("/cases")
    async def cases(
        suite_id: str | None = None,
        limit: int = Query(default=500, ge=1, le=1000),
    ) -> dict[str, Any]:
        store = store_provider()
        return {
            "cases": [
                case.model_dump(mode="json")
                for case in store.list_cases(suite_id=suite_id, limit=limit)
            ]
        }

    @router.get("/runs")
    async def runs(
        run_type: Literal["fixture", "smoke"] | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        store = store_provider()
        return {
            "runs": [
                run.model_dump(mode="json")
                for run in store.list_runs(run_type=run_type, limit=limit)
            ]
        }

    @router.post("/runs", status_code=status.HTTP_201_CREATED)
    async def create_run(request: TrustRunCreateRequest) -> dict[str, Any]:
        store = store_provider()
        if store.get_suite(request.suite_id) is None:
            raise _http_error(404, "suite_not_found", "Trust eval suite not found.")
        run = EvalRun(
            id=request.id,
            suite_id=request.suite_id,
            run_type=request.run_type,
            status="queued",
            started_at=datetime.now(UTC),
            code_version=request.code_version,
            code_dirty=request.code_dirty,
            prompt_version=request.prompt_version,
            skill_version=request.skill_version,
            tool_schema_version=request.tool_schema_version,
            policy_version=request.policy_version,
            fixture_manifest_hash=request.fixture_manifest_hash,
        )
        try:
            created = store.create_run(run)
        except RecordAlreadyExistsError as exc:
            raise _http_error(409, "run_already_exists", str(exc)) from exc
        return {"run": created.model_dump(mode="json")}

    @router.get("/runs/{run_id}/case-results")
    async def case_results(run_id: str) -> dict[str, Any]:
        store = store_provider()
        return {
            "case_results": [
                result.model_dump(mode="json")
                for result in store.list_case_results(run_id=run_id)
            ]
        }

    @router.get("/runs/{run_id}/metrics")
    async def metrics(run_id: str) -> dict[str, Any]:
        store = store_provider()
        return {
            "metrics": [
                metric.model_dump(mode="json")
                for metric in store.list_metrics(run_id=run_id)
            ]
        }

    @router.get("/runs/{run_id}/failure-clusters")
    async def failure_clusters(run_id: str) -> dict[str, Any]:
        store = store_provider()
        return {
            "failure_clusters": [
                cluster.model_dump(mode="json")
                for cluster in store.list_failure_clusters(run_id=run_id)
            ]
        }

    @router.get("/runs/{run_id}/gate")
    async def release_gate(run_id: str) -> dict[str, Any]:
        store = store_provider()
        gate = store.get_release_gate(run_id)
        if gate is None:
            raise _http_error(
                404,
                "release_gate_not_found",
                "Trust release gate has not been calculated for this run.",
            )
        return {"gate": gate.model_dump(mode="json")}

    @router.get("/traces")
    async def traces(
        eval_run_id: str | None = None,
        case_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        tool_call_id: str | None = None,
        limit: int = Query(default=500, ge=1, le=1000),
    ) -> dict[str, Any]:
        store = store_provider()
        return {
            "traces": [
                event.model_dump(mode="json")
                for event in store.list_trace_events(
                    eval_run_id=eval_run_id,
                    case_id=case_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    tool_call_id=tool_call_id,
                    limit=limit,
                )
            ]
        }

    @router.get("/safety")
    async def safety() -> dict[str, Any]:
        store = store_provider()
        runs = store.list_runs(limit=25)
        gates = [store.get_release_gate(run.id) for run in runs]
        blocking = [
            gate
            for gate in gates
            if gate is not None and gate.status == "blocked"
        ]
        return {
            "policy_version": runs[0].policy_version if runs else None,
            "gate_status": "blocked" if blocking else "unknown",
            "blocking_reasons": [
                reason for gate in blocking for reason in gate.blocking_reasons
            ],
            "evidence": [gate.model_dump(mode="json") for gate in blocking],
        }

    return router


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )
