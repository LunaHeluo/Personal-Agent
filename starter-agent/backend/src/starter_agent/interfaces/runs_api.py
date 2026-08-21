from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from starter_agent.delegation.store import (
    RecordNotFoundError, RevisionConflictError, RunStoreError,
)
from starter_agent.interfaces.capabilities_api import ManagementPrincipal, get_management_principal
from starter_agent.orchestration.models import ExecutionState


class RunCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=500)


class RunResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=160)


def _authorized(parent: Any, actor: ManagementPrincipal) -> bool:
    return actor.role == "admin" or parent.principal in {actor.subject, f"user:{actor.subject}"}


def _parent_or_404(store: Any, parent_run_id: str, actor: ManagementPrincipal):
    parent = store.get_parent(parent_run_id)
    if parent is None or not _authorized(parent, actor):
        raise HTTPException(status_code=404, detail={"code": "run_not_found"})
    return parent


_SAFE_EVENT_PAYLOAD_KEYS = frozenset({
    "task_id", "child_task_id", "child_run_id", "specialist_id", "route",
    "registry_hash", "registry_snapshot_hash", "contract_hash", "tool_view_hash",
    "effective_tool_view_hash", "result_version", "merge_report_id", "error_code",
    "reason_code", "cancellation_version", "from", "to", "version", "attempt",
    "envelope_ref", "result_hash", "payload_hash", "trace_ref", "checkpoint_ref",
    "route_decision_id", "plan_id", "step_id", "parent_run_id", "task_event_id",
    "join_decision_id", "verify_id", "recovery_id", "budget_snapshot_id",
    "model_decision_id", "pending_action_id", "duration_ms", "decision",
    "fallback", "stop_reason_code",
})


def sanitize_run_event(event: Any) -> dict[str, Any]:
    """Whitelist references and scalar lifecycle facts; never return raw evidence."""
    payload = {
        key: value for key, value in event.payload.items()
        if key in _SAFE_EVENT_PAYLOAD_KEYS
        and isinstance(value, (str, int, float, bool, type(None)))
    }
    return {
        "id": event.id, "parent_run_id": event.parent_run_id,
        "child_run_id": event.child_run_id, "event_seq": event.event_seq,
        "event_type": event.event_type, "status": event.status,
        "occurred_at": event.occurred_at.isoformat(), "payload": payload,
    }


def _safe_parent(parent: Any) -> dict[str, Any]:
    return parent.model_dump(mode="json", exclude={"orchestration_state"})


