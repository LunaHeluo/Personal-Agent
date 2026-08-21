"""Privacy-preserving interview reviews bound to application records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from starter_agent.cv_workbench.contracts import (
    Application,
    InterviewReview,
    InterviewRound,
    InterviewSummaryCandidate,
)
from starter_agent.cv_workbench.store import (
    IdempotencyConflictError,
    ObjectNotFoundError,
    RevisionConflictError,
    SQLiteWorkbenchStore,
)


class InterviewReviewError(RuntimeError):
    code = "interview_review_error"


class InterviewConfirmationRequiredError(InterviewReviewError):
    code = "interview_confirmation_required"


def _facts_hash(rounds: tuple[InterviewRound, ...]) -> str:
    payload = [item.model_dump(mode="json") for item in rounds]
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class RoundCommand:
    expected_revision: int
    round_id: str
    round_type: str
    occurred_at: datetime
    questions: tuple[str, ...] = ()
    answers: tuple[str, ...] = ()
    feedback: tuple[str, ...] = ()
    result: str | None = None
    improvement_items: tuple[str, ...] = ()
    user_confirmed: bool = False


class InterviewReviewService:
    def __init__(self, *, store: SQLiteWorkbenchStore, clock=lambda: datetime.now(UTC)) -> None:
        self.store = store
        self.clock = clock

    def create(self, review_id: str, application_id: str, *, principal: str) -> InterviewReview:
        application = self.store.get(Application, application_id, principal=principal)
        existing = self.for_application(application_id, principal=principal, required=False)
        if existing is not None:
            if existing.review_id != review_id:
                raise IdempotencyConflictError(application_id)
            return existing
        now = self.clock()
        return self.store.create(
            InterviewReview(
                review_id=review_id,
                workspace_id=application.workspace_id,
                application_id=application_id,
                revision=1,
                created_at=now,
                updated_at=now,
            ),
            principal=principal,
            workspace_id=application.workspace_id,
        )

    def for_application(
        self, application_id: str, *, principal: str, required: bool = True
    ) -> InterviewReview | None:
        cursor = None
        while True:
            page = self.store.list(InterviewReview, principal=principal, cursor=cursor)
            match = next((item for item in page.items if item.application_id == application_id), None)
            if match is not None:
                return match
            if page.next_cursor is None:
                if required:
                    raise ObjectNotFoundError(application_id)
                return None
            cursor = page.next_cursor

    def add_round(self, review_id: str, command: RoundCommand, *, principal: str) -> InterviewReview:
        if not command.user_confirmed:
            raise InterviewConfirmationRequiredError("interview_confirmation_required")
        current = self.store.get(InterviewReview, review_id, principal=principal)
        existing = next((item for item in current.rounds if item.round_id == command.round_id), None)
        candidate = InterviewRound(
            round_id=command.round_id,
            round_type=command.round_type,
            occurred_at=command.occurred_at,
            questions=command.questions,
            answers=command.answers,
            feedback=command.feedback,
            result=command.result,
            improvement_items=command.improvement_items,
            created_by=principal,
        )
        if existing is not None:
            if existing != candidate:
                raise IdempotencyConflictError(command.round_id)
            return current
        if current.revision != command.expected_revision:
            raise RevisionConflictError(review_id, current.revision)
        updated = InterviewReview.model_validate(current.model_dump() | {
            "rounds": current.rounds + (candidate,),
            "revision": current.revision + 1,
            "updated_at": self.clock(),
        })
        return self.store.update(updated, principal=principal, expected_revision=current.revision)

    def propose_summary(
        self, review_id: str, summary_id: str, *, expected_revision: int, principal: str
    ) -> InterviewReview:
        """Create an extractive candidate solely from user-entered round facts."""
        current = self.store.get(InterviewReview, review_id, principal=principal)
        existing = next((item for item in current.summary_candidates if item.summary_id == summary_id), None)
        if existing is not None:
            return current
        if current.revision != expected_revision:
            raise RevisionConflictError(review_id, current.revision)
        if not current.rounds:
            raise InterviewReviewError("interview_round_required")
        lines: list[str] = []
        for item in current.rounds:
            lines.append(f"{item.round_type}（{item.occurred_at.date().isoformat()}）")
            lines.extend(f"问题：{value}" for value in item.questions)
            lines.extend(f"反馈：{value}" for value in item.feedback)
            if item.result:
                lines.append(f"结果：{item.result}")
            lines.extend(f"改进：{value}" for value in item.improvement_items)
        now = self.clock()
        summary = InterviewSummaryCandidate(
            summary_id=summary_id,
            text="\n".join(lines),
            cited_round_ids=tuple(item.round_id for item in current.rounds),
            source_facts_sha256=_facts_hash(current.rounds),
            created_at=now,
        )
        updated = InterviewReview.model_validate(current.model_dump() | {
            "summary_candidates": current.summary_candidates + (summary,),
            "revision": current.revision + 1,
            "updated_at": now,
        })
        return self.store.update(updated, principal=principal, expected_revision=current.revision)

    def decide_summary(
        self,
        review_id: str,
        summary_id: str,
        *,
        expected_revision: int,
        decision: str,
        principal: str,
    ) -> InterviewReview:
        if decision not in {"accepted", "rejected"}:
            raise InterviewReviewError("invalid_summary_decision")
        current = self.store.get(InterviewReview, review_id, principal=principal)
        if current.revision != expected_revision:
            raise RevisionConflictError(review_id, current.revision)
        found = False
        values = []
        for item in current.summary_candidates:
            if item.summary_id != summary_id:
                values.append(item); continue
            found = True
            if item.status != "pending":
                if item.status == decision:
                    return current
                raise IdempotencyConflictError(summary_id)
            values.append(InterviewSummaryCandidate.model_validate(item.model_dump() | {
                "status": decision, "decided_at": self.clock()
            }))
        if not found:
            raise ObjectNotFoundError(summary_id)
        updated = InterviewReview.model_validate(current.model_dump() | {
            "summary_candidates": tuple(values),
            "revision": current.revision + 1,
            "updated_at": self.clock(),
        })
        return self.store.update(updated, principal=principal, expected_revision=current.revision)

    def delete(self, review_id: str, *, principal: str) -> None:
        self.store.delete_owned_unreferenced(InterviewReview, review_id, principal=principal)

