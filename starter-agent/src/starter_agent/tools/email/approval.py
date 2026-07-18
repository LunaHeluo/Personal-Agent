from __future__ import annotations

from starter_agent.tools.email.errors import EmailError, EmailErrorCode
from starter_agent.tools.email.manager import EmailManager
from starter_agent.tools.email.models import (
    ApprovalChallengeView,
    SendApproval,
)


class EmailApprovalService:
    """Trusted application-facing approval boundary.

    This service is intentionally not exposed as a model tool. An authenticated
    UI/API layer may call it after showing the complete draft to the user.
    """

    def __init__(self, manager: EmailManager) -> None:
        self.manager = manager

    def create_challenge(
        self,
        draft_id: str,
        *,
        session_id: str,
        profile: str | None = None,
        user_ref: str | None = None,
    ) -> ApprovalChallengeView:
        return self.manager.create_approval_challenge(
            draft_id,
            session_id=session_id,
            profile=profile,
            user_ref=user_ref,
        )

    def confirm(
        self,
        approval_id: str,
        *,
        session_id: str,
        confirmed: bool,
    ) -> SendApproval:
        if confirmed is not True:
            raise EmailError(
                EmailErrorCode.APPROVAL_REQUIRED,
                "需要用户明确确认当前草稿后才能发送",
            )
        return self.manager.confirm_approval(
            approval_id,
            session_id=session_id,
        )

    def get(
        self, approval_id: str, *, session_id: str
    ) -> SendApproval:
        return self.manager.store.get_approval(
            approval_id, session_id=session_id
        )

    def revoke(
        self, approval_id: str, *, session_id: str
    ) -> SendApproval:
        return self.manager.revoke_approval(
            approval_id, session_id=session_id
        )
