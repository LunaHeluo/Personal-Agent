from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from starter_agent.tools.email.errors import EmailError, EmailErrorCode
from starter_agent.tools.email.models import (
    EmailAttachmentMeta,
    EmailCapabilities,
    EmailHeaders,
    EmailMessage,
    EmailMessageSummary,
    EmailReplyContext,
    EmailSearchPage,
    EmailSearchQuery,
    SendReceipt,
    StoredDraft,
)
from starter_agent.tools.email.store import EmailStore, idempotency_hash


def mask_address(value: str) -> str:
    local, separator, domain = value.partition("@")
    if not separator:
        return "***"
    visible = local[:1] if local else ""
    return f"{visible}***@{domain}"


class MockFixtureEmailAdapter:
    """A deterministic, network-free mailbox adapter for contract testing."""

    def __init__(
        self,
        *,
        profile: str,
        fixture_root: Path,
        store: EmailStore,
    ) -> None:
        self.profile = profile
        self.fixture_root = fixture_root.resolve()
        self.store = store
        self._payload = self._load()
        self.send_calls = 0

    def _load(self) -> dict[str, Any]:
        path = (self.fixture_root / "mailbox.json").resolve()
        try:
            path.relative_to(self.fixture_root)
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise EmailError(
                EmailErrorCode.FIXTURE_INVALID,
                "Mock 邮箱 fixture 无法读取",
            ) from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("messages"), list
        ):
            raise EmailError(
                EmailErrorCode.FIXTURE_INVALID,
                "Mock 邮箱 fixture 格式不正确",
            )
        return payload

    async def capabilities(self) -> EmailCapabilities:
        return EmailCapabilities(
            search=True,
            read_peek=True,
            local_draft=True,
            mailbox_draft=False,
            simulated_send=True,
            real_send=False,
        )

    async def search(self, query: EmailSearchQuery) -> EmailSearchPage:
        self._raise_triggered_error(query)
        messages = [
            item
            for item in self._payload["messages"]
            if self._matches(item, query)
        ]
        messages.sort(key=lambda item: self._date(item), reverse=True)
        try:
            offset = int(query.cursor or "0")
        except ValueError as exc:
            raise EmailError(
                EmailErrorCode.CURSOR_INVALID,
                "Mock 邮箱分页游标无效",
            ) from exc
        if offset < 0 or offset > len(messages):
            raise EmailError(
                EmailErrorCode.CURSOR_INVALID,
                "Mock 邮箱分页游标无效",
            )
        selected = messages[offset : offset + query.limit]
        next_offset = offset + len(selected)
        has_more = next_offset < len(messages)
        summaries = [self._summary(item) for item in selected]
        return EmailSearchPage(
            profile=self.profile,
            messages=summaries,
            next_cursor=str(next_offset) if has_more else None,
            is_truncated=False,
            has_more=has_more,
            source_ref=f"fixture-search:{uuid4()}",
        )

    async def read(
        self,
        message_ref: str,
        *,
        include_thread: bool,
        thread_limit: int,
        max_body_chars: int,
    ) -> EmailMessage:
        identifier = message_ref.removeprefix("fixture-message:")
        item = next(
            (
                candidate
                for candidate in self._payload["messages"]
                if candidate.get("id") == identifier
            ),
            None,
        )
        if item is None:
            raise EmailError(
                EmailErrorCode.MESSAGE_NOT_FOUND,
                "Mock 邮箱中没有找到指定邮件",
            )
        body = self._body(item)
        truncated = len(body) > max_body_chars
        thread_items: list[dict[str, Any]] = []
        if include_thread:
            thread_items = [
                candidate
                for candidate in self._payload["messages"]
                if candidate.get("thread_id") == item.get("thread_id")
                and candidate.get("id") != item.get("id")
            ]
            thread_items.sort(key=self._date)
        thread_has_more = len(thread_items) > thread_limit
        thread_items = thread_items[-thread_limit:]
        attachments = [
            EmailAttachmentMeta(
                attachment_ref=(
                    f"fixture-attachment:{identifier}:{index}"
                ),
                filename_masked=self._mask_filename(str(attachment["filename"])),
                content_type=str(attachment.get("content_type", "application/octet-stream")),
                size_bytes=int(attachment.get("size_bytes", 0)),
                sha256=attachment.get("sha256"),
            )
            for index, attachment in enumerate(item.get("attachments", []))
        ]
        references = item.get("references", [])
        return EmailMessage(
            message_ref=f"fixture-message:{identifier}",
            thread_ref=f"fixture-thread:{item.get('thread_id')}",
            headers=EmailHeaders(
                from_masked=mask_address(str(item.get("from", ""))),
                to_masked=[
                    mask_address(str(value)) for value in item.get("to", [])
                ],
                cc_masked=[
                    mask_address(str(value)) for value in item.get("cc", [])
                ],
                subject=str(item.get("subject", "")),
                date=self._date(item),
            ),
            body_text=body[:max_body_chars],
            attachments=attachments,
            reply_context=EmailReplyContext(
                in_reply_to=item.get("in_reply_to"),
                references_count=len(references),
                thread_position=len(references) + 1,
            ),
            thread_messages=[self._summary(value) for value in thread_items],
            is_truncated=truncated,
            has_more=truncated or thread_has_more,
            source_ref=f"fixture-source:{identifier}",
        )

    async def create_draft(self, draft: StoredDraft) -> StoredDraft:
        if draft.storage_scope != "mock":
            raise EmailError(
                EmailErrorCode.CAPABILITY_NOT_SUPPORTED,
                "Mock adapter 只支持 mock 草稿",
            )
        created = draft.model_copy(
            update={
                "provider_draft_id": f"mock-provider-draft:{uuid4()}",
                "status": "draft_only",
                "updated_at": datetime.now(UTC),
            }
        )
        return self.store.save_draft(created)

    async def send_draft(
        self,
        draft: StoredDraft,
        *,
        idempotency_key: str,
    ) -> SendReceipt:
        existing = self.store.find_receipt(draft.draft_id, idempotency_key)
        if existing is not None:
            return existing
        self.send_calls += 1
        receipt = SendReceipt(
            delivery_mode="mock",
            status="simulated_sent",
            external_delivery=False,
            message_ref=f"mock-message:{uuid4()}",
            thread_ref=draft.in_reply_to,
            sent_at=datetime.now(UTC),
            content_sha256=draft.content_sha256,
            recipient_count=len(draft.to) + len(draft.cc) + len(draft.bcc),
            idempotency_key_hash=idempotency_hash(idempotency_key),
            source_ref=f"mock-send-receipt:{uuid4()}",
        )
        return self.store.save_receipt(
            draft.draft_id, idempotency_key, receipt
        )

    async def find_send_result(
        self,
        draft_id: str,
        *,
        idempotency_key: str,
    ) -> SendReceipt | None:
        return self.store.find_receipt(draft_id, idempotency_key)

    def _raise_triggered_error(self, query: EmailSearchQuery) -> None:
        keywords = {value.lower() for value in query.keywords}
        for item in self._payload.get("errors", []):
            trigger = str(item.get("trigger_keyword", "")).lower()
            if trigger not in keywords:
                continue
            try:
                code = EmailErrorCode(str(item["error_code"]))
            except (KeyError, ValueError) as exc:
                raise EmailError(
                    EmailErrorCode.FIXTURE_INVALID,
                    "Mock 错误 fixture 格式不正确",
                ) from exc
            raise EmailError(
                code,
                str(item.get("display", "Mock 邮箱调用失败")),
                retryable=bool(item.get("retryable", False)),
            )

    @staticmethod
    def _body(item: dict[str, Any]) -> str:
        text = str(item.get("body_text", ""))
        repeat = item.get("body_repeat", 1)
        if isinstance(repeat, int) and 1 <= repeat <= 10_000:
            return text * repeat
        return text

    def _matches(self, item: dict[str, Any], query: EmailSearchQuery) -> bool:
        sender = str(item.get("from", "")).lower()
        recipients = " ".join(
            str(value).lower()
            for value in [*item.get("to", []), *item.get("cc", [])]
        )
        subject = str(item.get("subject", "")).lower()
        searchable = " ".join(
            (sender, recipients, subject, self._body(item).lower())
        )
        if query.sender and query.sender.lower() not in sender:
            return False
        if query.recipient and query.recipient.lower() not in recipients:
            return False
        if query.subject and query.subject.lower() not in subject:
            return False
        if any(keyword.lower() not in searchable for keyword in query.keywords):
            return False
        sent_at = self._date(item)
        if query.date_from and sent_at < query.date_from:
            return False
        if query.date_to and sent_at > query.date_to:
            return False
        if query.unread_only and "unread" not in item.get("flags", []):
            return False
        return True

    def _summary(self, item: dict[str, Any]) -> EmailMessageSummary:
        body = self._body(item)
        return EmailMessageSummary(
            message_ref=f"fixture-message:{item.get('id')}",
            thread_ref=f"fixture-thread:{item.get('thread_id')}",
            from_masked=mask_address(str(item.get("from", ""))),
            to_me=True,
            subject=str(item.get("subject", "")),
            sent_at=self._date(item),
            snippet=body[:240],
            flags=[str(value) for value in item.get("flags", [])],
            source_ref=f"fixture-source:{item.get('id')}",
        )

    @staticmethod
    def _date(item: dict[str, Any]) -> datetime:
        try:
            parsed = datetime.fromisoformat(
                str(item["sent_at"]).replace("Z", "+00:00")
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise EmailError(
                EmailErrorCode.FIXTURE_INVALID,
                "Mock 邮箱时间字段无效",
            ) from exc
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    @staticmethod
    def _mask_filename(value: str) -> str:
        suffix = Path(value).suffix
        return f"*{suffix}" if suffix else "***"
