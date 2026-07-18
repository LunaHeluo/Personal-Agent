from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from starter_agent.settings import EmailProfileConfig, EmailToolConfig
from starter_agent.tools.email.adapters.base import EmailAdapter
from starter_agent.tools.email.adapters.mock_fixture import (
    MockFixtureEmailAdapter,
)
from starter_agent.tools.email.errors import EmailError, EmailErrorCode
from starter_agent.tools.email.models import (
    ApprovalChallengeView,
    DraftCreateRequest,
    EmailCapabilities,
    EmailMessage,
    EmailMessageSummary,
    EmailSearchPage,
    EmailSearchQuery,
    SendApproval,
    SendReceipt,
    StoredAttachment,
    StoredDraft,
)
from starter_agent.tools.email.store import (
    EmailStore,
    draft_content_sha256,
    idempotency_hash,
    stable_sha256,
)


EMAIL_PATTERN = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
PLACEHOLDER_PATTERN = re.compile(
    r"\[[^\]]*(?:待补充|todo|placeholder)[^\]]*\]|\{\{[^}]+\}\}",
    re.IGNORECASE,
)


class EmailManager:
    def __init__(
        self,
        *,
        config: EmailToolConfig,
        project_root: Path,
        store: EmailStore,
        adapters: dict[str, EmailAdapter] | None = None,
        environment_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self.config = config
        self.project_root = project_root.resolve()
        self.store = store
        self._adapters: dict[str, EmailAdapter] = dict(adapters or {})
        self.environment_resolver = environment_resolver or (lambda name: None)

    def resolve_profile(
        self, profile: str | None
    ) -> tuple[str, EmailProfileConfig]:
        selected = (profile or self.config.active_profile).strip()
        configured = self.config.profiles.get(selected)
        if configured is None:
            raise EmailError(
                EmailErrorCode.PROFILE_NOT_FOUND,
                "指定邮箱 profile 不存在",
                metadata={"profile": selected},
            )
        if not configured.enabled:
            raise EmailError(
                EmailErrorCode.PROFILE_DISABLED,
                "指定邮箱 profile 尚未启用",
                metadata={"profile": selected},
            )
        return selected, configured

    def adapter(self, profile: str | None = None) -> tuple[str, EmailAdapter]:
        selected, configured = self.resolve_profile(profile)
        existing = self._adapters.get(selected)
        if existing is not None:
            return selected, existing
        if configured.adapter == "mock_fixture":
            assert configured.fixture_root is not None
            adapter: EmailAdapter = MockFixtureEmailAdapter(
                profile=selected,
                fixture_root=self._resolve_fixture_root(configured.fixture_root),
                store=self.store,
            )
        else:
            from starter_agent.tools.email.adapters.imap_smtp import (
                EnvironmentCredentialResolver,
                ImapSmtpEmailAdapter,
            )

            adapter = ImapSmtpEmailAdapter(
                profile=selected,
                config=configured,
                store=self.store,
                credential_resolver=EnvironmentCredentialResolver(
                    self.environment_resolver
                ),
            )
        self._adapters[selected] = adapter
        return selected, adapter

    async def capabilities(
        self, profile: str | None = None
    ) -> EmailCapabilities:
        _, adapter = self.adapter(profile)
        return await adapter.capabilities()

    async def search(
        self,
        query: EmailSearchQuery,
        *,
        session_id: str,
        profile: str | None = None,
    ) -> EmailSearchPage:
        selected, adapter = self.adapter(profile)
        self._validate_search_query(query)
        query_hash = self._search_query_hash(query)
        raw_cursor: str | None = None
        if query.cursor:
            encoded = self.store.resolve_reference(
                query.cursor,
                session_id=session_id,
                profile=selected,
                object_type="cursor",
            )
            try:
                cursor_payload = json.loads(encoded)
            except json.JSONDecodeError as exc:
                raise EmailError(
                    EmailErrorCode.CURSOR_INVALID,
                    "邮箱分页游标无效",
                ) from exc
            if cursor_payload.get("query_hash") != query_hash:
                raise EmailError(
                    EmailErrorCode.CURSOR_INVALID,
                    "邮箱分页游标与当前查询不匹配",
                )
            raw_cursor = cursor_payload.get("cursor")
        adapter_query = query.model_copy(
            update={
                "cursor": raw_cursor,
                "limit": min(query.limit, self.config.result_max_items),
            }
        )
        try:
            page = await adapter.search(adapter_query)
        except EmailError:
            raise
        except TimeoutError as exc:
            raise EmailError(
                EmailErrorCode.PROVIDER_TIMEOUT,
                "邮箱搜索超时",
                retryable=True,
                metadata={"profile": selected, "operation": "search"},
            ) from exc
        except Exception as exc:
            raise EmailError(
                EmailErrorCode.INTERNAL_ERROR,
                "邮箱搜索发生内部错误",
                metadata={"profile": selected, "operation": "search"},
            ) from exc

        messages = [
            self._externalize_summary(item, session_id, selected)
            for item in page.messages[: self.config.result_max_items]
        ]
        manager_truncated = len(page.messages) > self.config.result_max_items
        next_cursor = None
        if page.next_cursor is not None:
            next_cursor = self.store.create_reference(
                session_id=session_id,
                profile=selected,
                object_type="cursor",
                object_id=json.dumps(
                    {
                        "cursor": page.next_cursor,
                        "query_hash": query_hash,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        source_ref = self.store.create_reference(
            session_id=session_id,
            profile=selected,
            object_type="source",
            object_id=page.source_ref,
        )
        return EmailSearchPage(
            profile=selected,
            messages=messages,
            next_cursor=next_cursor,
            is_truncated=page.is_truncated or manager_truncated,
            has_more=page.has_more or manager_truncated,
            source_ref=source_ref,
        )

    async def read(
        self,
        message_ref: str,
        *,
        session_id: str,
        profile: str | None = None,
        include_thread: bool = True,
        thread_limit: int = 10,
        max_body_chars: int | None = None,
    ) -> EmailMessage:
        selected, adapter = self.adapter(profile)
        if thread_limit < 1 or thread_limit > 20:
            raise EmailError(
                EmailErrorCode.INVALID_ARGUMENTS,
                "thread_limit 必须在 1 到 20 之间",
            )
        requested_max = max_body_chars or self.config.body_max_chars
        if requested_max < 1_000 or requested_max > 50_000:
            raise EmailError(
                EmailErrorCode.INVALID_ARGUMENTS,
                "max_body_chars 必须在 1000 到 50000 之间",
            )
        effective_max = min(requested_max, self.config.body_max_chars)
        raw_message_ref = self.store.resolve_reference(
            message_ref,
            session_id=session_id,
            profile=selected,
            object_type="message",
        )
        try:
            message = await adapter.read(
                raw_message_ref,
                include_thread=include_thread,
                thread_limit=thread_limit,
                max_body_chars=effective_max,
            )
        except EmailError:
            raise
        except TimeoutError as exc:
            raise EmailError(
                EmailErrorCode.PROVIDER_TIMEOUT,
                "读取邮件超时",
                retryable=True,
                metadata={"profile": selected, "operation": "read"},
            ) from exc
        except Exception as exc:
            raise EmailError(
                EmailErrorCode.INTERNAL_ERROR,
                "读取邮件发生内部错误",
                metadata={"profile": selected, "operation": "read"},
            ) from exc

        body = message.body_text[:effective_max]
        manager_truncated = len(message.body_text) > effective_max
        thread_items = [
            self._externalize_summary(item, session_id, selected)
            for item in message.thread_messages[:thread_limit]
        ]
        thread_truncated = len(message.thread_messages) > thread_limit
        external_message_ref = self.store.create_reference(
            session_id=session_id,
            profile=selected,
            object_type="message",
            object_id=message.message_ref,
        )
        external_thread_ref = (
            self.store.create_reference(
                session_id=session_id,
                profile=selected,
                object_type="thread",
                object_id=message.thread_ref,
            )
            if message.thread_ref
            else None
        )
        source_ref = self.store.create_reference(
            session_id=session_id,
            profile=selected,
            object_type="source",
            object_id=message.source_ref,
        )
        return message.model_copy(
            update={
                "message_ref": external_message_ref,
                "thread_ref": external_thread_ref,
                "body_text": body,
                "thread_messages": thread_items,
                "is_truncated": message.is_truncated or manager_truncated,
                "has_more": (
                    message.has_more or manager_truncated or thread_truncated
                ),
                "source_ref": source_ref,
            }
        )

    def resolve_source(
        self,
        source_ref: str,
        *,
        session_id: str,
        profile: str | None = None,
    ) -> str:
        selected, _ = self.resolve_profile(profile)
        return self.store.resolve_reference(
            source_ref,
            session_id=session_id,
            profile=selected,
            object_type="source",
        )

    async def create_draft(
        self,
        request: DraftCreateRequest,
        *,
        session_id: str,
    ) -> StoredDraft:
        selected, adapter = self.adapter(request.profile)
        self._validate_recipients(request.to, request.cc, request.bcc)
        if PLACEHOLDER_PATTERN.search(request.subject) or PLACEHOLDER_PATTERN.search(
            request.body_text
        ):
            raise EmailError(
                EmailErrorCode.PLACEHOLDER_PRESENT,
                "邮件主题或正文仍包含待补充占位符",
            )
        if "\r" in request.subject or "\n" in request.subject:
            raise EmailError(
                EmailErrorCode.INVALID_ARGUMENTS,
                "邮件主题不能包含换行符",
            )
        if request.in_reply_to:
            self.store.resolve_reference(
                request.in_reply_to,
                session_id=session_id,
                profile=selected,
                object_type="message",
            )
        for source_ref in request.evidence_source_refs:
            self.store.resolve_reference(
                source_ref,
                session_id=session_id,
                profile=selected,
                object_type="source",
            )
        attachments = [
            self._resolve_attachment(value) for value in request.attachment_refs
        ]
        request_payload = request.model_dump(mode="json")
        request_payload.pop("idempotency_key", None)
        request_hash = stable_sha256(request_payload)
        existing = self.store.get_idempotency(
            "create_draft",
            request.idempotency_key,
            request_hash,
        )
        if existing is not None:
            return self.store.get_draft(
                str(existing["draft_id"]),
                session_id=session_id,
                profile=selected,
            )

        content_hash = draft_content_sha256(
            to=request.to,
            cc=request.cc,
            bcc=request.bcc,
            subject=request.subject,
            body_text=request.body_text,
            in_reply_to=request.in_reply_to,
            attachment_sha256s=[item.sha256 for item in attachments],
        )
        now = datetime.now(UTC)
        draft = StoredDraft(
            draft_id=f"email-draft:{uuid4()}",
            session_id=session_id,
            profile=selected,
            storage_scope=request.storage_scope,
            to=request.to,
            cc=request.cc,
            bcc=request.bcc,
            subject=request.subject,
            body_text=request.body_text,
            in_reply_to=request.in_reply_to,
            attachments=attachments,
            content_sha256=content_hash,
            status="draft_only",
            created_at=now,
            updated_at=now,
            idempotency_key_hash=idempotency_hash(request.idempotency_key),
        )
        capabilities = await adapter.capabilities()
        if request.storage_scope == "local":
            if not capabilities.local_draft:
                raise EmailError(
                    EmailErrorCode.CAPABILITY_NOT_SUPPORTED,
                    "当前邮箱 profile 不支持本地草稿",
                )
            created = self.store.save_draft(draft)
        elif request.storage_scope == "mock":
            if not capabilities.simulated_send:
                raise EmailError(
                    EmailErrorCode.CAPABILITY_NOT_SUPPORTED,
                    "当前邮箱 profile 不支持 mock 草稿",
                )
            created = await adapter.create_draft(draft)
        else:
            if not capabilities.mailbox_draft:
                raise EmailError(
                    EmailErrorCode.CAPABILITY_NOT_SUPPORTED,
                    "当前邮箱 profile 不支持邮箱草稿",
                )
            created = await adapter.create_draft(draft)
        self.store.save_idempotency(
            "create_draft",
            request.idempotency_key,
            request_hash,
            {"draft_id": created.draft_id},
        )
        return created

    def create_approval_challenge(
        self,
        draft_id: str,
        *,
        session_id: str,
        profile: str | None = None,
        user_ref: str | None = None,
    ) -> ApprovalChallengeView:
        selected, _ = self.resolve_profile(profile)
        draft = self.store.get_draft(
            draft_id, session_id=session_id, profile=selected
        )
        if draft.status not in {
            "draft_only",
            "waiting_for_approval",
            "send_failed",
        }:
            raise EmailError(
                EmailErrorCode.APPROVAL_INVALID,
                "当前草稿状态不能创建发送审批",
            )
        expires_at = datetime.now(UTC) + timedelta(
            seconds=self.config.approval_ttl_seconds
        )
        approval = SendApproval(
            approval_id=f"email-approval:{uuid4()}",
            session_id=session_id,
            user_ref=user_ref,
            profile=selected,
            draft_id=draft.draft_id,
            content_sha256=draft.content_sha256,
            recipient_sha256=self._recipient_hash(draft),
            attachment_sha256s=[
                item.sha256 for item in draft.attachments
            ],
            status="pending",
            expires_at=expires_at,
        )
        self.store.save_approval(approval)
        self.store.save_draft(
            draft.model_copy(
                update={
                    "status": "waiting_for_approval",
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        return ApprovalChallengeView(
            approval_id=approval.approval_id,
            session_id=session_id,
            profile=selected,
            draft_id=draft.draft_id,
            status=approval.status,
            to=draft.to,
            cc=draft.cc,
            bcc=draft.bcc,
            subject=draft.subject,
            body_text=draft.body_text,
            attachment_sha256s=approval.attachment_sha256s,
            content_sha256=draft.content_sha256,
            expires_at=expires_at,
        )

    def confirm_approval(
        self,
        approval_id: str,
        *,
        session_id: str,
    ) -> SendApproval:
        approval = self.store.get_approval(
            approval_id, session_id=session_id
        )
        if approval.status == "expired":
            raise EmailError(
                EmailErrorCode.APPROVAL_EXPIRED,
                "发送审批已经过期，请重新预览草稿",
            )
        if approval.status != "pending":
            raise EmailError(
                EmailErrorCode.APPROVAL_INVALID,
                "发送审批当前不可确认",
            )
        draft = self.store.get_draft(
            approval.draft_id,
            session_id=session_id,
            profile=approval.profile,
        )
        self._assert_approval_matches(approval, draft)
        approved = self.store.update_approval_status(  # type: ignore[attr-defined]
            approval_id,
            session_id=session_id,
            status="approved",
        )
        self.store.save_draft(
            draft.model_copy(
                update={
                    "status": "approved",
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        return approved

    def revoke_approval(
        self,
        approval_id: str,
        *,
        session_id: str,
    ) -> SendApproval:
        approval = self.store.get_approval(
            approval_id, session_id=session_id
        )
        if approval.status in {"consumed", "expired"}:
            raise EmailError(
                EmailErrorCode.APPROVAL_INVALID,
                "发送审批当前不能撤销",
            )
        revoked = self.store.update_approval_status(  # type: ignore[attr-defined]
            approval_id,
            session_id=session_id,
            status="invalidated",
        )
        draft = self.store.get_draft(
            approval.draft_id,
            session_id=session_id,
            profile=approval.profile,
        )
        self.store.save_draft(
            draft.model_copy(
                update={
                    "status": "draft_only",
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        return revoked

    async def send(
        self,
        *,
        draft_id: str,
        expected_content_sha256: str,
        approval_id: str,
        idempotency_key: str,
        session_id: str,
        profile: str | None = None,
    ) -> SendReceipt:
        selected, configured = self.resolve_profile(profile)
        _, adapter = self.adapter(selected)
        draft = self.store.get_draft(
            draft_id, session_id=session_id, profile=selected
        )
        existing = self.store.find_receipt(draft_id, idempotency_key)
        if existing is not None:
            return existing
        if draft.content_sha256 != expected_content_sha256:
            raise EmailError(
                EmailErrorCode.DRAFT_CHANGED,
                "草稿内容与待发送指纹不一致",
            )
        capabilities = await adapter.capabilities()
        if capabilities.real_send and not configured.real_send_enabled:
            raise EmailError(
                EmailErrorCode.REAL_SEND_DISABLED,
                "当前邮箱 profile 未开启真实发送",
            )
        if not capabilities.real_send and not capabilities.simulated_send:
            raise EmailError(
                EmailErrorCode.CAPABILITY_NOT_SUPPORTED,
                "当前邮箱 profile 不支持发送",
            )
        approval = self.store.get_approval(
            approval_id, session_id=session_id
        )
        if approval.status == "expired":
            raise EmailError(
                EmailErrorCode.APPROVAL_EXPIRED,
                "发送审批已经过期，请重新预览草稿",
            )
        if approval.status == "consumed":
            raise EmailError(
                EmailErrorCode.APPROVAL_CONSUMED,
                "发送审批已经使用",
            )
        if approval.status != "approved":
            raise EmailError(
                EmailErrorCode.APPROVAL_REQUIRED,
                "发送前需要用户确认当前草稿",
            )
        self._assert_approval_matches(approval, draft)
        self.store.update_approval_status(  # type: ignore[attr-defined]
            approval_id,
            session_id=session_id,
            status="consumed",
        )
        self.store.save_draft(
            draft.model_copy(
                update={
                    "status": "sending",
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        try:
            receipt = await adapter.send_draft(
                draft, idempotency_key=idempotency_key
            )
        except EmailError as exc:
            if exc.code == EmailErrorCode.SEND_STATUS_UNKNOWN:
                receipt = SendReceipt(
                    delivery_mode=(
                        "real" if capabilities.real_send else "mock"
                    ),
                    status="unknown",
                    external_delivery=False,
                    content_sha256=draft.content_sha256,
                    recipient_count=(
                        len(draft.to) + len(draft.cc) + len(draft.bcc)
                    ),
                    idempotency_key_hash=idempotency_hash(idempotency_key),
                    source_ref=f"email-send-receipt:{uuid4()}",
                )
                self.store.save_receipt(
                    draft.draft_id, idempotency_key, receipt
                )
                self.store.save_draft(
                    draft.model_copy(
                        update={
                            "status": "send_status_unknown",
                            "updated_at": datetime.now(UTC),
                        }
                    )
                )
                return receipt
            self.store.save_draft(
                draft.model_copy(
                    update={
                        "status": "send_failed",
                        "updated_at": datetime.now(UTC),
                    }
                )
            )
            raise
        final_status = (
            "sent" if receipt.status == "sent" else "simulated_sent"
        )
        self.store.save_draft(
            draft.model_copy(
                update={
                    "status": final_status,
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        return receipt

    def _externalize_summary(
        self,
        item: EmailMessageSummary,
        session_id: str,
        profile: str,
    ) -> EmailMessageSummary:
        message_ref = self.store.create_reference(
            session_id=session_id,
            profile=profile,
            object_type="message",
            object_id=item.message_ref,
        )
        thread_ref = (
            self.store.create_reference(
                session_id=session_id,
                profile=profile,
                object_type="thread",
                object_id=item.thread_ref,
            )
            if item.thread_ref
            else None
        )
        source_ref = self.store.create_reference(
            session_id=session_id,
            profile=profile,
            object_type="source",
            object_id=item.source_ref,
        )
        return item.model_copy(
            update={
                "message_ref": message_ref,
                "thread_ref": thread_ref,
                "source_ref": source_ref,
            }
        )

    @staticmethod
    def _validate_recipients(
        to: list[str], cc: list[str], bcc: list[str]
    ) -> None:
        all_addresses = [*to, *cc, *bcc]
        if not to:
            raise EmailError(
                EmailErrorCode.INVALID_RECIPIENT,
                "邮件至少需要一个主收件人",
            )
        if len(all_addresses) > 10:
            raise EmailError(
                EmailErrorCode.INVALID_RECIPIENT,
                "首版单封邮件最多允许 10 个收件地址",
            )
        normalized = [value.strip().lower() for value in all_addresses]
        if any(
            not EMAIL_PATTERN.fullmatch(value) or "\r" in value or "\n" in value
            for value in normalized
        ):
            raise EmailError(
                EmailErrorCode.INVALID_RECIPIENT,
                "邮件包含无效收件地址",
            )
        if len(set(normalized)) != len(normalized):
            raise EmailError(
                EmailErrorCode.INVALID_RECIPIENT,
                "同一收件地址不能重复出现在收件人列表中",
            )

    def _resolve_attachment(self, value: str) -> StoredAttachment:
        root = self._resolve_attachment_root()
        path = Path(value)
        resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise EmailError(
                EmailErrorCode.ATTACHMENT_NOT_FOUND,
                "附件不在允许目录内",
            ) from exc
        if not resolved.exists() or not resolved.is_file():
            raise EmailError(
                EmailErrorCode.ATTACHMENT_NOT_FOUND,
                "没有找到指定附件",
            )
        try:
            content = resolved.read_bytes()
        except OSError as exc:
            raise EmailError(
                EmailErrorCode.ATTACHMENT_NOT_FOUND,
                "附件无法读取",
            ) from exc
        return StoredAttachment(
            attachment_ref=value,
            path=str(resolved),
            size_bytes=len(content),
            sha256=sha256(content).hexdigest(),
        )

    def _resolve_attachment_root(self) -> Path:
        value = Path(self.config.attachment_root)
        root = (
            value.resolve()
            if value.is_absolute()
            else (self.project_root / value).resolve()
        )
        try:
            root.relative_to(self.project_root)
        except ValueError as exc:
            raise EmailError(
                EmailErrorCode.ATTACHMENT_NOT_FOUND,
                "附件目录不在项目目录内",
            ) from exc
        return root

    @staticmethod
    def _recipient_hash(draft: StoredDraft) -> str:
        return stable_sha256(
            sorted(
                value.strip().lower()
                for value in [*draft.to, *draft.cc, *draft.bcc]
            )
        )

    def _assert_approval_matches(
        self, approval: SendApproval, draft: StoredDraft
    ) -> None:
        if (
            approval.profile != draft.profile
            or approval.draft_id != draft.draft_id
            or approval.content_sha256 != draft.content_sha256
            or approval.recipient_sha256 != self._recipient_hash(draft)
            or approval.attachment_sha256s
            != [item.sha256 for item in draft.attachments]
        ):
            self.store.invalidate_draft_approvals(draft.draft_id)  # type: ignore[attr-defined]
            raise EmailError(
                EmailErrorCode.APPROVAL_INVALID,
                "发送审批与当前草稿版本不匹配",
            )

    @staticmethod
    def _validate_search_query(query: EmailSearchQuery) -> None:
        if not any(
            (
                query.sender,
                query.recipient,
                query.subject,
                query.keywords,
                query.date_from,
                query.date_to,
                query.unread_only,
            )
        ):
            raise EmailError(
                EmailErrorCode.QUERY_INVALID,
                "请至少提供一个邮箱搜索条件",
            )
        if (
            query.date_from is not None
            and query.date_to is not None
            and query.date_from > query.date_to
        ):
            raise EmailError(
                EmailErrorCode.QUERY_INVALID,
                "邮箱搜索的开始时间不能晚于结束时间",
            )

    @staticmethod
    def _search_query_hash(query: EmailSearchQuery) -> str:
        payload: dict[str, Any] = query.model_dump(mode="json")
        payload.pop("cursor", None)
        return stable_sha256(payload)

    def _resolve_fixture_root(self, value: str) -> Path:
        path = Path(value)
        resolved = (
            path.resolve()
            if path.is_absolute()
            else (self.project_root / path).resolve()
        )
        try:
            resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise EmailError(
                EmailErrorCode.FIXTURE_INVALID,
                "Mock 邮箱 fixture 路径不在项目目录内",
            ) from exc
        return resolved
