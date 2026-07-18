from __future__ import annotations

from typing import Protocol

from starter_agent.tools.email.models import (
    EmailCapabilities,
    EmailMessage,
    EmailSearchPage,
    EmailSearchQuery,
    SendReceipt,
    StoredDraft,
)


class EmailAdapter(Protocol):
    async def capabilities(self) -> EmailCapabilities: ...

    async def search(self, query: EmailSearchQuery) -> EmailSearchPage: ...

    async def read(
        self,
        message_ref: str,
        *,
        include_thread: bool,
        thread_limit: int,
        max_body_chars: int,
    ) -> EmailMessage: ...

    async def create_draft(self, draft: StoredDraft) -> StoredDraft: ...

    async def send_draft(
        self,
        draft: StoredDraft,
        *,
        idempotency_key: str,
    ) -> SendReceipt: ...

    async def find_send_result(
        self,
        draft_id: str,
        *,
        idempotency_key: str,
    ) -> SendReceipt | None: ...
