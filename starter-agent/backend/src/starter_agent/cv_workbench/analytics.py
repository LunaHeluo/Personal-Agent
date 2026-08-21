"""Explainable event-sourced funnel and local visible reminder projections."""

from __future__ import annotations

from datetime import UTC, datetime

from starter_agent.cv_workbench.contracts import Application, ApplicationStatus, Workspace
from starter_agent.cv_workbench.store import SQLiteWorkbenchStore


FUNNEL_DEFINITION_VERSION = "application-funnel.v1"
FUNNEL_ORDER = (
    ApplicationStatus.TO_DECIDE,
    ApplicationStatus.TO_APPLY,
    ApplicationStatus.APPLIED,
    ApplicationStatus.ASSESSMENT,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.OFFER,
)


class ApplicationAnalyticsService:
    def __init__(self, *, store: SQLiteWorkbenchStore, clock=lambda: datetime.now(UTC)) -> None:
        self.store = store
        self.clock = clock

    def _applications(self, workspace_id: str, principal: str) -> tuple[Application, ...]:
        self.store.get(Workspace, workspace_id, principal=principal)
        values, cursor = [], None
        while True:
            page = self.store.list(Application, principal=principal, workspace_id=workspace_id, cursor=cursor)
            values.extend(item for item in page.items if item.workspace_id == workspace_id)
            if page.next_cursor is None:
                return tuple(values)
            cursor = page.next_cursor

    def funnel(self, workspace_id: str, *, principal: str) -> dict[str, object]:
        applications = self._applications(workspace_id, principal)
        stages = []
        for status in FUNNEL_ORDER:
            reached = sum(any(event.to_status == status for event in item.events) for item in applications)
            current = sum(item.current_status == status for item in applications)
            stages.append({"status": status.value, "reached": reached, "current": current})
        return {
            "definition_version": FUNNEL_DEFINITION_VERSION,
            "workspace_id": workspace_id,
            "application_count": len(applications),
            "event_count": sum(len(item.events) for item in applications),
            "stages": tuple(stages),
            "explanation": "reached 按 ApplicationEvent 历史去重计数；current 按当前投递状态计数。",
            "generated_at": self.clock(),
        }

    def reminders(self, workspace_id: str, *, principal: str, before: datetime | None = None) -> dict[str, object]:
        cutoff = before or self.clock()
        applications = self._applications(workspace_id, principal)
        items = tuple(
            {
                "application_id": item.application_id,
                "remind_at": item.remind_at,
                "next_action": item.next_action,
                "status": "due" if item.remind_at <= cutoff else "upcoming",
                "application_status": item.current_status.value,
            }
            for item in applications
            if item.remind_at is not None and item.current_status != ApplicationStatus.ARCHIVED
        )
        return {
            "definition_version": "local-reminder.v1",
            "workspace_id": workspace_id,
            "cutoff": cutoff,
            "items": tuple(sorted(items, key=lambda value: value["remind_at"])),
            "external_messages_sent": 0,
        }
