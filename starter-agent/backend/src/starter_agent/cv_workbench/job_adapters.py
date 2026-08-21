"""Adapters that connect CV Workbench jobs to existing trusted stores/gates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from starter_agent.cv_workbench.contracts import ContentReference
from starter_agent.cv_workbench.jobs import (
    CandidateContent,
    JobContentRepository,
    JobServiceError,
    PublishedJobContent,
    StableUrlResult,
)
from starter_agent.infrastructure.session_store import SQLiteSessionStore
from starter_agent.job_research.jd import JobDescriptionNormalizer
from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.ingestion import KnowledgeIngestionPipeline
from starter_agent.knowledge.models import KnowledgeScope
from starter_agent.knowledge.store import SQLiteKnowledgeStore


class SafeJobArtifactGateway(Protocol):
    def fetch_job_artifact(
        self, url: str, *, principal: str, workspace_id: str
    ) -> str:
        """Return a restricted Artifact ref after the existing URL safety gate runs."""


class ExistingGateStableUrlFetcher:
    """Normalize only restricted Artifacts produced by the existing safe URL path."""

    def __init__(
        self,
        gateway: SafeJobArtifactGateway,
        artifacts: SQLiteSessionStore,
        *,
        normalizer: JobDescriptionNormalizer | None = None,
    ) -> None:
        self.gateway = gateway
        self.artifacts = artifacts
        self.normalizer = normalizer or JobDescriptionNormalizer()

    def fetch(
        self, url: str, *, principal: str, workspace_id: str
    ) -> StableUrlResult:
        source_ref = self.gateway.fetch_job_artifact(
            url, principal=principal, workspace_id=workspace_id
        )
        artifact = self.artifacts.get_tool_artifact_for_principal(
            source_ref, principal=principal
        )
        if artifact is None:
            raise JobServiceError("stable_url_artifact_unavailable")
        if artifact.get("expired"):
            raise JobServiceError("stable_url_artifact_expired")
        normalized = self.normalizer.normalize_artifact(artifact)
        if not normalized.is_complete:
            reasons = ",".join(normalized.completeness_reasons)
            raise JobServiceError(f"stable_url_incomplete:{reasons}")
        requested_url = str(artifact.get("requested_url") or url)
        created_at = artifact.get("created_at")
        fetched_at = (
            normalized.retrieved_at
            or (created_at if isinstance(created_at, datetime) else datetime.now(UTC))
        )
        expires_at = artifact.get("expires_at")
        return StableUrlResult(
            title=normalized.title,
            company=normalized.company,
            location=normalized.location or None,
            requested_url=requested_url,
            final_url=normalized.source_url,
            markdown=normalized.to_markdown(),
            source_content_sha256=normalized.source_content_sha256,
            artifact_ref=normalized.artifact_ref,
            fetched_at=fetched_at,
            expires_at=expires_at if isinstance(expires_at, datetime) else None,
        )


class SessionKnowledgeJobContentRepository(JobContentRepository):
    """Keep candidates temporary; publish confirmed snapshots to scoped Knowledge."""

    def __init__(
        self,
        artifacts: SQLiteSessionStore,
        knowledge: SQLiteKnowledgeStore,
        *,
        retention_days: int = 14,
        chunk_target_chars: int = 1200,
        chunk_overlap_chars: int = 150,
        max_chunks: int = 5000,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self.artifacts = artifacts
        self.knowledge = knowledge
        self.retention_days = retention_days
        self.clock = clock
        self.pipeline = KnowledgeIngestionPipeline(
            knowledge,
            target_chars=chunk_target_chars,
            overlap_chars=chunk_overlap_chars,
            max_chunks=max_chunks,
        )

    def write_candidate(
        self,
        *,
        candidate_id: str,
        filename: str,
        markdown: str,
        content_sha256: str,
        principal: str,
        workspace_id: str,
    ) -> CandidateContent:
        actual = sha256(markdown.encode()).hexdigest()
        if actual != content_sha256:
            raise JobServiceError("candidate_content_hash_mismatch")
        source_ref = f"artifact:job-candidate:{candidate_id}"
        namespace = uuid5(NAMESPACE_URL, f"job-candidate:{workspace_id}")
        now = self.clock()
        self.artifacts.save_tool_artifact(
            source_ref=source_ref,
            session_id=namespace,
            turn_id=uuid5(namespace, candidate_id),
            tool_name="job_candidate",
            content=markdown,
            call_id=candidate_id,
            content_sha256=actual,
            truncation_summary={
                "filename": filename,
                "workspace_id": workspace_id,
                "complete": True,
            },
            parent_run_id=f"local-job-candidate:{candidate_id}",
            access_level="restricted",
            principal=principal,
            expires_at=now + timedelta(days=self.retention_days),
        )
        return CandidateContent(artifact_ref=source_ref, content_sha256=actual)

    def read_candidate(
        self, artifact_ref: str, *, principal: str, workspace_id: str
    ) -> str:
        artifact = self.artifacts.get_tool_artifact_for_principal(
            artifact_ref, principal=principal
        )
        if artifact is None:
            raise JobServiceError("candidate_artifact_unavailable")
        if artifact.get("expired"):
            raise JobServiceError("candidate_artifact_expired")
        summary = artifact.get("truncation_summary") or {}
        if not isinstance(summary, dict) or summary.get("workspace_id") != workspace_id:
            raise JobServiceError("candidate_artifact_workspace_mismatch")
        content = str(artifact.get("content") or "")
        actual = sha256(content.encode()).hexdigest()
        if actual != artifact.get("content_sha256"):
            raise JobServiceError("candidate_artifact_hash_mismatch")
        return content

    def publish_snapshot(
        self,
        *,
        operation_id: str,
        filename: str,
        markdown: str,
        content_sha256: str,
        principal: str,
        workspace_id: str,
    ) -> PublishedJobContent:
        if sha256(markdown.encode()).hexdigest() != content_sha256:
            raise JobServiceError("job_snapshot_hash_mismatch")
        scope = KnowledgeScope(user_id=principal, project_id=workspace_id)
        knowledge_base_id = uuid5(
            NAMESPACE_URL, f"starter-agent:{principal}:{workspace_id}:cv-workbench"
        )
        self.knowledge.ensure_knowledge_base(
            scope, knowledge_base_id=knowledge_base_id, name="CV Workbench"
        )
        existing = self.knowledge.find_document_version_by_hash(
            scope, knowledge_base_id, content_sha256
        )
        if existing is not None:
            return PublishedJobContent(
                content_ref=ContentReference(
                    content_sha256=existing.content_sha256,
                    knowledge_base_id=str(existing.knowledge_base_id),
                    document_id=str(existing.document_id),
                    document_version_id=str(existing.id),
                )
            )
        upload = self.knowledge.create_upload(
            scope,
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            document_type="job_description",
            source_text=markdown,
            content_sha256=content_sha256,
        )
        try:
            self.pipeline.run(scope, upload)
        except Exception:
            self.knowledge.discard_upload(scope, upload)
            raise
        document = self.knowledge.get_document(
            scope, knowledge_base_id, upload.document.id
        )
        job = self.knowledge.get_job(scope, knowledge_base_id, upload.job.id)
        if (
            document is None
            or job is None
            or document.status != "indexed"
            or job.status != "succeeded"
            or document.active_version_id != upload.version.id
        ):
            raise KnowledgeError("document_ingestion_failed")
        return PublishedJobContent(
            content_ref=ContentReference(
                content_sha256=content_sha256,
                knowledge_base_id=str(knowledge_base_id),
                document_id=str(upload.document.id),
                document_version_id=str(upload.version.id),
            )
        )
