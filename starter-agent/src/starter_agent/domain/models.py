from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


Role = Literal["system", "user", "assistant", "tool"]
RiskLevel = Literal["read", "write", "external", "dangerous"]
MemoryCategory = Literal[
    "profile",
    "preference",
    "constraint",
    "verified_skill",
    "application_state",
]
MemorySensitivity = Literal["normal", "personal", "sensitive"]
MemoryStatus = Literal["active", "disabled", "expired"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    provider: str
    model: str
    usage: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    ok: bool
    data: Any = None
    display: str = ""
    error_code: str | None = None
    retryable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ContextUsage(BaseModel):
    raw_estimated_prompt_tokens: int = 0
    corrected_estimated_prompt_tokens: int = 0
    actual_prompt_tokens: int | None = None
    correction_coefficient: float = 1.0
    max_context_tokens: int = 128_000
    estimated: bool = True


class SummaryTrace(BaseModel):
    summary_id: UUID
    summary_type: Literal["session_summary"] = "session_summary"
    before_tokens: int
    after_tokens: int
    source_message_ids: list[UUID] = Field(default_factory=list)
    compacted_message_ids: list[UUID] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContinuationInfo(BaseModel):
    reason: Literal["max_model_calls"]
    model_calls: int
    tool_calls: int
    next_message: str


class ChatResult(BaseModel):
    session_id: UUID
    turn_id: UUID
    content: str
    provider: str
    model: str
    tool_calls: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)
    session_usage: TokenUsage = Field(default_factory=TokenUsage)
    max_total_tokens: int = 128_000
    token_budget_status: Literal["normal", "warning", "exceeded"] = "normal"
    context_usage: ContextUsage = Field(default_factory=ContextUsage)
    summary_trace: SummaryTrace | None = None
    tool_governance_enabled: bool = True
    finish_reason: Literal["completed", "continuation_required"] = "completed"
    continuation: ContinuationInfo | None = None


class StoredMessage(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    turn_id: UUID
    message: Message
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StoredContextSummary(BaseModel):
    id: UUID
    session_id: UUID
    content: str
    source_message_ids: list[UUID] = Field(default_factory=list)
    compacted_message_ids: list[UUID] = Field(default_factory=list)
    before_tokens: int
    after_tokens: int
    created_at: datetime


class StoredSessionSummary(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    first_user_message: str | None = None
    last_message: str | None = None


class MemoryItem(BaseModel):
    id: UUID
    key: str
    value: str
    category: MemoryCategory
    source_ref: str
    source_type: Literal["user_confirmed", "local_file", "conversation_inferred"]
    confidence: float = Field(ge=0, le=1)
    verified_by: Literal["user", "local_file", "memory_model"]
    expires_at: datetime | None = None
    sensitivity: MemorySensitivity = "personal"
    status: MemoryStatus = "active"
    created_at: datetime
    updated_at: datetime


class StoredHistoryMessage(BaseModel):
    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    created_at: datetime
    turn_id: UUID
