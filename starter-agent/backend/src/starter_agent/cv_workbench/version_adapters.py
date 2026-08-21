"""Persistent Draft and immutable ResumeVersion content adapters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from starter_agent.cv_workbench.contracts import ContentReference
from starter_agent.cv_workbench.resume_import_adapters import ScopedKnowledgeResumeImporter
from starter_agent.cv_workbench.versioning import VersioningError
from starter_agent.infrastructure.session_store import SQLiteSessionStore
from starter_agent.knowledge.models import KnowledgeScope
from starter_agent.knowledge.store import SQLiteKnowledgeStore


class SessionKnowledgeVersionContentRepository:
    def __init__(
        self,
        artifacts: SQLiteSessionStore,
        knowledge: SQLiteKnowledgeStore,
        *,
        retention_days: int = 30,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self.artifacts = artifacts
        self.knowledge = knowledge
        self.importer = ScopedKnowledgeResumeImporter(knowledge)
        self.retention_days = retention_days
        self.clock = clock

    def read(
        self,
        reference: ContentReference,
        *,
        principal: str,
        workspace_id: str,
    ) -> str:
        if reference.artifact_id is not None:
            artifact = self.artifacts.get_tool_artifact_for_principal(
                reference.artifact_id, principal=principal
            )
            if artifact is None:
                raise VersioningError("version_artifact_unavailable")
            if artifact.get("expired"):
                raise VersioningError("version_artifact_expired")
            content = str(artifact.get("content") or "")
            if sha256(content.encode()).hexdigest() != reference.content_sha256:
                raise VersioningError("version_artifact_hash_mismatch")
            return content
        if not (
            reference.knowledge_base_id
            and reference.document_id
            and reference.document_version_id
        ):
            raise VersioningError("version_content_reference_incomplete")
        scope = KnowledgeScope(user_id=principal, project_id=workspace_id)
        version = self.knowledge.get_document_version(
            scope,
            UUID(reference.knowledge_base_id),
            UUID(reference.document_id),
            UUID(reference.document_version_id),
        )
        if version is None:
            raise VersioningError("version_document_unavailable")
        if version.content_sha256 != reference.content_sha256:
            raise VersioningError("version_document_hash_mismatch")
        return version.source_text

    def write_draft(
        self,
        *,
        draft_id: str,
        revision: int,
        markdown: str,
        content_sha256: str,
        principal: str,
        workspace_id: str,
    ) -> ContentReference:
        if sha256(markdown.encode()).hexdigest() != content_sha256:
            raise VersioningError("draft_content_hash_mismatch")
        source_ref = f"artifact:resume-draft:{draft_id}:{revision}"
        namespace = uuid5(NAMESPACE_URL, f"resume-draft:{workspace_id}:{draft_id}")
        now = self.clock()
        self.artifacts.save_tool_artifact(
            source_ref=source_ref,
            session_id=namespace,
            turn_id=uuid5(namespace, str(revision)),
            tool_name="result_envelope",
            content=markdown,
            call_id=f"{draft_id}:{revision}",
            content_sha256=content_sha256,
            truncation_summary={
                "source_type": "resume_draft",
                "workspace_id": workspace_id,
                "revision": revision,
                "complete": True,
            },
            parent_run_id=f"local-draft:{draft_id}",
            access_level="restricted",
            principal=principal,
            expires_at=now + timedelta(days=self.retention_days),
        )
        return ContentReference(
            content_sha256=content_sha256, artifact_id=source_ref
        )

    def publish_version(
        self,
        *,
        version_id: str,
        markdown: str,
        content_sha256: str,
        principal: str,
        workspace_id: str,
    ) -> ContentReference:
        if sha256(markdown.encode()).hexdigest() != content_sha256:
            raise VersioningError("resume_version_hash_mismatch")
        imported = self.importer.ingest_resume(
            operation_id=f"version:{version_id}",
            filename=f"{version_id}.md",
            normalized_markdown=markdown,
            content_sha256=content_sha256,
            principal=principal,
            workspace_id=workspace_id,
        )
        return ContentReference(
            content_sha256=imported.content_sha256,
            knowledge_base_id=imported.knowledge_base_id,
            document_id=imported.document_id,
            document_version_id=imported.document_version_id,
        )