def _safe_child_task(task: Any) -> dict[str, Any]:
    """Expose execution contract metadata, never the Child input/context payload."""
    return {
        "id": task.id,
        "parent_run_id": task.parent_run_id,
        "specialist_id": task.specialist_id,
        "specialist_snapshot_id": task.specialist_snapshot_id,
        "output_schema_version": task.output_schema_version,
        "requested_allowed_tools": list(task.requested_allowed_tools),
        "requested_deadline": task.requested_deadline.isoformat(),
        "requested_budget": task.requested_budget.model_dump(mode="json"),
        "failure_behavior": task.failure_behavior,
        "status": task.status,
        "version": task.version,
        "accepted_child_run_id": task.accepted_child_run_id,
        "accepted_result_envelope_ref": task.accepted_result_envelope_ref,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def orchestration_run_detail(tree: Any) -> dict[str, Any] | None:
    raw = tree.parent.orchestration_state
    if raw is None:
        return None
    state = ExecutionState.model_validate(raw)
    task_by_id = {item.id: item for item in tree.child_tasks}
    child_runs = []
    for child in tree.child_runs:
        task = task_by_id.get(child.child_task_id)
        child_runs.append(
            {
                "child_run_id": child.id,
                "child_task_id": child.child_task_id,
                "step_id": None if task is None else task.constraints_json.get("step_id"),
                "specialist_id": None if task is None else task.specialist_id,
                "status": child.status,
                "phase": child.phase,
                "attempt": child.attempt,
                "deadline_at": child.deadline_at.isoformat(),
                "result_envelope_ref": child.result_envelope_ref,
                "error_code": child.error_code,
            }
        )
    recovery_events = [
        sanitize_run_event(item)
        for item in tree.events
        if item.event_type == "orchestration.recovery"
    ]
    plan = None
    if state.plan is not None:
        plan = {
            "plan_id": state.plan.plan_id,
            "status": state.plan.status,
            "goal": state.plan.goal,
            "validation_result_id": state.plan.validation_result_id,
            "join_policy": state.plan.join_policy,
            "steps": [
                {
                    "step_id": item.step_id,
                    "ordinal": item.ordinal,
                    "goal": item.goal,
                    "status": item.status,
                    "depends_on": list(item.depends_on),
                    "execution": item.execution,
                    "specialist_id": item.specialist_id,
                    "risk": item.risk,
                    "parallel_candidate": item.parallel_candidate,
                    "parallel_decision_reason": item.parallel_decision_reason,
                }
                for item in state.plan.steps
            ],
        }
    return {
        "execution_status": state.execution_status,
        "current_node": state.current_node,
        "current_step": state.current_step,
        "route": None if state.route is None else state.route.model_dump(mode="json"),
        "plan": plan,
        "background_task": (
            None
            if state.background_task is None
            else state.background_task.model_dump(mode="json")
        ),
        "child_runs": child_runs,
        "join_decision": (
            None
            if state.latest_join_decision is None
            else state.latest_join_decision.model_dump(mode="json")
        ),
        "verify_result": (
            None
            if state.latest_verify_result is None
            else state.latest_verify_result.model_dump(mode="json")
        ),
        "recovery_attempts": recovery_events,
        "revision_count": state.revision_count.model_dump(mode="json"),
        "budget": None if state.budget is None else state.budget.model_dump(mode="json"),
        "model_decisions": [item.model_dump(mode="json") for item in state.model_decisions],
        "pending_action": (
            None
            if state.pending_action is None
            else state.pending_action.model_dump(
                mode="json", exclude={"arguments_hash"}
            )
        ),
        "stop_reason": state.stop_reason,
        "latest_event_seq": tree.events[-1].event_seq if tree.events else 0,
    }
def create_runs_router(application_provider: Callable[[], Any]) -> APIRouter:
    router = APIRouter(prefix="/v1/runs", tags=["runs"])

    @router.get("/{parent_run_id}")
    async def details(parent_run_id: str, actor: ManagementPrincipal = Depends(get_management_principal)) -> dict[str, Any]:
        store = application_provider().delegation_store
        _parent_or_404(store, parent_run_id, actor)
        tree = store.get_run_tree(parent_run_id)
        return {
            "parent": _safe_parent(tree.parent),
            "child_tasks": [_safe_child_task(item) for item in tree.child_tasks],
            "child_runs": [item.model_dump(mode="json") for item in tree.child_runs],
            "merge_reports": [item.model_dump(mode="json") for item in tree.merge_reports],
            "orchestration": orchestration_run_detail(tree),
        }

    @router.get("/{parent_run_id}/orchestration")
    async def orchestration_details(
        parent_run_id: str,
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        store = application_provider().delegation_store
        _parent_or_404(store, parent_run_id, actor)
        tree = store.get_run_tree(parent_run_id)
        detail = orchestration_run_detail(tree)
        if detail is None:
            raise HTTPException(
                status_code=404, detail={"code": "orchestration_detail_not_found"}
            )
        return detail

    @router.get("/{parent_run_id}/events")
    async def events(
        parent_run_id: str, after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        store = application_provider().delegation_store
        _parent_or_404(store, parent_run_id, actor)
        page = store.list_events(parent_run_id, after_seq=after_seq, limit=limit)
        return {"events": [sanitize_run_event(item) for item in page.items], "next_cursor": page.next_cursor}

    @router.get("/{parent_run_id}/events/stream")
    async def events_stream(
        parent_run_id: str, after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> StreamingResponse:
        """Durable catch-up only; closing this response never cancels a Parent."""
        store = application_provider().delegation_store
        _parent_or_404(store, parent_run_id, actor)
        page = store.list_events(parent_run_id, after_seq=after_seq, limit=limit)

        async def stream():
            for item in page.items:
                yield "data: " + json.dumps(
                    {"type": "run_event", "event": sanitize_run_event(item)},
                    ensure_ascii=False,
                ) + "\n\n"
            yield "data: " + json.dumps(
                {"type": "cursor", "next_cursor": page.next_cursor}, ensure_ascii=False,
            ) + "\n\n"

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/{parent_run_id}/artifacts")
    async def artifacts(parent_run_id: str, actor: ManagementPrincipal = Depends(get_management_principal)) -> dict[str, Any]:
        store = application_provider().delegation_store
        _parent_or_404(store, parent_run_id, actor)
        tree = store.get_run_tree(parent_run_id)
        return {"artifacts": [
            {"id": item.id, "child_run_id": item.child_run_id, "kind": item.kind,
             "restricted": item.restricted, "created_at": item.created_at}
            for item in tree.artifact_links
        ]}

    @router.post("/{parent_run_id}/cancel")
    async def cancel(parent_run_id: str, request: RunCancelRequest, actor: ManagementPrincipal = Depends(get_management_principal)) -> dict[str, Any]:
        store = application_provider().delegation_store
        _parent_or_404(store, parent_run_id, actor)
        try:
            parent = store.request_parent_cancellation(
                parent_run_id, reason=request.reason, requested_at=datetime.now(UTC),
                expected_version=request.expected_version, idempotency_key=request.idempotency_key,
            )
        except RevisionConflictError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code}) from exc
        return parent.model_dump(mode="json")

    @router.post("/{parent_run_id}/resume")
    async def resume(parent_run_id: str, request: RunResumeRequest, actor: ManagementPrincipal = Depends(get_management_principal)) -> dict[str, Any]:
        store = application_provider().delegation_store
        _parent_or_404(store, parent_run_id, actor)
        try:
            parent = store.resume_parent_for_validation(
                parent_run_id, expected_version=request.expected_version,
                idempotency_key=request.idempotency_key, occurred_at=datetime.now(UTC)
            )
        except (RevisionConflictError, RunStoreError) as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code}) from exc
        return parent.model_dump(mode="json")

    return router
