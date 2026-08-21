"""Authorized, redaction-safe evidence bindings for workbench objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal, Protocol

from starter_agent.cv_workbench.store import (
    SQLiteWorkbenchStore,
    StoredEvidenceBinding,
)


SourceKind = Literal["document_version", "chunk", "artifact", "run", "trace"]
BindingStatus = Literal[
    "traceable", "expired", "missing", "forbidden", "hash_mismatch"
]


class EvidenceBindingError(RuntimeError):
    code = "evidence_binding_error"


class EvidenceBindingRejected(EvidenceBindingError):
    code = "evidence_binding_rejected"


@dataclass(frozen=True)
class EvidenceSourceSnapshot:
    exists: bool
    authorized: bool
    expired: bool = False
    content_sha256: str | None = None
    metadata: dict[str, Any] | None = None


class EvidenceSourceReader(Protocol):
    def inspect(
        self,
        source_ref: str,
        *,
        principal: str,
        workspace_id: str,
        now: datetime,
    ) -> EvidenceSourceSnapshot: ...


@dataclass(frozen=True)
class BindEvidenceCommand:
    workspace_id: str
    subject_id: str
    source_kind: SourceKind
    source_ref: str
    expected_sha256: str | None = None


@dataclass(frozen=True)
class EvidenceSummary:
    binding_id: str
    subject_id: str
    source_kind: str
    status: str
    traceable: bool
    label: str
    metadata: dict[str, Any]
    detail_route: str | None
    checked_at: datetime


@dataclass(frozen=True)
class EvidenceAudit:
    subject_id: str
    total: int
    traceable: int
    broken: int
    statuses: dict[str, int]


SAFE_METADATA_KEYS = frozenset(
    {
        "title",
        "filename",
        "tool_name",
        "run_status",
        "event_count",
        "chunk_id",
        "document_version_id",
        "created_at",
        "expires_at",
        "source_type",
    }
)


class EvidenceBindingService:
    def __init__(
        self,
        *,
        store: SQLiteWorkbenchStore,
        readers: dict[SourceKind, EvidenceSourceReader],
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.readers = readers
        self.clock = clock

    def bind(
        self, command: BindEvidenceCommand, *, principal: str
    ) -> EvidenceSummary:
        now = self.clock()
        snapshot = self._inspect(command, principal=principal, now=now)
        status = self._status(snapshot, command.expected_sha256)
        if status != "traceable":
            raise EvidenceBindingRejected(status)
        binding_id = self._binding_id(command)
        stored = self.store.save_evidence_binding(
            binding_id=binding_id,
            workspace_id=command.workspace_id,
            subject_id=command.subject_id,
            source_kind=command.source_kind,
            source_ref=command.source_ref,
            expected_sha256=command.expected_sha256,
            status=status,
            safe_summary=self._safe_metadata(snapshot.metadata),
            principal=principal,
            checked_at=now,
        )
        return self._summary(stored)

    def refresh_subject(
        self, subject_id: str, *, principal: str
    ) -> tuple[EvidenceSummary, ...]:
        refreshed: list[EvidenceSummary] = []
        for binding in self.store.list_evidence_bindings(
            subject_id, principal=principal
        ):
            reader = self.readers.get(binding.source_kind)  # type: ignore[arg-type]
            now = self.clock()
            if reader is None:
                snapshot = EvidenceSourceSnapshot(exists=False, authorized=True)
            else:
                snapshot = reader.inspect(
                    binding.source_ref,
                    principal=principal,
                    workspace_id=binding.workspace_id,
                    now=now,
                )
            status = self._status(snapshot, binding.expected_sha256)
            stored = self.store.save_evidence_binding(
                binding_id=binding.binding_id,
                workspace_id=binding.workspace_id,
                subject_id=binding.subject_id,
                source_kind=binding.source_kind,
                source_ref=binding.source_ref,
                expected_sha256=binding.expected_sha256,
                status=status,
                safe_summary=self._safe_metadata(snapshot.metadata),
                principal=principal,
                checked_at=now,
            )
            refreshed.append(self._summary(stored))
        return tuple(refreshed)

    def summaries(
        self, subject_id: str, *, principal: str
    ) -> tuple[EvidenceSummary, ...]:
        return tuple(
            self._summary(binding)
            for binding in self.store.list_evidence_bindings(
                subject_id, principal=principal
            )
        )

    def audit(self, subject_id: str, *, principal: str) -> EvidenceAudit:
        summaries = self.summaries(subject_id, principal=principal)
        statuses: dict[str, int] = {}
        for summary in summaries:
            statuses[summary.status] = statuses.get(summary.status, 0) + 1
        traceable = statuses.get("traceable", 0)
        return EvidenceAudit(
            subject_id=subject_id,
            total=len(summaries),
            traceable=traceable,
            broken=len(summaries) - traceable,
            statuses=statuses,
        )

    def _inspect(
        self,
        command: BindEvidenceCommand,
        *,
        principal: str,
        now: datetime,
    ) -> EvidenceSourceSnapshot:
        self.store.assert_entity_in_workspace(
            command.subject_id, command.workspace_id, principal=principal
        )
        reader = self.readers.get(command.source_kind)
        if reader is None:
            raise EvidenceBindingRejected("source_reader_unavailable")
        return reader.inspect(
            command.source_ref,
            principal=principal,
            workspace_id=command.workspace_id,
            now=now,
        )

    @staticmethod
    def _status(
        snapshot: EvidenceSourceSnapshot, expected_sha256: str | None
    ) -> BindingStatus:
        if not snapshot.authorized:
            return "forbidden"
        if not snapshot.exists:
            return "missing"
        if snapshot.expired:
            return "expired"
        if (
            expected_sha256 is not None
            and snapshot.content_sha256 != expected_sha256
        ):
            return "hash_mismatch"
        return "traceable"

    @staticmethod
    def _binding_id(command: BindEvidenceCommand) -> str:
        digest = sha256(
            "\0".join(
                (
                    command.workspace_id,
                    command.subject_id,
                    command.source_kind,
                    command.source_ref,
                )
            ).encode()
        ).hexdigest()
        return f"eb_{digest[:32]}"

    @staticmethod
    def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
        return {
            key: value
            for key, value in (metadata or {}).items()
            if key in SAFE_METADATA_KEYS
            and isinstance(value, (str, int, float, bool, type(None)))
        }

    @staticmethod
    def _summary(binding: StoredEvidenceBinding) -> EvidenceSummary:
        traceable = binding.status == "traceable"
        label = str(
            binding.safe_summary.get("title")
            or binding.safe_summary.get("filename")
            or binding.safe_summary.get("tool_name")
            or binding.source_kind.replace("_", " ")
        )
        return EvidenceSummary(
            binding_id=binding.binding_id,
            subject_id=binding.subject_id,
            source_kind=binding.source_kind,
            status=binding.status,
            traceable=traceable,
            label=label,
            metadata=binding.safe_summary,
            detail_route=(
                f"/workbench/evidence/{binding.binding_id}/advanced"
                if traceable
                else None
            ),
            checked_at=binding.checked_at,
        )
