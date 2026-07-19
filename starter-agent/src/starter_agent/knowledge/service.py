from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.ingestion import KnowledgeIngestionPipeline
from starter_agent.knowledge.models import (
    IngestionJob,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeScope,
    UploadBundle,
    KnowledgeChunk,
)
from starter_agent.knowledge.security import validate_markdown_upload
from starter_agent.knowledge.store import SQLiteKnowledgeStore
from starter_agent.settings import AgentSettings


class KnowledgeApplicationService:
    def __init__(self, settings: AgentSettings, store: SQLiteKnowledgeStore):
        self.settings = settings
        self.store = store
        self.scope = KnowledgeScope(
            user_id=settings.knowledge.default_user_id,
            project_id=settings.knowledge.default_project_id,
        )
        self.default_knowledge_base_id = uuid5(
            NAMESPACE_URL,
            f"starter-agent:{self.scope.user_id}:{self.scope.project_id}:default",
        )
        self.store.ensure_knowledge_base(
            self.scope,
            knowledge_base_id=self.default_knowledge_base_id,
            name="求职知识库",
        )
        self.ingestion = KnowledgeIngestionPipeline(
            store,
            target_chars=settings.knowledge.chunk_target_chars,
            overlap_chars=settings.knowledge.chunk_overlap_chars,
        )

    def list_knowledge_bases(self) -> list[KnowledgeBase]:
        return self.store.list_knowledge_bases(self.scope)

    def upload(
        self,
        *,
        knowledge_base_id: UUID,
        filename: str,
        content: bytes,
        document_type: str,
        confirmed_authorized: bool,
    ) -> UploadBundle:
        validated = validate_markdown_upload(
            filename=filename,
            content=content,
            confirmed_authorized=confirmed_authorized,
            max_bytes=self.settings.knowledge.max_upload_bytes,
            allowed_extensions=self.settings.knowledge.allowed_extensions,
        )
        if (
            self.store.count_documents(self.scope, knowledge_base_id)
            >= self.settings.knowledge.max_documents
        ):
            raise KnowledgeError("knowledge_capacity_exceeded")
        upload = self.store.create_upload(
            self.scope,
            knowledge_base_id=knowledge_base_id,
            filename=validated.filename,
            document_type=document_type,
            source_text=validated.text,
            content_sha256=validated.content_sha256,
        )
        self.ingestion.run(self.scope, upload)
        return upload

    def list_documents(self, knowledge_base_id: UUID) -> list[KnowledgeDocument]:
        return self.store.list_documents(self.scope, knowledge_base_id)

    def get_document(
        self, knowledge_base_id: UUID, document_id: UUID
    ) -> KnowledgeDocument:
        document = self.store.get_document(
            self.scope, knowledge_base_id, document_id
        )
        if document is None:
            raise KnowledgeError("document_not_found")
        return document

    def get_job(
        self, knowledge_base_id: UUID, job_id: UUID
    ) -> IngestionJob:
        job = self.store.get_job(self.scope, knowledge_base_id, job_id)
        if job is None:
            raise KnowledgeError("document_not_found")
        return job

    def list_chunks(
        self,
        knowledge_base_id: UUID,
        document_id: UUID,
        *,
        after_ordinal: int,
        limit: int,
    ) -> list[KnowledgeChunk]:
        if self.store.get_document(
            self.scope, knowledge_base_id, document_id
        ) is None:
            raise KnowledgeError("document_not_found")
        return self.store.list_chunks(
            self.scope,
            knowledge_base_id,
            document_id,
            after_ordinal=after_ordinal,
            limit=limit,
        )
