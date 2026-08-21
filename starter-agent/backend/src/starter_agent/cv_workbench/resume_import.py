"""Recoverable UTF-8 Markdown/TXT resume import for the CV workbench."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from starter_agent.cv_workbench.bindings import (
    BindEvidenceCommand,
    EvidenceBindingService,
)
from starter_agent.cv_workbench.contracts import (
    CONTRACT_VERSION,
    ContentReference,
    OperationStatus,
    Resume,
    ResumeBranch,
    ResumeBranchType,
    ResumeNodeType,
    ResumeVersion,
    ResumeVersionStatus,
)
from starter_agent.cv_workbench.operations import (
    BusinessOperationService,
    CommitReceipt,
    OperationCommand,
    RunBinding,
    RunOutcome,
    SafetyDecision,
    ValidationDecision,
)
from starter_agent.cv_workbench.store import (
    ObjectNotFoundError,
    ResumeImportStaging,
    SQLiteWorkbenchStore,
)
from starter_agent.knowledge.security import validate_markdown_upload


PARSER_VERSION = "resume-markdown-v1"
ALLOWED_EXTENSIONS = (".md", ".markdown", ".txt")


class ResumeImportError(RuntimeError):
    code = "resume_import_failed"

    def __init__(
        self, message: str, *, operation_id: str, raw_artifact_ref: str | None = None
    ) -> None:
        super().__init__(message)
        self.operation_id = operation_id
        self.raw_artifact_ref = raw_artifact_ref


@dataclass(frozen=True)
class ResumeBlockProjection:
    block_id: str
    kind: str
    ordinal: int
    start_line: int
    end_line: int
    content_sha256: str


@dataclass(frozen=True)
class NormalizedResume:
    markdown: str
    content_sha256: str
    parser_version: str
    blocks: tuple[ResumeBlockProjection, ...]

    def projection(self) -> dict[str, object]:
        return {
            "parser_version": self.parser_version,
            "content_sha256": self.content_sha256,
            "blocks": [
                {
                    "block_id": block.block_id,
                    "kind": block.kind,
                    "ordinal": block.ordinal,
                    "start_line": block.start_line,
                    "end_line": block.end_line,
                    "content_sha256": block.content_sha256,
                }
                for block in self.blocks
            ],
        }


@dataclass(frozen=True)
class RawArtifact:
    source_ref: str
    content_sha256: str


class RawArtifactWriter(Protocol):
    def write_resume_source(
        self,
        *,
        operation_id: str,
        filename: str,
        content: bytes,
        principal: str,
        workspace_id: str,
    ) -> RawArtifact: ...


@dataclass(frozen=True)
class KnowledgeImportResult:
    knowledge_base_id: str
    document_id: str
    document_version_id: str
    content_sha256: str

    @property
    def source_ref(self) -> str:
        return (
            f"knowledge://{self.knowledge_base_id}/"
            f"{self.document_id}/{self.document_version_id}"
        )


class ResumeKnowledgeImporter(Protocol):
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
        """Ingest idempotently using operation_id and content_sha256."""


@dataclass(frozen=True)
class ResumeImportCommand:
    operation_id: str
    idempotency_key: str
    workspace_id: str
    resume_id: str
    branch_id: str
    version_id: str
    resume_name: str
    filename: str
    content: bytes
    confirmed_authorized: bool


@dataclass(frozen=True)
class ResumeImportResult:
    operation_id: str
    resume_id: str
    version_id: str
    normalized_sha256: str
    raw_artifact_ref: str
    reused: bool


class ResumeMarkdownNormalizer:
    def normalize(self, text: str) -> NormalizedResume:
        lines = [line.rstrip() for line in text.replace("\t", "    ").splitlines()]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        compact: list[str] = []
        blank = False
        for line in lines:
            if not line.strip():
                if not blank:
                    compact.append("")
                blank = True
            else:
                compact.append(line)
                blank = False
        if not compact or not any(line.strip() for line in compact):
            raise ValueError("resume_content_empty")
        markdown = "\n".join(compact) + "\n"
        blocks = self._blocks(compact)
        return NormalizedResume(
            markdown=markdown,
            content_sha256=sha256(markdown.encode("utf-8")).hexdigest(),
            parser_version=PARSER_VERSION,
            blocks=blocks,
        )

    @staticmethod
    def _blocks(lines: list[str]) -> tuple[ResumeBlockProjection, ...]:
        ranges: list[tuple[str, int, int, str]] = []
        start: int | None = None
        kind = "paragraph"
        content: list[str] = []

        def flush(end: int) -> None:
            nonlocal start, kind, content
            if start is not None and content:
                ranges.append((kind, start, end, "\n".join(content)))
            start = None
            kind = "paragraph"
            content = []

        for index, line in enumerate(lines, start=1):
            if not line:
                flush(index - 1)
                continue
            next_kind = (
                "heading"
                if re.match(r"^#{1,6}\s+", line)
                else "list"
                if re.match(r"^\s*(?:[-*+] |\d+[.)] )", line)
                else "paragraph"
            )
            if start is None:
                start, kind, content = index, next_kind, [line]
            elif next_kind != kind or next_kind == "heading":
                flush(index - 1)
                start, kind, content = index, next_kind, [line]
            else:
                content.append(line)
        flush(len(lines))

        projections: list[ResumeBlockProjection] = []
        for ordinal, (block_kind, start_line, end_line, value) in enumerate(
            ranges, start=1
        ):
            content_hash = sha256(value.encode("utf-8")).hexdigest()
            block_hash = sha256(
                f"resume-block-v1\0{block_kind}\0{ordinal}".encode()
            ).hexdigest()
            projections.append(
                ResumeBlockProjection(
                    block_id=f"blk_{block_hash[:24]}",
                    kind=block_kind,
                    ordinal=ordinal,
                    start_line=start_line,
                    end_line=end_line,
                    content_sha256=content_hash,
                )
            )
        return tuple(projections)


class ResumeImportService:
    def __init__(
        self,
        *,
        store: SQLiteWorkbenchStore,
        artifact_writer: RawArtifactWriter,
        knowledge_importer: ResumeKnowledgeImporter,
        evidence_bindings: EvidenceBindingService,
        max_bytes: int = 2_000_000,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.artifact_writer = artifact_writer
        self.knowledge_importer = knowledge_importer
        self.evidence_bindings = evidence_bindings
        self.max_bytes = max_bytes
        self.clock = clock
        self.normalizer = ResumeMarkdownNormalizer()

    def import_resume(
        self, command: ResumeImportCommand, *, principal: str
    ) -> ResumeImportResult:
        self._validate_filename(command.filename)
        raw_sha256 = sha256(command.content).hexdigest()
        operation_service = self._operation_service(principal)
        operation, created = operation_service.create(
            OperationCommand(
                operation_id=command.operation_id,
                workspace_id=command.workspace_id,
                operation_type="import_resume",
                idempotency_key=command.idempotency_key,
                input_sha256=raw_sha256,
            ),
            principal=principal,
        )
        if operation.status == OperationStatus.COMMITTED:
            staging = self._require_staging(command.operation_id, principal)
            return self._result(staging, reused=True)
        if operation.status == OperationStatus.COMMIT_FAILED:
            committed = operation_service.retry_commit(
                operation.operation_id, principal=principal
            )
            if committed.status != OperationStatus.COMMITTED:
                raise ResumeImportError(
                    committed.error_code or "resume_import_commit_failed",
                    operation_id=operation.operation_id,
                )
            return self._result(
                self._require_staging(operation.operation_id, principal), reused=True
            )
        run_id = f"local-import:{operation.operation_id}"
        if operation.status == OperationStatus.CREATED:
            operation_service.bind_run(
                operation.operation_id,
                RunBinding(parent_run_id=run_id, task_id=None),
                principal=principal,
            )
        raw: RawArtifact | None = None
        try:
            staging = self.store.get_resume_import_staging(
                operation.operation_id, principal=principal
            )
            if staging is None:
                validated = validate_markdown_upload(
                    filename=command.filename,
                    content=command.content,
                    confirmed_authorized=command.confirmed_authorized,
                    max_bytes=self.max_bytes,
                    allowed_extensions=list(ALLOWED_EXTENSIONS),
                )
                raw = self.artifact_writer.write_resume_source(
                    operation_id=operation.operation_id,
                    filename=command.filename,
                    content=command.content,
                    principal=principal,
                    workspace_id=command.workspace_id,
                )
                if raw.content_sha256 != raw_sha256:
                    raise ValueError("raw_artifact_hash_mismatch")
                normalized = self.normalizer.normalize(validated.text)
                knowledge = self.knowledge_importer.ingest_resume(
                    operation_id=operation.operation_id,
                    filename=self._knowledge_filename(command.filename),
                    normalized_markdown=normalized.markdown,
                    content_sha256=normalized.content_sha256,
                    principal=principal,
                    workspace_id=command.workspace_id,
                )
                if knowledge.content_sha256 != normalized.content_sha256:
                    raise ValueError("knowledge_content_hash_mismatch")
                staging = self.store.save_resume_import_staging(
                    ResumeImportStaging(
                        operation_id=operation.operation_id,
                        resume_id=command.resume_id,
                        branch_id=command.branch_id,
                        version_id=command.version_id,
                        resume_name=command.resume_name,
                        normalized_sha256=normalized.content_sha256,
                        raw_artifact_ref=raw.source_ref,
                        raw_sha256=raw.content_sha256,
                        knowledge_base_id=knowledge.knowledge_base_id,
                        document_id=knowledge.document_id,
                        document_version_id=knowledge.document_version_id,
                        parser_version=normalized.parser_version,
                        projection=normalized.projection(),
                        created_at=self.clock(),
                    ),
                    principal=principal,
                )
            outcome = operation_service.process_run_outcome(
                operation.operation_id,
                RunOutcome(
                    parent_run_id=run_id,
                    status="succeeded",
                    result_ref=self._knowledge_ref(staging),
                    result_sha256=staging.normalized_sha256,
                ),
                principal=principal,
            )
            if outcome.status != OperationStatus.COMMITTED:
                raise ResumeImportError(
                    outcome.error_code or "resume_import_commit_failed",
                    operation_id=operation.operation_id,
                    raw_artifact_ref=staging.raw_artifact_ref,
                )
            return self._result(staging, reused=not created)
        except ResumeImportError:
            raise
        except Exception as exc:
            current = operation_service.get(operation.operation_id, principal=principal)
            if current.status == OperationStatus.RUNNING:
                operation_service.process_run_outcome(
                    operation.operation_id,
                    RunOutcome(
                        parent_run_id=run_id,
                        status="failed",
                        error_code=getattr(exc, "code", "resume_import_failed"),
                    ),
                    principal=principal,
                )
            raise ResumeImportError(
                str(getattr(exc, "code", "resume_import_failed")),
                operation_id=operation.operation_id,
                raw_artifact_ref=None if raw is None else raw.source_ref,
            ) from exc

    def _operation_service(self, principal: str) -> BusinessOperationService:
        return BusinessOperationService(
            store=self.store,
            validator=_ImportValidator(self.store, principal),
            safety_gate=_ImportSafetyGate(),
            committer=_ImportCommitter(
                store=self.store,
                bindings=self.evidence_bindings,
                clock=self.clock,
                principal=principal,
            ),
            clock=self.clock,
        )

    def _require_staging(
        self, operation_id: str, principal: str
    ) -> ResumeImportStaging:
        staging = self.store.get_resume_import_staging(
            operation_id, principal=principal
        )
        if staging is None:
            raise ResumeImportError(
                "resume_import_staging_missing", operation_id=operation_id
            )
        return staging

    @staticmethod
    def _result(staging: ResumeImportStaging, *, reused: bool) -> ResumeImportResult:
        return ResumeImportResult(
            operation_id=staging.operation_id,
            resume_id=staging.resume_id,
            version_id=staging.version_id,
            normalized_sha256=staging.normalized_sha256,
            raw_artifact_ref=staging.raw_artifact_ref,
            reused=reused,
        )

    @staticmethod
    def _validate_filename(filename: str) -> None:
        if Path(filename).name != filename or Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ValueError("unsupported_document_type")

    @staticmethod
    def _knowledge_filename(filename: str) -> str:
        return f"{Path(filename).stem}.normalized.md"

    @staticmethod
    def _knowledge_ref(staging: ResumeImportStaging) -> str:
        return (
            f"knowledge://{staging.knowledge_base_id}/{staging.document_id}/"
            f"{staging.document_version_id}"
        )


class _ImportValidator:
    def __init__(self, store: SQLiteWorkbenchStore, principal: str) -> None:
        self.store = store
        self.principal = principal

    def validate(self, operation, outcome) -> ValidationDecision:
        staging = self.store.get_resume_import_staging(
            operation.operation_id, principal=self.principal
        )
        accepted = bool(
            staging is not None
            and outcome.result_ref == ResumeImportService._knowledge_ref(staging)
            and outcome.result_sha256 == staging.normalized_sha256
            and staging.projection.get("parser_version") == staging.parser_version
        )
        return ValidationDecision(
            accepted=accepted,
            validator_version=PARSER_VERSION,
            result_ref=outcome.result_ref,
            result_sha256=outcome.result_sha256,
            evidence_refs=() if staging is None else (staging.raw_artifact_ref,),
            error_code=None if accepted else "resume_import_validation_failed",
        )

class _ImportSafetyGate:
    def evaluate(self, operation, decision) -> SafetyDecision:
        return SafetyDecision(
            allowed=decision.accepted,
            summary={"parser_version": PARSER_VERSION, "human_confirmation_required": False},
            error_code=None if decision.accepted else "resume_import_validation_failed",
        )


class _ImportCommitter:
    def __init__(self, *, store, bindings, clock, principal: str) -> None:
        self.store = store
        self.bindings = bindings
        self.clock = clock
        self.principal = principal

    def commit(self, operation, checkpoint) -> CommitReceipt:
        principal = self.principal
        staging = self.store.get_resume_import_staging(
            operation.operation_id, principal=principal
        )
        if staging is None:
            raise ResumeImportError(
                "resume_import_staging_missing", operation_id=operation.operation_id
            )
        try:
            self.store.get(ResumeVersion, staging.version_id, principal=principal)
            version_exists = True
        except ObjectNotFoundError:
            version_exists = False
        now = self.clock()
        try:
            resume = self.store.get(Resume, staging.resume_id, principal=principal)
        except ObjectNotFoundError:
            resume = Resume.model_validate(
                {
                    "contract_version": CONTRACT_VERSION,
                    "resume_id": staging.resume_id,
                    "owner_id": principal,
                    "name": staging.resume_name,
                    "status": "active",
                    "latest_version_id": None,
                    "revision": 1,
                    "created_at": now,
                    "updated_at": now,
                    "allowed_actions": ("create_branch", "archive"),
                }
            )
            self.store.create(resume, principal=principal)
        try:
            self.store.get(ResumeBranch, staging.branch_id, principal=principal)
        except ObjectNotFoundError:
            self.store.create(
                ResumeBranch.model_validate(
                    {
                        "contract_version": CONTRACT_VERSION,
                        "branch_id": staging.branch_id,
                        "resume_id": staging.resume_id,
                        "name": "master",
                        "branch_type": ResumeBranchType.MASTER,
                        "base_version_id": staging.version_id,
                        "job_snapshot_id": None,
                        "archived": False,
                        "revision": 1,
                        "created_at": now,
                        "updated_at": now,
                        "allowed_actions": ("create_version",),
                    }
                ),
                principal=principal,
            )
        if not version_exists:
            version = ResumeVersion.model_validate(
                {
                "contract_version": CONTRACT_VERSION,
                "version_id": staging.version_id,
                "resume_id": staging.resume_id,
                "branch_id": staging.branch_id,
                "parent_version_id": None,
                "branch_base_version_id": staging.version_id,
                "node_type": ResumeNodeType.BASE,
                "version_number": 1,
                "label": f"{staging.resume_name} v1",
                "content": ContentReference(
                    content_sha256=staging.normalized_sha256,
                    knowledge_base_id=staging.knowledge_base_id,
                    document_id=staging.document_id,
                    document_version_id=staging.document_version_id,
                ),
                "status": ResumeVersionStatus.CONFIRMED,
                "job_snapshot_id": None,
                "upstream_changes_available": False,
                "revision": 1,
                "created_by": principal,
                "created_at": now,
                "confirmed_at": now,
                "allowed_actions": ("open_in_workbench", "compare"),
                }
            )
            self.store.create(version, principal=principal)
        self.store.link_to_workspace(
            operation.workspace_id, staging.resume_id, principal=principal
        )
        current_resume = self.store.get(Resume, staging.resume_id, principal=principal)
        if current_resume.latest_version_id is None:
            self.store.update(
                Resume.model_validate(
                    current_resume.model_dump()
                    | {
                        "latest_version_id": staging.version_id,
                        "revision": current_resume.revision + 1,
                        "updated_at": now,
                    }
                ),
                principal=principal,
                expected_revision=current_resume.revision,
            )
        self.bindings.bind(
            BindEvidenceCommand(
                workspace_id=operation.workspace_id,
                subject_id=staging.version_id,
                source_kind="document_version",
                source_ref=ResumeImportService._knowledge_ref(staging),
                expected_sha256=staging.normalized_sha256,
            ),
            principal=principal,
        )
        self.bindings.bind(
            BindEvidenceCommand(
                workspace_id=operation.workspace_id,
                subject_id=staging.version_id,
                source_kind="artifact",
                source_ref=staging.raw_artifact_ref,
                expected_sha256=staging.raw_sha256,
            ),
            principal=principal,
        )
        return CommitReceipt(result_object_id=staging.version_id)
