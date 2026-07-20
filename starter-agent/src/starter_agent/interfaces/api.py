from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from starter_agent.bootstrap import (
    create_application,
    create_knowledge_service,
    get_settings,
)
from starter_agent.domain.errors import AgentError
from starter_agent.domain.models import (
    ChatResult,
    MemoryCategory,
    MemoryItem,
    MemorySensitivity,
    Role,
    SummaryTrace,
    TokenUsage,
    ToolResult,
)
from starter_agent.observability.logging import get_logger
from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.tools.email.approval import EmailApprovalService
from starter_agent.tools.email.errors import EmailError
from starter_agent.tools.email.models import ApprovalChallengeView, SendApproval
from starter_agent.tools.base import ToolContext


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=50_000)
    session_id: UUID | None = None
    provider: str | None = None
    model: str | None = None
    tool: str | None = Field(default=None, min_length=1, max_length=100)
    tool_governance_enabled: bool = True


class ProviderInfo(BaseModel):
    name: str
    type: str
    models: list[str] = Field(default_factory=list)
    is_default: bool = False
    has_api_key: bool = True


class ProvidersResponse(BaseModel):
    default_provider: str
    default_model: str
    providers: list[ProviderInfo]


class ToolInfo(BaseModel):
    name: str
    description: str
    risk_level: str


class ToolsResponse(BaseModel):
    tools: list[ToolInfo]


class SessionSummary(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    message_count: int = 0
    last_message: str | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]
    total: int = 0
    offset: int = 0
    limit: int = 50
    has_more: bool = False


class HistoryMessage(BaseModel):
    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    created_at: datetime
    turn_id: UUID


class SessionMessagesResponse(BaseModel):
    session_id: UUID
    messages: list[HistoryMessage]
    session_usage: TokenUsage = Field(default_factory=TokenUsage)
    max_total_tokens: int = 128_000
    token_budget_status: str = "normal"
    latest_summary_trace: SummaryTrace | None = None


class MemoryCreateRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=2_000)
    category: MemoryCategory
    source_type: Literal[
        "user_confirmed", "local_file", "external_web", "email", "tool_output"
    ] = "user_confirmed"
    expires_at: datetime | None = None
    sensitivity: MemorySensitivity = "personal"
    confirmed: bool


class MemoryUpdateRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=2_000)
    category: MemoryCategory
    expires_at: datetime | None = None
    sensitivity: MemorySensitivity = "personal"
    status: Literal["active", "disabled"] = "active"
    confirmed: bool


class MemoryListResponse(BaseModel):
    memories: list[MemoryItem]


class EmailApprovalChallengeRequest(BaseModel):
    session_id: UUID
    profile: str | None = Field(default=None, max_length=80)
    user_ref: str | None = Field(default=None, max_length=200)


class EmailApprovalActionRequest(BaseModel):
    session_id: UUID
    confirmed: bool = False


class EmailApprovalSendRequest(BaseModel):
    session_id: UUID
    idempotency_key: str = Field(min_length=16, max_length=200)


class KnowledgeRetrieveRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10_000)
    top_k: int = Field(default=6, ge=1, le=50)
    document_ids: list[UUID] | None = None
    document_types: list[str] | None = None
    filenames: list[str] | None = None
    versions: list[int] | None = None


def _email_approval_service() -> EmailApprovalService:
    manager = create_application().runtime.tools.email_manager
    if manager is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "email_tools_not_enabled",
                "message": "邮件工具套装尚未启用",
            },
        )
    return EmailApprovalService(manager)


def _email_http_error(error: EmailError) -> HTTPException:
    return HTTPException(status_code=400, detail=error.public_payload())


def _knowledge_http_error(error: KnowledgeError) -> HTTPException:
    return HTTPException(
        status_code=error.http_status,
        detail=error.to_public_dict(),
    )


MEMORY_TTL_DAYS: dict[str, int] = {
    "profile": 365,
    "preference": 180,
    "constraint": 180,
    "verified_skill": 365,
    "application_state": 365,
}


def _memory_expiry(category: str, expires_at: datetime | None) -> datetime:
    if expires_at is not None:
        return expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
    return datetime.now(UTC) + timedelta(days=MEMORY_TTL_DAYS[category])


