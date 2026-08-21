"""Manual/stable-URL job candidates and immutable snapshot promotion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from starter_agent.cv_workbench.bindings import BindEvidenceCommand, EvidenceBindingService
from starter_agent.cv_workbench.contracts import (
    ContentReference,
    Job,
    JobCandidate,
    JobSnapshot,
    JobUserStatus,
    OperationStatus,
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
from starter_agent.cv_workbench.store import ObjectNotFoundError, SQLiteWorkbenchStore
from starter_agent.knowledge.security import validate_markdown_upload


SENSITIVE_QUERY_KEYS = frozenset(
    {"access_token", "api_key", "apikey", "auth", "authorization", "cookie", "password", "secret", "token"}
)
TRACKING_QUERY_PREFIXES = ("utm_",)


class JobServiceError(RuntimeError):
    code = "job_service_error"


@dataclass(frozen=True)
class CandidateContent:
    artifact_ref: str
    content_sha256: str


@dataclass(frozen=True)
class PublishedJobContent:
    content_ref: ContentReference
    artifact_ref: str | None = None


class JobContentRepository(Protocol):
    def write_candidate(
        self,
        *,
        candidate_id: str,
        filename: str,
        markdown: str,
        content_sha256: str,
        principal: str,
        workspace_id: str,
    ) -> CandidateContent: ...

    def read_candidate(
        self, artifact_ref: str, *, principal: str, workspace_id: str
    ) -> str: ...

    def publish_snapshot(
        self,
        *,
        operation_id: str,
        filename: str,
        markdown: str,
        content_sha256: str,
        principal: str,
        workspace_id: str,
    ) -> PublishedJobContent: ...


@dataclass(frozen=True)
class StableUrlResult:
    title: str
    company: str
    location: str | None
    requested_url: str
    final_url: str
    markdown: str
    source_content_sha256: str
    artifact_ref: str
    fetched_at: datetime
    expires_at: datetime | None = None


class StableUrlFetcher(Protocol):
    def fetch(
        self, url: str, *, principal: str, workspace_id: str
    ) -> StableUrlResult:
        """Fetch one URL through the existing network/tool safety gate."""


@dataclass(frozen=True)
class CandidateCommand:
    candidate_id: str
    workspace_id: str
    title: str
    company: str
    location: str | None
    filename: str
    content: bytes
    confirmed_authorized: bool


@dataclass(frozen=True)
class JobPromotion:
    operation_id: str
    candidate_id: str
    job_id: str
    snapshot_id: str
    reused: bool
    conflict_snapshot_ids: tuple[str, ...]


@dataclass(frozen=True)
class SnapshotSourceHealth:
    snapshot_id: str
    source_status: str
    risk_flags: tuple[str, ...]
    last_legal_content_sha256: str


class JobService:
    def __init__(
        self,
        *,
        store: SQLiteWorkbenchStore,
        content: JobContentRepository,
        evidence: EvidenceBindingService,
        url_fetcher: StableUrlFetcher | None = None,
        max_bytes: int = 2_000_000,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.content = content
        self.evidence = evidence
        self.url_fetcher = url_fetcher
        self.max_bytes = max_bytes
        self.clock = clock

    def create_text_candidate(
        self, command: CandidateCommand, *, principal: str
    ) -> JobCandidate:
        validated = validate_markdown_upload(
            filename=command.filename,
            content=command.content,
            confirmed_authorized=command.confirmed_authorized,
            max_bytes=self.max_bytes,
            allowed_extensions=[".md", ".markdown", ".txt"],
        )
        markdown = self._normalize(validated.text)
        digest = sha256(markdown.encode()).hexdigest()
        stored = self.content.write_candidate(
            candidate_id=command.candidate_id,
            filename=command.filename,
            markdown=markdown,
            content_sha256=digest,
            principal=principal,
            workspace_id=command.workspace_id,
        )
        if stored.content_sha256 != digest:
            raise JobServiceError("candidate_artifact_hash_mismatch")
        candidate = JobCandidate.model_validate(
            {
                "candidate_id": command.candidate_id,
                "title": command.title,
                "company": command.company,
                "location": command.location,
                "source_kind": "text",
                "source_url": None,
                "final_url": None,
                "content_sha256": digest,
                "content": {"content_sha256": digest, "artifact_id": stored.artifact_ref},
                "source_artifact_ref": None,
                "source_content_sha256": None,
                "verified": False,
                "risk_flags": (),
                "created_at": self.clock(),
                "expires_at": None,
                "candidate_only": True,
            }
        )
        return self.store.create(
            candidate, principal=principal, workspace_id=command.workspace_id
        )

    def create_url_candidate(
        self,
        *,
        candidate_id: str,
        workspace_id: str,
        url: str,
        principal: str,
    ) -> JobCandidate:
        if self.url_fetcher is None:
            raise JobServiceError("stable_url_fetcher_unavailable")
        canonical = canonical_job_url(url)
        fetched = self.url_fetcher.fetch(
            canonical, principal=principal, workspace_id=workspace_id
        )
        final_url = canonical_job_url(fetched.final_url)
        markdown = self._normalize(fetched.markdown)
        digest = sha256(markdown.encode()).hexdigest()
        if len(fetched.source_content_sha256) != 64:
            raise JobServiceError("fetched_source_hash_invalid")
        stored = self.content.write_candidate(
            candidate_id=candidate_id,
            filename=f"{Path(candidate_id).name}.normalized.md",
            markdown=markdown,
            content_sha256=digest,
            principal=principal,
            workspace_id=workspace_id,
        )
        if stored.content_sha256 != digest:
            raise JobServiceError("candidate_artifact_hash_mismatch")
        candidate = JobCandidate.model_validate(
            {
                "candidate_id": candidate_id,
                "title": fetched.title,
                "company": fetched.company,
                "location": fetched.location,
                "source_kind": "stable_url",
                "source_url": canonical,
                "final_url": final_url,
                "content_sha256": digest,
                "content": {"content_sha256": digest, "artifact_id": stored.artifact_ref},
                "source_artifact_ref": fetched.artifact_ref,
                "source_content_sha256": fetched.source_content_sha256,
                "verified": True,
                "risk_flags": (),
                "created_at": fetched.fetched_at,
                "expires_at": fetched.expires_at,
                "candidate_only": True,
            }
        )
        return self.store.create(
            candidate, principal=principal, workspace_id=workspace_id
        )

    def create_research_candidate(
        self,
        *,
        candidate_id: str,
        workspace_id: str,
        title: str,
        company: str,
        location: str | None,
        markdown: str,
        source_url: str,
        final_url: str,
        source_artifact_ref: str,
        source_content_sha256: str,
        verified: bool,
        principal: str,
    ) -> JobCandidate:
        """Create a candidate only from a server-owned delegated result envelope."""
        normalized = self._normalize(markdown)
        digest = sha256(normalized.encode()).hexdigest()
        stored = self.content.write_candidate(
            candidate_id=candidate_id,
            filename=f"{Path(candidate_id).name}.research.md",
            markdown=normalized,
            content_sha256=digest,
            principal=principal,
            workspace_id=workspace_id,
        )
        if stored.content_sha256 != digest or len(source_content_sha256) != 64:
            raise JobServiceError("research_candidate_hash_invalid")
        candidate = JobCandidate(
            candidate_id=candidate_id,
            title=title,
            company=company,
            location=location,
            source_kind="research",
            source_url=canonical_job_url(source_url),
            final_url=canonical_job_url(final_url),
            content_sha256=digest,
            content=ContentReference(content_sha256=digest, artifact_id=stored.artifact_ref),
            source_artifact_ref=source_artifact_ref,
            source_content_sha256=source_content_sha256,
            verified=verified,
            risk_flags=() if verified else ("partial_verified",),
            created_at=self.clock(),
            expires_at=None,
        )
        return self.store.create(
            candidate, principal=principal, workspace_id=workspace_id
        )

    def confirm_candidate(
        self,
        candidate_id: str,
        *,
        workspace_id: str,
        operation_id: str,
        idempotency_key: str,
        principal: str,
    ) -> JobPromotion:
        candidate = self.store.get(JobCandidate, candidate_id, principal=principal)
        self.store.assert_entity_in_workspace(
            candidate_id, workspace_id, principal=principal
        )
        if candidate.content is None or candidate.content_sha256 is None:
            raise JobServiceError("candidate_content_missing")
        operation_service = BusinessOperationService(
            store=self.store,
            validator=_CandidateValidator(candidate),
            safety_gate=_CandidateSafetyGate(),
            committer=_CandidateCommitter(self, principal=principal),
            clock=self.clock,
        )
        operation, created = operation_service.create(
            OperationCommand(
                operation_id=operation_id,
                workspace_id=workspace_id,
                operation_type="confirm_job_candidate",
                idempotency_key=idempotency_key,
                input_sha256=candidate.content_sha256,
            ),
            principal=principal,
        )
        if operation.status == OperationStatus.COMMITTED:
            return self._promotion(candidate_id, operation_id, principal, reused=True)
        if operation.status == OperationStatus.COMMIT_FAILED:
            operation = operation_service.retry_commit(operation_id, principal=principal)
        else:
            run_id = f"local-job-confirm:{operation_id}"
            if operation.status == OperationStatus.CREATED:
                operation_service.bind_run(
                    operation_id,
                    RunBinding(parent_run_id=run_id),
                    principal=principal,
                )
            operation = operation_service.process_run_outcome(
                operation_id,
                RunOutcome(
                    parent_run_id=run_id,
                    status="succeeded",
                    result_ref=f"candidate://{candidate_id}",
                    result_sha256=candidate.content_sha256,
                ),
                principal=principal,
            )
        if operation.status != OperationStatus.COMMITTED:
            raise JobServiceError(operation.error_code or "candidate_commit_failed")
        return self._promotion(candidate_id, operation_id, principal, reused=not created)

    def source_health(
        self, snapshot_id: str, *, principal: str, available: bool
    ) -> SnapshotSourceHealth:
        snapshot = self.store.get(JobSnapshot, snapshot_id, principal=principal)
        if snapshot.source_url is None:
            status, risks = "manual", ()
        elif available:
            status, risks = "live", ()
        else:
            status, risks = "unavailable", ("source_unavailable", "using_last_legal_snapshot")
        return SnapshotSourceHealth(
            snapshot_id=snapshot_id,
            source_status=status,
            risk_flags=risks,
            last_legal_content_sha256=snapshot.content.content_sha256,
        )

    def _commit_candidate(self, candidate, operation, principal) -> CommitReceipt:
        prior = self._promotion_event(candidate.candidate_id, principal)
        if prior is not None:
            return CommitReceipt(result_object_id=str(prior["job_id"]))
        exact, conflicts = self._identity_matches(
            operation.workspace_id, candidate, principal
        )
        if exact is not None:
            self.store.link_to_workspace(
                operation.workspace_id, exact.job_id, principal=principal
            )
            self._record_promotion(candidate, exact.job_id, exact.snapshot_id, conflicts, operation, principal)
            return CommitReceipt(result_object_id=exact.job_id)
        markdown = self.content.read_candidate(
            candidate.content.artifact_id,
            principal=principal,
            workspace_id=operation.workspace_id,
        )
        published = self.content.publish_snapshot(
            operation_id=operation.operation_id,
            filename=f"{Path(candidate.candidate_id).name}.normalized.md",
            markdown=markdown,
            content_sha256=candidate.content_sha256,
            principal=principal,
            workspace_id=operation.workspace_id,
        )
        digest = sha256(operation.operation_id.encode()).hexdigest()[:24]
        job_id, snapshot_id = f"job_{digest}", f"js_{digest}"
        now = self.clock()
        try:
            self.store.get(Job, job_id, principal=principal)
        except ObjectNotFoundError:
            self.store.create(
                Job(
                    job_id=job_id,
                    owner_id=principal,
                    title=candidate.title,
                    company=candidate.company,
                    location=candidate.location,
                    user_status=JobUserStatus.SAVED,
                    revision=1,
                    created_at=now,
                    updated_at=now,
                    allowed_actions=("analyze", "archive"),
                ),
                principal=principal,
            )
        try:
            snapshot = self.store.get(JobSnapshot, snapshot_id, principal=principal)
        except ObjectNotFoundError:
            snapshot = JobSnapshot(
                snapshot_id=snapshot_id,
                job_id=job_id,
                title=candidate.title,
                company=candidate.company,
                location=candidate.location,
                source_url=candidate.source_url,
                final_url=candidate.final_url,
                content=published.content_ref,
                verified=candidate.verified,
                verified_at=now if candidate.verified else None,
                source_status="live" if candidate.verified else "manual",
                risk_flags=candidate.risk_flags,
                captured_at=now,
            )
            self.store.create(snapshot, principal=principal)
        self.store.link_to_workspace(
            operation.workspace_id, job_id, principal=principal
        )
        self.evidence.bind(
            BindEvidenceCommand(
                workspace_id=operation.workspace_id,
                subject_id=snapshot_id,
                source_kind="document_version",
                source_ref=self._document_ref(snapshot.content),
                expected_sha256=snapshot.content.content_sha256,
            ),
            principal=principal,
        )
        if candidate.source_artifact_ref is not None:
            self.evidence.bind(
                BindEvidenceCommand(
                    workspace_id=operation.workspace_id,
                    subject_id=snapshot_id,
                    source_kind="artifact",
                    source_ref=candidate.source_artifact_ref,
                    expected_sha256=candidate.source_content_sha256,
                ),
                principal=principal,
            )
        self.evidence.bind(
            BindEvidenceCommand(
                workspace_id=operation.workspace_id,
                subject_id=snapshot_id,
                source_kind="artifact",
                source_ref=candidate.content.artifact_id,
                expected_sha256=candidate.content_sha256,
            ),
            principal=principal,
        )
        self._record_promotion(candidate, job_id, snapshot_id, conflicts, operation, principal)
        return CommitReceipt(result_object_id=job_id)

    def _identity_matches(self, workspace_id, candidate, principal):
        jobs = self._all_linked_jobs(workspace_id, principal)
        job_ids = {item.job_id for item in jobs}
        snapshots = self._all_snapshots(principal)
        exact = next(
            (
                item
                for item in snapshots
                if item.job_id in job_ids
                and item.content.content_sha256 == candidate.content_sha256
            ),
            None,
        )
        candidate_urls = {
            canonical_job_url(str(value))
            for value in (candidate.source_url, candidate.final_url)
            if value is not None
        }
        conflicts = tuple(
            item.snapshot_id
            for item in snapshots
            if item.job_id in job_ids
            and item.content.content_sha256 != candidate.content_sha256
            and candidate_urls
            & {
                canonical_job_url(str(value))
                for value in (item.source_url, item.final_url)
                if value is not None
            }
        )
        return exact, tuple(sorted(conflicts))

    def _all_linked_jobs(self, workspace_id, principal):
        items, cursor = [], None
        while True:
            page = self.store.list_linked(Job, workspace_id, principal=principal, cursor=cursor)
            items.extend(page.items)
            if page.next_cursor is None:
                return tuple(items)
            cursor = page.next_cursor

    def _all_snapshots(self, principal):
        items, cursor = [], None
        while True:
            page = self.store.list(JobSnapshot, principal=principal, cursor=cursor, include_archived=True)
            items.extend(page.items)
            if page.next_cursor is None:
                return tuple(items)
            cursor = page.next_cursor

    def _record_promotion(self, candidate, job_id, snapshot_id, conflicts, operation, principal):
        if self._promotion_event(candidate.candidate_id, principal) is None:
            self.store.append_event(
                candidate.candidate_id,
                principal=principal,
                event_type="candidate_promoted",
                payload={
                    "operation_id": operation.operation_id,
                    "job_id": job_id,
                    "snapshot_id": snapshot_id,
                    "conflict_snapshot_ids": list(conflicts),
                },
                occurred_at=self.clock(),
            )

    def _promotion(self, candidate_id, operation_id, principal, reused):
        event = self._promotion_event(candidate_id, principal)
        if event is None:
            raise JobServiceError("candidate_promotion_missing")
        return JobPromotion(
            operation_id=operation_id,
            candidate_id=candidate_id,
            job_id=str(event["job_id"]),
            snapshot_id=str(event["snapshot_id"]),
            reused=reused,
            conflict_snapshot_ids=tuple(event.get("conflict_snapshot_ids", ())),
        )

    def _promotion_event(self, candidate_id, principal):
        events = self.store.list_events(candidate_id, principal=principal)
        event = next((item for item in reversed(events) if item.event_type == "candidate_promoted"), None)
        return None if event is None else event.payload

    @staticmethod
    def _normalize(text: str) -> str:
        lines = [item.rstrip() for item in text.replace("\r\n", "\n").replace("\r", "\n").splitlines()]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            raise JobServiceError("job_description_empty")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _document_ref(content) -> str:
        if not (content.knowledge_base_id and content.document_id and content.document_version_id):
            raise JobServiceError("job_snapshot_knowledge_reference_missing")
        return f"knowledge://{content.knowledge_base_id}/{content.document_id}/{content.document_version_id}"


class _CandidateValidator:
    def __init__(self, candidate) -> None:
        self.candidate = candidate

    def validate(self, operation, outcome):
        accepted = outcome.result_ref == f"candidate://{self.candidate.candidate_id}" and outcome.result_sha256 == self.candidate.content_sha256
        return ValidationDecision(
            accepted=accepted,
            validator_version="job-candidate-v1",
            result_ref=outcome.result_ref,
            result_sha256=outcome.result_sha256,
            evidence_refs=(self.candidate.content.artifact_id,),
            error_code=None if accepted else "candidate_validation_failed",
        )


class _CandidateSafetyGate:
    def evaluate(self, operation, decision):
        return SafetyDecision(allowed=decision.accepted, summary={"candidate_only": True})


class _CandidateCommitter:
    def __init__(self, service: JobService, *, principal: str) -> None:
        self.service = service
        self.principal = principal

    def commit(self, operation, checkpoint):
        candidate_id = checkpoint.result_ref.removeprefix("candidate://")
        candidate = self.service.store.get(JobCandidate, candidate_id, principal=self.principal)
        return self.service._commit_candidate(candidate, operation, self.principal)


def canonical_job_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise JobServiceError("stable_url_invalid")
    if parsed.username or parsed.password:
        raise JobServiceError("stable_url_credentials_forbidden")
    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        folded = key.casefold()
        if folded in SENSITIVE_QUERY_KEYS:
            raise JobServiceError("stable_url_sensitive_query")
        if folded.startswith(TRACKING_QUERY_PREFIXES):
            continue
        query.append((key, value))
    host = parsed.hostname.casefold()
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            f"{host}{port}",
            parsed.path or "/",
            urlencode(sorted(query)),
            "",
        )
    )
