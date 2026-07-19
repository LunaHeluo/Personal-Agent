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
    version: int | None = None
    content_sha256: str | None = None
    chunk_count: int = 0
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


class ParsedBlock(BaseModel):
    kind: Literal["paragraph", "list", "table", "quote", "code"]
    text: str
    search_text: str
    section_path: list[str] = Field(default_factory=list)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class ParsedDocument(BaseModel):
    normalized_source: str
    blocks: list[ParsedBlock] = Field(default_factory=list)


class SourceLocation(BaseModel):
    page: int | None = None
    section_path: list[str] = Field(default_factory=list)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class KnowledgeChunk(BaseModel):
    id: UUID
    document_id: UUID
    version_id: UUID
    knowledge_base_id: UUID
    user_id: str
    project_id: str
    version: int
    filename: str
    page: int | None = None
    section_path: list[str] = Field(default_factory=list)
    start_line: int
    end_line: int
    ordinal: int
    text: str
    search_text: str
    content_sha256: str
    created_at: datetime

    @property
    def source_ref(self) -> str:
        return (
            f"{self.filename}@v{self.version}"
            f"#L{self.start_line}-L{self.end_line}"
        )


class RetrievalMatch(BaseModel):
    chunk_id: UUID
    document_id: UUID
    document_type: str
    filename: str
    version: int
    page: int | None = None
    section_path: list[str] = Field(default_factory=list)
    start_line: int
    end_line: int
    preview: str
    source_ref: str
    bm25_score: float | None = None
    matched_terms: list[str] = Field(default_factory=list)
    rank: int
