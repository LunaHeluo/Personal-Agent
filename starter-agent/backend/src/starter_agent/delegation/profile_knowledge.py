from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from starter_agent.delegation.context import (
    ContextBuildError,
    ContextFragment,
    ContextReference,
    RuntimeContextAuthority,
)
from starter_agent.knowledge.models import KnowledgeScope


class ProfileKnowledgeBindings:
    """Explicit Profile-to-Knowledge binding; no implicit global scope fallback."""

    def __init__(self, knowledge) -> None:
        self.knowledge = knowledge

    def authority_values(self, claim) -> tuple[str, str, str]:
        scope = self._scope(claim)
        return scope.user_id, scope.project_id, str(self._knowledge_base_id(claim, scope))

    def references(self, claim) -> tuple[ContextReference, ...]:
        if claim.task.specialist_id != "profile_evidence_analyst":
            return ()
        scope = self._scope(claim)
        knowledge_base_id = self._knowledge_base_id(claim, scope)
        raw = claim.task.inputs_ref_json.get("candidate_chunk_ids", ())
        if not isinstance(raw, (list, tuple)):
            raise ContextBuildError("profile_knowledge_binding_unavailable", "candidate chunk references are invalid")
        try:
            requested = [UUID(item) for item in raw]
        except (TypeError, ValueError) as exc:
            raise ContextBuildError("profile_knowledge_binding_unavailable", "candidate chunk references are invalid") from exc
        loaded = self.knowledge.store.get_chunks_by_ids(scope, knowledge_base_id, requested)
        if len(loaded) != len(set(requested)):
            raise ContextBuildError("profile_knowledge_binding_unavailable", "candidate chunk is not authorized")
        return tuple(
            ContextReference(
                kind="knowledge_chunk",
                ref_id=f"knowledge-chunk:{chunk.id}",
                parent_run_id=claim.parent.id,
                principal=claim.parent.principal,
                child_task_id=claim.task.id,
                child_run_id=claim.run.id,
                knowledge_scope_type="resume",
                knowledge_user_id=scope.user_id,
                knowledge_project_id=scope.project_id,
                knowledge_base_id=str(knowledge_base_id),
                document_id=str(chunk.document_id),
                chunk_id=str(chunk.id),
                expires_at=claim.run.deadline_at,
            )
            for chunk, document_type in loaded.values()
            if document_type == "resume"
        )

    def load(self, reference: ContextReference, authority: RuntimeContextAuthority) -> ContextFragment:
        if reference.kind != "knowledge_chunk":
            raise ContextBuildError("context_reference_forbidden", "only knowledge chunks are supported")
        try:
            scope = KnowledgeScope(user_id=authority.knowledge_user_id or "", project_id=authority.knowledge_project_id or "")
            knowledge_base_id = UUID(authority.knowledge_base_id or "")
            chunk_id = UUID(reference.chunk_id or "")
        except ValueError as exc:
            raise ContextBuildError("profile_knowledge_binding_unavailable", "knowledge binding is invalid") from exc
        loaded = self.knowledge.store.get_chunks_by_ids(scope, knowledge_base_id, [chunk_id])
        item = loaded.get(chunk_id)
        if item is None or item[1] != "resume":
            raise ContextBuildError("context_reference_forbidden", "knowledge chunk is not authorized")
        chunk, _document_type = item
        if str(chunk.document_id) != reference.document_id:
            raise ContextBuildError("context_reference_forbidden", "knowledge document mismatch")
        return ContextFragment(
            kind="knowledge_chunk", ref_id=reference.ref_id, content=chunk.text,
            document_id=str(chunk.document_id), chunk_id=str(chunk.id),
            knowledge_user_id=chunk.user_id, knowledge_project_id=chunk.project_id,
            knowledge_base_id=str(chunk.knowledge_base_id), untrusted=True,
        )

    def _scope(self, claim) -> KnowledgeScope:
        if claim.task.specialist_id != "profile_evidence_analyst":
            raise ContextBuildError("profile_knowledge_binding_unavailable", "not a profile child")
        raw = claim.task.inputs_ref_json.get("knowledge_scope")
        if not isinstance(raw, dict) or raw.get("type") != "resume":
            raise ContextBuildError("profile_knowledge_binding_unavailable", "profile knowledge scope is required")
        user_id, project_id = raw.get("user_id"), raw.get("project_id")
        if not isinstance(user_id, str) or not isinstance(project_id, str) or user_id != claim.parent.principal:
            raise ContextBuildError("profile_knowledge_binding_unavailable", "profile principal binding is invalid")
        return KnowledgeScope(user_id=user_id, project_id=project_id)

    @staticmethod
    def _knowledge_base_id(claim, scope: KnowledgeScope) -> UUID:
        raw = claim.task.inputs_ref_json.get("knowledge_scope")
        try:
            return UUID(str(raw["knowledge_base_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ContextBuildError("profile_knowledge_binding_unavailable", "profile knowledge base binding is invalid") from exc
