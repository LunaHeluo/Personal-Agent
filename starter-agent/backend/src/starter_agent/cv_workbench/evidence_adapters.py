"""Read-only adapters over existing Knowledge, Artifact and Run stores."""

from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from starter_agent.cv_workbench.bindings import EvidenceSourceSnapshot
from starter_agent.delegation.store import SQLiteRunStore
from starter_agent.infrastructure.session_store import SQLiteSessionStore
from starter_agent.knowledge.models import KnowledgeScope
from starter_agent.knowledge.store import SQLiteKnowledgeStore


class KnowledgeEvidenceReader:
    def __init__(self, store: SQLiteKnowledgeStore) -> None:
        self.store = store

    def inspect(
        self,
        source_ref: str,
        *,
        principal: str,
        workspace_id: str,
        now: datetime,
    ) -> EvidenceSourceSnapshot:
        parsed = urlsplit(source_ref)
        scope = KnowledgeScope(user_id=principal, project_id=workspace_id)
        try:
            if parsed.scheme == "knowledge":
                document_id, version_id = self._parts(parsed, 2)
                version = self.store.get_document_version(
                    scope,
                    UUID(parsed.netloc),
                    UUID(document_id),
                    UUID(version_id),
                )
                if version is None:
                    return EvidenceSourceSnapshot(exists=False, authorized=True)
                return EvidenceSourceSnapshot(
                    exists=True,
                    authorized=True,
                    content_sha256=version.content_sha256,
                    metadata={
                        "source_type": "document_version",
                        "document_version_id": str(version.id),
                        "created_at": version.created_at.isoformat(),
                    },
                )
            if parsed.scheme == "knowledge-chunk":
                (chunk_id,) = self._parts(parsed, 1)
                chunk = self.store.get_chunk(
                    scope, UUID(parsed.netloc), UUID(chunk_id)
                )
                if chunk is None:
                    return EvidenceSourceSnapshot(exists=False, authorized=True)
                return EvidenceSourceSnapshot(
                    exists=True,
                    authorized=True,
                    content_sha256=chunk.content_sha256,
                    metadata={
                        "source_type": "chunk",
                        "chunk_id": str(chunk.id),
                        "filename": chunk.filename,
                        "created_at": chunk.created_at.isoformat(),
                    },
                )
        except (ValueError, TypeError):
            return EvidenceSourceSnapshot(exists=False, authorized=True)
        return EvidenceSourceSnapshot(exists=False, authorized=True)

    @staticmethod
    def _parts(parsed, count: int) -> tuple[str, ...]:
        values = tuple(value for value in parsed.path.split("/") if value)
        if not parsed.netloc or len(values) != count:
            raise ValueError("invalid_knowledge_reference")
        return values


class ArtifactEvidenceReader:
    def __init__(self, store: SQLiteSessionStore) -> None:
        self.store = store

    def inspect(
        self,
        source_ref: str,
        *,
        principal: str,
        workspace_id: str,
        now: datetime,
    ) -> EvidenceSourceSnapshot:
        internal = self.store.get_tool_artifact(source_ref)
        if internal is None:
            return EvidenceSourceSnapshot(exists=False, authorized=True)
        authorized = self.store.get_tool_artifact_for_principal(
            source_ref, principal=principal, now=now
        )
        if authorized is None:
            return EvidenceSourceSnapshot(exists=True, authorized=False)
        return EvidenceSourceSnapshot(
            exists=True,
            authorized=True,
            expired=bool(authorized.get("expired", False)),
            content_sha256=str(authorized.get("content_sha256") or "") or None,
            metadata={
                "source_type": "artifact",
                "tool_name": authorized.get("tool_name"),
                "created_at": self._iso(authorized.get("created_at")),
                "expires_at": self._iso(authorized.get("expires_at")),
            },
        )

    @staticmethod
    def _iso(value):
        return value.isoformat() if isinstance(value, datetime) else value


class RunEvidenceReader:
    def __init__(
        self,
        store: SQLiteRunStore,
        *,
        expires_after: timedelta | None = None,
        trace: bool = False,
    ) -> None:
        self.store = store
        self.expires_after = expires_after
        self.trace = trace

    def inspect(
        self,
        source_ref: str,
        *,
        principal: str,
        workspace_id: str,
        now: datetime,
    ) -> EvidenceSourceSnapshot:
        parent = self.store.get_parent(source_ref)
        if parent is None:
            return EvidenceSourceSnapshot(exists=False, authorized=True)
        if parent.principal != principal:
            return EvidenceSourceSnapshot(exists=True, authorized=False)
        terminal_time = parent.completed_at or parent.updated_at
        expired = bool(
            self.expires_after is not None
            and terminal_time + self.expires_after <= now
        )
        metadata = {
            "source_type": "trace" if self.trace else "run",
            "run_status": parent.status,
            "created_at": parent.created_at.isoformat(),
        }
        if self.trace:
            event_count = 0
            after_seq = 0
            while True:
                page = self.store.list_events(
                    parent.id, after_seq=after_seq, limit=500
                )
                event_count += len(page.items)
                if page.next_cursor is None:
                    break
                after_seq = int(page.next_cursor)
            metadata["event_count"] = event_count
        return EvidenceSourceSnapshot(
            exists=True,
            authorized=True,
            expired=expired,
            metadata=metadata,
        )
