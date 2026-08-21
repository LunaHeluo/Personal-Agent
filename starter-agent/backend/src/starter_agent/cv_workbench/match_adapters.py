"""Persistent MatchAnalysis candidate adapter over the existing Artifact store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from starter_agent.cv_workbench.matching import (
    MatchCandidateEnvelope,
    MatchServiceError,
    StoredMatchCandidate,
)
from starter_agent.infrastructure.session_store import SQLiteSessionStore


class SessionMatchCandidateRepository:
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

    def write(
        self, candidate: MatchCandidateEnvelope, *, principal: str
    ) -> StoredMatchCandidate:
        source_ref = f"artifact:match-candidate:{candidate.analysis_id}"
        namespace = uuid5(
            NAMESPACE_URL, f"match-candidate:{candidate.workspace_id}"
        )
        now = self.clock()
        self.store.save_tool_artifact(
            source_ref=source_ref,
            session_id=namespace,
            turn_id=uuid5(namespace, candidate.analysis_id),
            tool_name="result_envelope",
            content=candidate.canonical_json,
            call_id=candidate.analysis_id,
            content_sha256=candidate.content_sha256,
            truncation_summary={
                "candidate_kind": "match_analysis",
                "workspace_id": candidate.workspace_id,
                "complete": True,
            },
            parent_run_id=candidate.parent_run_id,
            access_level="restricted",
            principal=principal,
            expires_at=now + timedelta(days=self.retention_days),
        )
        return StoredMatchCandidate(
            artifact_ref=source_ref,
            content_sha256=candidate.content_sha256,
        )

    def read(self, artifact_ref: str, *, principal: str) -> MatchCandidateEnvelope:
        artifact = self.store.get_tool_artifact_for_principal(
            artifact_ref, principal=principal
        )
        if artifact is None:
            raise MatchServiceError("match_candidate_unavailable")
        if artifact.get("expired"):
            raise MatchServiceError("match_candidate_expired")
        content = str(artifact.get("content") or "")
        digest = sha256(content.encode()).hexdigest()
        if digest != artifact.get("content_sha256"):
            raise MatchServiceError("match_candidate_artifact_hash_mismatch")
        candidate = MatchCandidateEnvelope.model_validate_json(content)
        if candidate.content_sha256 != digest:
            raise MatchServiceError("match_candidate_payload_hash_mismatch")
        return candidate
