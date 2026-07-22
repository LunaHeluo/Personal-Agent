from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from starter_agent.infrastructure.session_store import SQLiteSessionStore
from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.models import UploadBundle
from starter_agent.knowledge.service import KnowledgeApplicationService


_REQUIRED_FIELDS = (
    "title",
    "company",
    "location",
    "responsibilities",
    "requirements",
)
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "cookie",
        "password",
        "secret",
        "token",
    }
)
_GATE_REASON = "job_description_ingestion_confirmation_required"


class JobDescriptionSourceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    source_url: str
    source_content_sha256: str
    artifact_content_sha256: str
    artifact_ref: str
    server_id: str
    call_id: str
    snapshot_id: str
    schema_hash: str


class NormalizedJobDescription(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str = ""
    company: str = ""
    location: str = ""
    responsibilities: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    source_url: str = ""
    source_content_sha256: str = ""
    artifact_content_sha256: str = ""
    artifact_ref: str = ""
    server_id: str = ""
    call_id: str = ""
    snapshot_id: str = ""
    schema_hash: str = ""
    is_complete: bool = False
    completeness_reasons: tuple[str, ...] = ()
    field_source_refs: dict[str, JobDescriptionSourceRef] = Field(default_factory=dict)

    def to_markdown(self) -> str:
        responsibility_lines = "\n".join(
            f"- {item}" for item in self.responsibilities
        )
        requirement_lines = "\n".join(
            f"- {item}" for item in self.requirements
        )
        return (
            f"# {self.title}\n\n"
            f"- Company: {self.company}\n"
            f"- Location: {self.location}\n"
            f"- Source URL: {self.source_url}\n"
            f"- Source content SHA-256: {self.source_content_sha256}\n"
            f"- Artifact content SHA-256: {self.artifact_content_sha256}\n\n"
            f"## Responsibilities\n\n{responsibility_lines}\n\n"
            f"## Requirements\n\n{requirement_lines}\n"
        )


class JobDescriptionNormalizer:
    """Build a JD only from a restricted, server-loaded tool Artifact."""

    def normalize_artifact(
        self, artifact: Mapping[str, Any]
    ) -> NormalizedJobDescription:
        if artifact.get("restricted") is not True:
            raise ValueError("restricted_artifact_required")
        try:
            envelope = json.loads(str(artifact.get("content", "")))
        except (TypeError, ValueError) as exc:
            raise ValueError("artifact_content_invalid") from exc
        if not isinstance(envelope, dict) or envelope.get("ok") is not True:
            raise ValueError("artifact_result_invalid")
        payload = _artifact_payload(envelope.get("data"))

        title = _text(payload.get("title"))
        company = _text(payload.get("company"))
        location = _text(payload.get("location"))
        responsibilities = _items(payload.get("responsibilities"))
        requirements = _items(payload.get("requirements"))
        source_url = _canonical_http_url(_text(artifact.get("final_url")))
        source_content_sha256 = _valid_sha256(
            artifact.get("source_content_sha256")
        )
        artifact_content_sha256 = _valid_sha256(artifact.get("content_sha256"))
        artifact_ref = _text(artifact.get("source_ref"))
        server_id = _text(artifact.get("server_id"))
        call_id = _text(artifact.get("call_id"))
        snapshot_id = _text(artifact.get("snapshot_id"))
        schema_hash = _valid_sha256(artifact.get("schema_hash"))

        fields: dict[str, str | tuple[str, ...]] = {
            "title": title,
            "company": company,
            "location": location,
            "responsibilities": responsibilities,
            "requirements": requirements,
        }
        reasons = [
            f"missing_{field}"
            for field in _REQUIRED_FIELDS
            if not fields[field]
        ]
        for value, reason in (
            (source_url, "missing_final_url"),
            (source_content_sha256, "missing_content_hash"),
            (artifact_content_sha256, "missing_artifact_hash"),
            (artifact_ref, "missing_artifact_ref"),
            (server_id, "missing_server_id"),
            (call_id, "missing_call_id"),
            (snapshot_id, "missing_snapshot_id"),
            (schema_hash, "missing_schema_hash"),
        ):
            if not value:
                reasons.append(reason)
        page_type = _text(payload.get("page_type")).casefold()
        error_code = _text(payload.get("error_code")).casefold()
        if page_type == "listing" or error_code == "job_listing_page":
            reasons.append("listing_page")
        if page_type in {"login", "login_wall"} or error_code in {
            "authentication_required",
            "login_wall",
        }:
            reasons.append("login_wall")

        common = {
            "source_url": source_url,
            "source_content_sha256": source_content_sha256,
            "artifact_content_sha256": artifact_content_sha256,
            "artifact_ref": artifact_ref,
            "server_id": server_id,
            "call_id": call_id,
            "snapshot_id": snapshot_id,
            "schema_hash": schema_hash,
        }
        refs = {
            field: JobDescriptionSourceRef(field=field, **common)
            for field, item in fields.items()
            if item and all(common.values())
        }
        return NormalizedJobDescription(
            title=title,
            company=company,
            location=location,
            responsibilities=responsibilities,
            requirements=requirements,
            is_complete=not reasons,
            completeness_reasons=tuple(dict.fromkeys(reasons)),
            field_source_refs=refs,
            **common,
        )


class JobDescriptionApproval(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    principal: str
    session_id: UUID
    turn_id: UUID
    call_id: str
    artifact_ref: str
    server_id: str
    snapshot_id: str
    schema_hash: str
    source_url: str
    source_content_sha256: str
    artifact_content_sha256: str
    gate_reason_code: str
    status: str


class JobDescriptionTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    gate_reason_code: str
    confirmation_id: UUID
    principal: str
    session_id: UUID
    turn_id: UUID
    call_id: str
    server_id: str
    snapshot_id: str
    schema_hash: str
    artifact_ref: str
    source_url: str
    source_content_sha256: str
    artifact_content_sha256: str
    document_id: UUID
    ingestion_job_id: UUID


class JobDescriptionIngestionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: UUID
    version_id: UUID
    job_id: UUID
    trace: JobDescriptionTrace


class JobDescriptionIngestionError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class JobDescriptionIngestionService:
    """Persisted one-shot confirmation barrier around real knowledge ingestion."""

    def __init__(
        self,
        knowledge: KnowledgeApplicationService,
        artifact_store: SQLiteSessionStore,
    ) -> None:
        self.knowledge = knowledge
        self.artifact_store = artifact_store
        self.normalizer = JobDescriptionNormalizer()

    def prepare(
        self,
        *,
        source_ref: str,
        principal: str,
        session_id: UUID,
    ) -> JobDescriptionApproval:
        artifact = self._bound_artifact(source_ref, session_id)
        job = self.normalizer.normalize_artifact(artifact)
        if not job.is_complete:
            raise JobDescriptionIngestionError("incomplete_job_description")
        created = self.artifact_store.create_job_description_approval(
            principal=principal,
            session_id=session_id,
            turn_id=artifact["turn_id"],
            call_id=job.call_id,
            artifact_ref=job.artifact_ref,
            server_id=job.server_id,
            snapshot_id=job.snapshot_id,
            schema_hash=job.schema_hash,
            source_url=job.source_url,
            source_content_sha256=job.source_content_sha256,
            artifact_content_sha256=job.artifact_content_sha256,
            gate_reason_code=_GATE_REASON,
        )
        return JobDescriptionApproval.model_validate(created)

    def approve(
        self,
        approval_id: UUID,
        *,
        principal: str,
        session_id: UUID,
    ) -> JobDescriptionApproval:
        try:
            approved = self.artifact_store.approve_job_description_ingestion(
                approval_id, principal=principal, session_id=session_id
            )
        except ValueError as exc:
            raise JobDescriptionIngestionError(str(exc)) from exc
        return JobDescriptionApproval.model_validate(approved)

    def ingest(
        self,
        approval_id: UUID,
        *,
        principal: str,
        session_id: UUID,
        knowledge_base_id: UUID | None = None,
    ) -> JobDescriptionIngestionReceipt:
        approval = self._approval(approval_id, principal, session_id)
        if approval.status == "consumed":
            raise JobDescriptionIngestionError("confirmation_consumed")
        if approval.status != "approved":
            raise JobDescriptionIngestionError("confirmation_not_approved")
        artifact = self._bound_artifact(approval.artifact_ref, session_id)
        job = self.normalizer.normalize_artifact(artifact)
        self._verify_approval_binding(approval, job)
        base_id = knowledge_base_id or self.knowledge.default_knowledge_base_id
        try:
            self.knowledge.reserve_job_description_source_identity(
                base_id,
                reservation_id=approval.id,
                source_url=job.source_url,
                source_content_sha256=job.source_content_sha256,
            )
        except KnowledgeError as exc:
            code = {
                "duplicate_job_description_source_hash": "duplicate_source_hash",
                "duplicate_job_description_source_url": "duplicate_source_url",
            }.get(exc.code, exc.code)
            raise JobDescriptionIngestionError(code) from exc
        try:
            consumed = self.artifact_store.consume_job_description_approval(
                approval_id, principal=principal, session_id=session_id
            )
        except ValueError as exc:
            self.knowledge.release_job_description_source_identity(
                reservation_id=approval.id
            )
            raise JobDescriptionIngestionError(str(exc)) from exc
        upload = None
        try:
            upload = self.knowledge.upload(
                knowledge_base_id=base_id,
                filename=_source_filename(job.source_url),
                content=job.to_markdown().encode("utf-8"),
                document_type="job_description",
                confirmed_authorized=True,
            )
            self.knowledge.require_upload_succeeded(upload)
        except Exception as exc:
            if not (
                isinstance(exc, KnowledgeError)
                and exc.code == "document_ingestion_cleanup_failed"
            ):
                self._rollback_ingestion_attempt(
                    approval,
                    principal=principal,
                    session_id=session_id,
                    upload=upload,
                )
            code = (
                "duplicate_content_hash"
                if isinstance(exc, KnowledgeError)
                and exc.code == "duplicate_document_content"
                else (
                    exc.code
                    if isinstance(exc, KnowledgeError)
                    else "document_ingestion_failed"
                )
            )
            raise JobDescriptionIngestionError(code) from exc
        try:
            self.knowledge.commit_job_description_source_identity(
                reservation_id=approval.id,
                document_id=upload.document.id,
            )
        except KnowledgeError as exc:
            self._rollback_ingestion_attempt(
                approval,
                principal=principal,
                session_id=session_id,
                upload=upload,
            )
            raise JobDescriptionIngestionError(exc.code) from exc
        confirmed = JobDescriptionApproval.model_validate(consumed)
        trace = JobDescriptionTrace(
            gate_reason_code=confirmed.gate_reason_code,
            confirmation_id=confirmed.id,
            principal=confirmed.principal,
            session_id=confirmed.session_id,
            turn_id=confirmed.turn_id,
            call_id=confirmed.call_id,
            server_id=confirmed.server_id,
            snapshot_id=confirmed.snapshot_id,
            schema_hash=confirmed.schema_hash,
            artifact_ref=confirmed.artifact_ref,
            source_url=confirmed.source_url,
            source_content_sha256=confirmed.source_content_sha256,
            artifact_content_sha256=confirmed.artifact_content_sha256,
            document_id=upload.document.id,
            ingestion_job_id=upload.job.id,
        )
        return JobDescriptionIngestionReceipt(
            document_id=upload.document.id,
            version_id=upload.version.id,
            job_id=upload.job.id,
            trace=trace,
        )

    def _rollback_ingestion_attempt(
        self,
        approval: JobDescriptionApproval,
        *,
        principal: str,
        session_id: UUID,
        upload: UploadBundle | None,
    ) -> None:
        """Reopen approval only after this attempt's partial rows are gone."""

        try:
            if upload is not None:
                self.knowledge.discard_upload(upload)
        except Exception as exc:
            # Keep the consumed approval and reservation closed when cleanup is
            # uncertain; this is safer than allowing a duplicate retry.
            raise JobDescriptionIngestionError(
                "document_ingestion_cleanup_failed"
            ) from exc
        self.knowledge.release_job_description_source_identity(
            reservation_id=approval.id
        )
        self.artifact_store.restore_job_description_approval(
            approval.id, principal=principal, session_id=session_id
        )

    def _approval(
        self, approval_id: UUID, principal: str, session_id: UUID
    ) -> JobDescriptionApproval:
        value = self.artifact_store.get_job_description_approval(approval_id)
        if value is None:
            raise JobDescriptionIngestionError("confirmation_not_found")
        approval = JobDescriptionApproval.model_validate(value)
        if approval.principal != principal or approval.session_id != session_id:
            raise JobDescriptionIngestionError("confirmation_binding_mismatch")
        return approval

    def _bound_artifact(self, source_ref: str, session_id: UUID) -> dict[str, Any]:
        artifact = self.artifact_store.get_tool_artifact(source_ref)
        if artifact is None:
            raise JobDescriptionIngestionError("artifact_not_found")
        if artifact["session_id"] != session_id:
            raise JobDescriptionIngestionError("artifact_binding_mismatch")
        return artifact

    @staticmethod
    def _verify_approval_binding(
        approval: JobDescriptionApproval, job: NormalizedJobDescription
    ) -> None:
        expected = (
            approval.call_id,
            approval.artifact_ref,
            approval.server_id,
            approval.snapshot_id,
            approval.schema_hash,
            approval.source_url,
            approval.source_content_sha256,
            approval.artifact_content_sha256,
        )
        actual = (
            job.call_id,
            job.artifact_ref,
            job.server_id,
            job.snapshot_id,
            job.schema_hash,
            job.source_url,
            job.source_content_sha256,
            job.artifact_content_sha256,
        )
        if actual != expected:
            raise JobDescriptionIngestionError("artifact_binding_mismatch")


def _artifact_payload(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    structured = value.get("structured_content")
    return structured if isinstance(structured, Mapping) else value


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _items(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in (_text(candidate) for candidate in value) if item)


def _valid_sha256(value: object) -> str:
    text = _text(value).casefold()
    return (
        text
        if len(text) == 64
        and all(character in "0123456789abcdef" for character in text)
        else ""
    )


def _canonical_http_url(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return ""
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return ""
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    if any(key.casefold() in _SENSITIVE_QUERY_KEYS for key, _ in query_items):
        return ""
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            f"{parsed.hostname.casefold()}{port}",
            parsed.path or "/",
            urlencode(query_items),
            "",
        )
    )


def _source_filename(source_url: str) -> str:
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:24]
    return f"job-{digest}.md"