def _validate_memory_write(
    *, source_type: str, confirmed: bool, expires_at: datetime | None
) -> None:
    if not confirmed:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "memory_confirmation_required",
                "message": "写入或修改长期记忆前需要用户明确确认",
            },
        )
    if source_type != "user_confirmed":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "external_memory_source_not_allowed",
                "message": "网页、邮件、工具结果或未经核验的文件内容不能直接写入长期记忆",
            },
        )
    if expires_at is not None:
        normalized = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        if normalized <= datetime.now(UTC):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "memory_expiry_invalid",
                    "message": "长期记忆的过期时间必须晚于当前时间",
                },
            )


def _summary_text(value: str | None, limit: int = 80) -> str | None:
    if not value:
        return None
    text = " ".join(value.split())
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


@asynccontextmanager
async def _api_lifespan(_api: FastAPI):
    yield
    await create_application().wait_for_background_tasks()


def create_api() -> FastAPI:
    api = FastAPI(
        title="Starter Agent API", version="0.1.0", lifespan=_api_lifespan
    )
    api.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8001",
            "http://localhost:8001",
        ],
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @api.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "name": get_settings().app.name}

    @api.get("/v1/providers", response_model=ProvidersResponse)
    async def providers() -> ProvidersResponse:
        settings = get_settings()
        infos: list[ProviderInfo] = []
        for name in sorted(settings.providers):
            config = settings.providers[name]
            infos.append(
                ProviderInfo(
                    name=name,
                    type=config.type,
                    models=config.models,
                    is_default=name == settings.model.default_provider,
                    has_api_key=(
                        True
                        if config.type == "mock"
                        else bool(settings.provider_api_key(name))
                    ),
                )
            )
        return ProvidersResponse(
            default_provider=settings.model.default_provider,
            default_model=settings.model.default_model,
            providers=infos,
        )

    @api.get("/v1/tools", response_model=ToolsResponse)
    async def tools() -> ToolsResponse:
        registry = create_application().runtime.tools
        return ToolsResponse(
            tools=[
                ToolInfo(
                    name=tool.name,
                    description=tool.description,
                    risk_level=tool.risk_level,
                )
                for tool in registry.list()
            ]
        )

    @api.get("/v1/knowledge-bases")
    async def list_knowledge_bases() -> dict[str, object]:
        bases = create_knowledge_service().list_knowledge_bases()
        return {
            "knowledge_bases": [
                item.model_dump(mode="json") for item in bases
            ]
        }

    @api.post(
        "/v1/knowledge-bases/{knowledge_base_id}/documents",
        status_code=202,
    )
    async def upload_knowledge_document(
        knowledge_base_id: UUID,
        file: UploadFile = File(...),
        document_type: str = Form("other"),
        confirmed_authorized: bool = Form(False),
    ) -> dict[str, object]:
        try:
            content = await file.read(
                get_settings().knowledge.max_upload_bytes + 1
            )
            result = create_knowledge_service().upload(
                knowledge_base_id=knowledge_base_id,
                filename=file.filename or "",
                content=content,
                document_type=document_type,
                confirmed_authorized=confirmed_authorized,
            )
        except KnowledgeError as error:
            raise _knowledge_http_error(error) from error
        return {
            "document_id": str(result.document.id),
            "version_id": str(result.version.id),
            "job_id": str(result.job.id),
            "status": result.job.status,
            "stage": result.job.stage,
            "content_sha256": result.version.content_sha256,
        }

    @api.get("/v1/knowledge-bases/{knowledge_base_id}/documents")
    async def list_knowledge_documents(
        knowledge_base_id: UUID,
    ) -> dict[str, object]:
        documents = create_knowledge_service().list_documents(
            knowledge_base_id
        )
        return {
            "documents": [
                item.model_dump(mode="json") for item in documents
            ]
        }

    @api.get(
        "/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}"
    )
    async def get_knowledge_document(
        knowledge_base_id: UUID,
        document_id: UUID,
    ) -> dict[str, object]:
        try:
            document = create_knowledge_service().get_document(
                knowledge_base_id, document_id
            )
        except KnowledgeError as error:
            raise _knowledge_http_error(error) from error
        return document.model_dump(mode="json")

    @api.get(
        "/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/chunks"
    )
    async def list_knowledge_chunks(
        knowledge_base_id: UUID,
        document_id: UUID,
        after_ordinal: int = Query(default=-1, ge=-1),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, object]:
        try:
            chunks = create_knowledge_service().list_chunks(
                knowledge_base_id,
                document_id,
                after_ordinal=after_ordinal,
                limit=limit,
            )
        except KnowledgeError as error:
            raise _knowledge_http_error(error) from error
        return {
            "chunks": [
                {
                    **item.model_dump(
                        mode="json",
                        exclude={"text", "search_text"},
                    ),
                    "source_ref": item.source_ref,
                    "preview": item.text[:400],
                }
                for item in chunks
            ],
            "next_after_ordinal": (
                chunks[-1].ordinal if len(chunks) == limit else None
            ),
        }

    @api.post("/v1/knowledge-bases/{knowledge_base_id}/retrieve")
    async def retrieve_knowledge(
        knowledge_base_id: UUID,
        request: KnowledgeRetrieveRequest,
    ) -> dict[str, object]:
        try:
            matches = create_knowledge_service().retrieve(
                knowledge_base_id,
                request.question,
                top_k=request.top_k,
                document_ids=request.document_ids,
                document_types=request.document_types,
                filenames=request.filenames,
                versions=request.versions,
            )
        except KnowledgeError as error:
            raise _knowledge_http_error(error) from error
        return {
            "status": "ok" if matches else "no_evidence",
            "matches": [
                item.model_dump(mode="json") for item in matches
            ],
        }

    @api.put(
        "/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/content",
        status_code=202,
    )
    async def update_knowledge_document(
        knowledge_base_id: UUID,
        document_id: UUID,
        file: UploadFile = File(...),
        confirmed_authorized: bool = Form(False),
        if_match: str = Header(..., alias="If-Match"),
    ) -> dict[str, object]:
        try:
            content = await file.read(
                get_settings().knowledge.max_upload_bytes + 1
            )
            result = create_knowledge_service().update_document(
                knowledge_base_id,
                document_id,
                expected_content_sha256=if_match.strip('"'),
                filename=file.filename or "",
                content=content,
                confirmed_authorized=confirmed_authorized,
            )
        except KnowledgeError as error:
            raise _knowledge_http_error(error) from error
        return {
            "document_id": str(result.document.id),
            "version_id": str(result.version.id),
            "job_id": str(result.job.id),
            "version": result.version.version,
            "content_sha256": result.version.content_sha256,
            "status": "queued",
        }

    @api.delete(
        "/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}"
    )
    async def delete_knowledge_document(
        knowledge_base_id: UUID,
        document_id: UUID,
    ) -> dict[str, object]:
        deleted = create_knowledge_service().delete_document(
            knowledge_base_id, document_id
        )
        return {"status": "deleted", "existed": deleted}

    @api.get(
        "/v1/knowledge-bases/{knowledge_base_id}/citations/{chunk_id}"
    )
    async def resolve_knowledge_citation(
        knowledge_base_id: UUID,
        chunk_id: UUID,
    ) -> dict[str, object]:
        try:
            chunk = create_knowledge_service().resolve_citation(
                knowledge_base_id, chunk_id
            )
        except KnowledgeError as error:
            raise _knowledge_http_error(error) from error
        return {
            **chunk.model_dump(
                mode="json", exclude={"text", "search_text"}
            ),
            "source_ref": chunk.source_ref,
            "quote": chunk.text[:400],
        }

    @api.get(
        "/v1/knowledge-bases/{knowledge_base_id}/ingestion-jobs/{job_id}"
    )
    async def get_knowledge_ingestion_job(
        knowledge_base_id: UUID,
        job_id: UUID,
    ) -> dict[str, object]:
        try:
            job = create_knowledge_service().get_job(
                knowledge_base_id, job_id
            )
        except KnowledgeError as error:
            raise _knowledge_http_error(error) from error
        return job.model_dump(mode="json")

    @api.post(
        "/v1/email/drafts/{draft_id}/approval-challenges",
        response_model=ApprovalChallengeView,
    )
    async def create_email_approval_challenge(
        draft_id: str,
        request: EmailApprovalChallengeRequest,
    ) -> ApprovalChallengeView:
        try:
            return _email_approval_service().create_challenge(
                draft_id,
                session_id=str(request.session_id),
                profile=request.profile,
                user_ref=request.user_ref,
            )
        except EmailError as error:
            raise _email_http_error(error) from error

    @api.post(
        "/v1/email/approval-challenges/{approval_id}/confirm",
        response_model=SendApproval,
    )
    async def confirm_email_approval(
        approval_id: str,
        request: EmailApprovalActionRequest,
    ) -> SendApproval:
        try:
            return _email_approval_service().confirm(
                approval_id,
                session_id=str(request.session_id),
                confirmed=request.confirmed,
            )
        except EmailError as error:
            raise _email_http_error(error) from error

    @api.get(
        "/v1/email/approvals/{approval_id}",
        response_model=SendApproval,
    )
    async def get_email_approval(
        approval_id: str,
        session_id: UUID = Query(),
    ) -> SendApproval:
        try:
            return _email_approval_service().get(
                approval_id, session_id=str(session_id)
            )
        except EmailError as error:
            raise _email_http_error(error) from error

    @api.post(
        "/v1/email/approvals/{approval_id}/revoke",
        response_model=SendApproval,
    )
    async def revoke_email_approval(
        approval_id: str,
        request: EmailApprovalActionRequest,
    ) -> SendApproval:
        try:
            return _email_approval_service().revoke(
                approval_id, session_id=str(request.session_id)
            )
        except EmailError as error:
            raise _email_http_error(error) from error

    @api.post(
        "/v1/email/approvals/{approval_id}/send",
        response_model=ToolResult,
    )
    async def send_approved_email(
        approval_id: str,
        request: EmailApprovalSendRequest,
    ) -> ToolResult:
        application = create_application()
        tool = application.runtime.tools.get("email_send")
        manager = application.runtime.tools.email_manager
        if tool is None or manager is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "email_tools_not_enabled",
                    "message": "邮件发送工具尚未启用",
                },
            )
        try:
            application.runtime.policy.check(tool)
            approval = EmailApprovalService(manager).get(
                approval_id,
                session_id=str(request.session_id),
            )
            draft = manager.store.get_draft(
                approval.draft_id,
                session_id=str(request.session_id),
                profile=approval.profile,
            )
            result = await tool.execute(
                {
                    "profile": approval.profile,
                    "draft_id": draft.draft_id,
                    "expected_content_sha256": draft.content_sha256,
                    "approval_id": approval.approval_id,
                    "idempotency_key": request.idempotency_key,
                },
                ToolContext(
                    session_id=request.session_id,
                    turn_id=uuid4(),
                ),
            )
        except EmailError as error:
            raise _email_http_error(error) from error
        except AgentError as error:
            raise HTTPException(
                status_code=error.http_status,
                detail=error.to_public_dict(),
            ) from error
        if not result.ok:
            raise HTTPException(
                status_code=400,
                detail=result.model_dump(mode="json", exclude_none=True),
            )
        get_logger(
            session_id=str(request.session_id),
            approval_id=approval_id,
        ).info(
            "email.manual_send_completed",
            status=result.metadata.get("status"),
            external_delivery=result.metadata.get("external_delivery"),
        )
        return result

    @api.post("/v1/chat", response_model=ChatResult)
    async def chat(request: ChatRequest) -> ChatResult:
        try:
            return await create_application().chat(
                content=request.message,
                session_id=request.session_id,
                provider_name=request.provider,
                model=request.model,
                required_tool_name=request.tool,
                tool_governance_enabled=request.tool_governance_enabled,
            )
        except AgentError as exc:
            raise HTTPException(
                status_code=exc.http_status,
                detail=exc.to_public_dict(),
            ) from exc

    @api.post("/v1/chat/stream")
    async def chat_stream(request: ChatRequest) -> StreamingResponse:
        async def events():
            queue: asyncio.Queue[dict | None] = asyncio.Queue()

            async def on_delta(text: str) -> None:
                await queue.put({"type": "delta", "content": text})

            async def on_tool_event(event: dict) -> None:
                await queue.put(event)

            async def run_chat() -> None:
                try:
                    result = await create_application().chat(
                        content=request.message,
                        session_id=request.session_id,
                        provider_name=request.provider,
                        model=request.model,
                        on_delta=on_delta,
                        required_tool_name=request.tool,
                        on_tool_event=on_tool_event,
                        tool_governance_enabled=request.tool_governance_enabled,
                    )
                    await queue.put({"type": "done", "result": result.model_dump(mode="json")})
                except AgentError as exc:
                    await queue.put(
                        {
                            "type": "error",
                            "error": exc.to_public_dict(),
                        }
                    )
                finally:
                    await queue.put(None)

            task = asyncio.create_task(run_chat())
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                await task

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @api.get("/v1/sessions", response_model=SessionListResponse)
    async def sessions(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> SessionListResponse:
        application = create_application()
        rows = application.list_sessions(limit=limit, offset=offset)
        total = application.count_sessions()
        return SessionListResponse(
            total=total,
            offset=offset,
            limit=limit,
            has_more=offset + len(rows) < total,
            sessions=[
                SessionSummary(
                    id=row.id,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    title=_summary_text(row.first_user_message),
                    message_count=row.message_count,
                    last_message=_summary_text(row.last_message),
                )
                for row in rows
            ]
        )

    @api.get(
        "/v1/sessions/{session_id}/messages",
        response_model=SessionMessagesResponse,
    )
    async def session_messages(
        session_id: UUID,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> SessionMessagesResponse:
        application = create_application()
        try:
            rows = application.list_session_messages(
                session_id=session_id,
                limit=limit,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        session_usage = application.session_usage(session_id)
        return SessionMessagesResponse(
            session_id=session_id,
            session_usage=session_usage,
            max_total_tokens=get_settings().context.max_total_tokens,
            token_budget_status=application.token_budget_status(
                session_usage.total_tokens
            ),
            latest_summary_trace=application.latest_summary_trace(session_id),
            messages=[
                HistoryMessage(
                    role=row.role,
                    content=row.content,
                    name=row.name,
                    tool_call_id=row.tool_call_id,
                    created_at=row.created_at,
                    turn_id=row.turn_id,
                )
                for row in rows
            ],
        )

    @api.delete("/v1/sessions/{session_id}")
    async def delete_session(session_id: UUID) -> dict[str, str]:
        deleted = create_application().delete_session(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"status": "deleted"}

    @api.delete("/v1/sessions")
    async def delete_all_sessions() -> dict[str, int | str]:
        application = create_application()
        await application.wait_for_background_tasks()
        deleted = application.delete_all_sessions()
        get_logger().info("sessions.deleted_all", deleted_sessions=deleted)
        return {"status": "deleted", "deleted_sessions": deleted}

    @api.get("/v1/memories", response_model=MemoryListResponse)
    async def list_memories(
        active_only: bool = Query(default=False),
    ) -> MemoryListResponse:
        return MemoryListResponse(
            memories=create_application().list_memories(active_only=active_only)
        )

    @api.post("/v1/memories", response_model=MemoryItem, status_code=201)
    async def create_memory(request: MemoryCreateRequest) -> MemoryItem:
        _validate_memory_write(
            source_type=request.source_type,
            confirmed=request.confirmed,
            expires_at=request.expires_at,
        )
        item = create_application().create_memory(
            key=request.key.strip(),
            value=request.value.strip(),
            category=request.category,
            source_ref="user:memory-panel",
            source_type="user_confirmed",
            confidence=1.0,
            verified_by="user",
            expires_at=_memory_expiry(request.category, request.expires_at),
            sensitivity=request.sensitivity,
        )
        get_logger(memory_id=str(item.id)).info(
            "memory.created",
            category=item.category,
            source_type=item.source_type,
            expires_at=item.expires_at.isoformat() if item.expires_at else None,
        )
        return item

    @api.put("/v1/memories/{memory_id}", response_model=MemoryItem)
    async def update_memory(
        memory_id: UUID, request: MemoryUpdateRequest
    ) -> MemoryItem:
        _validate_memory_write(
            source_type="user_confirmed",
            confirmed=request.confirmed,
            expires_at=request.expires_at,
        )
        item = create_application().update_memory(
            memory_id,
            key=request.key.strip(),
            value=request.value.strip(),
            category=request.category,
            source_ref="user:memory-panel:update",
            confidence=1.0,
            expires_at=_memory_expiry(request.category, request.expires_at),
            sensitivity=request.sensitivity,
            status=request.status,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        get_logger(memory_id=str(item.id)).info(
            "memory.updated",
            category=item.category,
            status=item.status,
        )
        return item

    @api.delete("/v1/memories/{memory_id}")
    async def delete_memory(memory_id: UUID) -> dict[str, str]:
        if not create_application().delete_memory(memory_id):
            raise HTTPException(status_code=404, detail="Memory not found")
        # The audit event deliberately contains only the ID, never the memory value.
        get_logger(memory_id=str(memory_id)).info("memory.deleted")
        return {"status": "deleted", "id": str(memory_id)}

    return api


app = create_api()
