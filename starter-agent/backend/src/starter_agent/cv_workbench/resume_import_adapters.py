"""Adapters connecting resume import to existing Artifact and Knowledge stores."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from starter_agent.cv_workbench.resume_import import (
    KnowledgeImportResult,
    RawArtifact,
)
from starter_agent.infrastructure.session_store import SQLiteSessionStore
from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.ingestion import KnowledgeIngestionPipeline
from starter_agent.knowledge.models import KnowledgeScope
from starter_agent.knowledge.store import SQLiteKnowledgeStore


class SessionResumeArtifactWriter:
    def __init__(
        self,
        store: SQLiteSessionStore,
        *,
        retention_days: int = 14,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.retention_days = retention_days
        self.clock = clock

    def write_resume_source(
        self,
        *,
        operation_id: str,
        filename: str,
        content: bytes,
        principal: str,
        workspace_id: str,
    ) -> RawArtifact:
        text = content.decode("utf-8-sig")
        digest = sha256(content).hexdigest()
        source_ref = f"artifact:resume-import:{operation_id}"
        namespace = uuid5(NAMESPACE_URL, f"resume-import:{workspace_id}")
        now = self.clock()
        self.store.save_tool_artifact(
            source_ref=source_ref,
            session_id=namespace,
            turn_id=uuid5(namespace, operation_id),
            tool_name="resume_source_import",
            content=text,
            call_id=operation_id,
            content_sha256=digest,
            truncation_summary={"filename": filename, "complete": True},
            parent_run_id=f"local-import:{operation_id}",
            access_level="restricted",
            principal=principal,
            expires_at=now + timedelta(days=self.retention_days),
        )
        return RawArtifact(source_ref=source_ref, content_sha256=digest)


class ScopedKnowledgeResumeImporter:
    def __init__(
        self,
        store: SQLiteKnowledgeStore,
        *,
        chunk_target_chars: int = 1200,
        chunk_overlap_chars: int = 150,
        max_chunks: int = 5000,
    ) -> None:
        self.store = store
        self.pipeline = KnowledgeIngestionPipeline(
            store,
            target_chars=chunk_target_chars,
            overlap_chars=chunk_overlap_chars,
            max_chunks=max_chunks,
        )

    def ingest_resume(
        self,
        *,
        operation_id: str,
        filename: str,
        normalized_markdown: str,
        content_sha256: str,
        principal: str,
        workspace_id: str,
    ) -> KnowledgeImportResult:
        scope = KnowledgeScope(user_id=principal, project_id=workspace_id)
        knowledge_base_id = uuid5(
            NAMESPACE_URL,
            f"starter-agent:{principal}:{workspace_id}:cv-workbench",
        )
        self.store.ensure_knowledge_base(
            scope,
            knowledge_base_id=knowledge_base_id,
            name="CV Workbench",
        )
        existing = self.store.find_document_version_by_hash(
            scope, knowledge_base_id, content_sha256
        )
        if existing is not None:
            return KnowledgeImportResult(
                knowledge_base_id=str(existing.knowledge_base_id),
                document_id=str(existing.document_id),
                document_version_id=str(existing.id),
                content_sha256=existing.content_sha256,
            )
        upload = self.store.create_upload(
            scope,
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            document_type="resume",
            source_text=normalized_markdown,
            content_sha256=content_sha256,
        )
        try:
            self.pipeline.run(scope, upload)
        except Exception:
            self.store.discard_upload(scope, upload)
            raise
        document = self.store.get_document(
            scope, knowledge_base_id, upload.document.id
        )
        job = self.store.get_job(scope, knowledge_base_id, upload.job.id)
        if (
            document is None
            or job is None
            or document.status != "indexed"
            or job.status != "succeeded"
            or document.active_version_id != upload.version.id
        ):
            raise KnowledgeError("document_ingestion_failed")
        return KnowledgeImportResult(
            knowledge_base_id=str(knowledge_base_id),
            document_id=str(upload.document.id),
            document_version_id=str(upload.version.id),
            content_sha256=content_sha256,
        )
