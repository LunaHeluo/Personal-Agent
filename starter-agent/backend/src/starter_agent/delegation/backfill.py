from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from starter_agent.delegation.store import SQLiteRunStore
from starter_agent.domain.models import Message
from starter_agent.infrastructure.session_store import SQLiteSessionStore


class ChatBackfillService:
    """Deliver one deterministic, public Assistant notice for a merged Parent."""

    def __init__(self, *, run_store: SQLiteRunStore, session_store: SQLiteSessionStore) -> None:
        self.run_store = run_store
        self.session_store = session_store

    def publish_once(
        self, parent_run_id: str, *, result_version: int, message_kind: str,
    ) -> UUID | None:
        parent = self.run_store.get_parent(parent_run_id)
        if (
            parent is None
            or parent.status not in {"succeeded", "partial"}
            or parent.result_version != result_version
            or parent.merge_report_id is None
        ):
            return None
        message_id = uuid5(
            NAMESPACE_URL, f"starter-agent:chat-backfill:{parent_run_id}:{result_version}:{message_kind}"
        )
        turn_id = uuid5(NAMESPACE_URL, f"starter-agent:chat-backfill-turn:{parent_run_id}:{result_version}")
        session_id = UUID(parent.session_id)
        content = "调研任务已完成，结果已准备好。"
        self.session_store.add_message(
            session_id, turn_id,
            Message(role="assistant", content=content, metadata={
                "parent_run_id": parent_run_id, "result_version": result_version,
                "message_kind": message_kind, "merge_report_id": parent.merge_report_id,
            }),
            message_id=message_id,
        )
        self.run_store.mark_parent_backfill_completed(
            parent_run_id, result_version=result_version, message_id=str(message_id),
            occurred_at=datetime.now(UTC),
        )
        return message_id

    def consume_pending(self, *, limit: int = 100) -> int:
        """Consume merge requests after commit; retries reuse the message UUID."""
        delivered = 0
        cursor = None
        while True:
            page = self.run_store.list_outbox(limit=limit, cursor=cursor)
            for message in page.items:
                if message.status != "pending" or message.topic != "chat.backfill_requested":
                    continue
                result_version = message.payload.get("result_version")
                message_kind = message.payload.get("message_kind", "delegation.final")
                if not isinstance(result_version, int) or not isinstance(message_kind, str):
                    continue
                message_id = self.publish_once(
                    message.aggregate_id, result_version=result_version,
                    message_kind=message_kind,
                )
                if message_id is None:
                    # A terminal non-merge state must never be acknowledged as
                    # delivered: it has no public conclusion to publish.
                    continue
                self.run_store.mark_outbox_delivered(
                    message.id, delivered_at=datetime.now(UTC)
                )
                delivered += 1
            if page.next_cursor is None:
                return delivered
            cursor = page.next_cursor
