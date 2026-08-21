from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from starter_agent.delegation.store import RecordNotFoundError, RevisionConflictError
from starter_agent.interfaces.capabilities_api import (
    ManagementPrincipal,
    get_management_principal,
)
from starter_agent.interfaces.runs_api import sanitize_run_event


class TaskCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=200)


def create_tasks_router(application_provider: Callable[[], Any]) -> APIRouter:
    router = APIRouter(prefix="/v1/tasks", tags=["tasks"])

    def services(task_id: str, actor: ManagementPrincipal):
        application = application_provider()
        manager = getattr(application, "orchestration_tasks", None)
        if manager is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "orchestration_tasks_unavailable"},
            )
        try:
            task = manager.get(task_id)
        except RecordNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "task_not_found"},
            ) from exc
        parent = manager.store.get_parent(task.parent_run_id)
        if parent is None or not (
            actor.role == "admin"
            or parent.principal in {actor.subject, f"user:{actor.subject}"}
        ):
            raise HTTPException(
                status_code=404,
                detail={"code": "task_not_found"},
            )
        return manager, task, parent

    @router.get("/{task_id}")
    async def details(
        task_id: str,
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        manager, task, parent = services(task_id, actor)
        tree = manager.store.get_run_tree(parent.id)
        return {
            "task": task.model_dump(mode="json"),
            "parent_run_id": parent.id,
            "child_runs": [item.model_dump(mode="json") for item in tree.child_runs],
            "latest_event_seq": tree.events[-1].event_seq if tree.events else 0,
        }

    @router.get("/{task_id}/events")
    async def events(
        task_id: str,
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        manager, _task, parent = services(task_id, actor)
        page = manager.store.list_events(parent.id, after_seq=after_seq, limit=limit)
        return {
            "events": [sanitize_run_event(item) for item in page.items],
            "next_cursor": page.next_cursor,
        }

    @router.post("/{task_id}/cancel")
    async def cancel(
        task_id: str,
        request: TaskCancelRequest,
        actor: ManagementPrincipal = Depends(get_management_principal),
    ) -> dict[str, Any]:
        manager, _task, _parent = services(task_id, actor)
        try:
            task = manager.transition(
                task_id,
                "cancelled",
                expected_version=request.expected_version,
                occurred_at=datetime.now(UTC),
                reason_code=request.reason,
                phase="cancelled",
            )
        except RevisionConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code},
            ) from exc
        return task.model_dump(mode="json")

    return router

