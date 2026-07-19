from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


DocumentStatus = Literal["queued", "processing", "indexed", "failed", "deleting"]
VersionStatus = Literal[
    "queued", "parsing", "normalizing", "chunking", "indexing", "indexed", "failed"
]
JobStatus = Literal["queued", "running", "succeeded", "failed"]
IngestionStage = Literal[
    "upload", "parse", "normalize", "chunk", "metadata", "index", "activate", "delete"
]


class KnowledgeScope(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str = Field(min_length=1, max_length=120)
    project_id: str = Field(min_length=1, max_length=120)


class KnowledgeBase(BaseModel):
    id: UUID
    user_id: str
    project_id: str
    name: str
    created_at: datetime
    updated_at: datetime


class KnowledgeDocument(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    user_id: str
    project_id: str
    filename: str
    document_type: str
    active_version_id: UUID | None = None
    status: DocumentStatus
    created_at: datetime
    updated_at: datetime


class DocumentVersion(BaseModel):
    id: UUID
    document_id: UUID
    knowledge_base_id: UUID
    version: int
    content_sha256: str
    source_text: str
    status: VersionStatus
    chunk_count: int = 0
    created_at: datetime
    indexed_at: datetime | None = None
    error_code: str | None = None


class IngestionJob(BaseModel):
    id: UUID
    document_id: UUID
    version_id: UUID
    knowledge_base_id: UUID
    user_id: str
    project_id: str
    status: JobStatus
    stage: IngestionStage
    progress_current: int = 0
    progress_total: int = 0
    error_code: str | None = None
    retryable: bool = False
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class UploadBundle(BaseModel):
    document: KnowledgeDocument
    version: DocumentVersion
    job: IngestionJob
