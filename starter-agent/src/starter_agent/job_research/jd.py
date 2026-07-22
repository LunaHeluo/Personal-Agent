from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.service import KnowledgeApplicationService


_REQUIRED_FIELDS = (
    "title",
    "company",
    "location",
    "responsibilities",
    "requirements",
)
_SENSITIVE_QUERY_KEYS = frozenset(
    {"access_token", "api_key", "apikey", "auth", "authorization", "cookie", "password", "secret", "token"}
)


class JobDescriptionSourceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    source_url: str
    content_sha256: str = ""
    call_id: str | None = None
    snapshot_id: str | None = None
    schema_hash: str | None = None


class NormalizedJobDescription(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str = ""
    company: str = ""
    location: str = ""
    responsibilities: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    source_url: str = ""
    source_content_sha256: str = ""
    is_complete: bool = False
    completeness_reasons: tuple[str, ...] = ()
    field_source_refs: dict[str, JobDescriptionSourceRef] = Field(default_factory=dict)
    call_id: str | None = None
    snapshot_id: str | None = None
    schema_hash: str | None = None
    raw_source_ref: str | None = None

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
            f"- Source content SHA-256: {self.source_content_sha256}\n\n"
            f"## Responsibilities\n\n{responsibility_lines}\n\n"
            f"## Requirements\n\n{requirement_lines}\n"
        )


class JobDescriptionNormalizer:
    """Normalize a fetched JD and fail closed on unverifiable completeness."""

    def normalize(
        self,
        value: Mapping[str, Any],
        *,
        call_id: str | None = None,
        snapshot_id: str | None = None,
        schema_hash: str | None = None,
        raw_source_ref: str | None = None,
    ) -> NormalizedJobDescription:
        metadata = value.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        call_id = call_id or _optional_text(metadata.get("call_id"))
        snapshot_id = snapshot_id or _optional_text(metadata.get("snapshot_id"))
        schema_hash = schema_hash or _optional_text(metadata.get("schema_hash"))
        raw_source_ref = raw_source_ref or _optional_text(
            metadata.get("raw_source_ref")
        )

        title = _text(value.get("title"))
        company = _text(value.get("company"))
        location = _text(value.get("location"))
        responsibilities = _items(value.get("responsibilities"))
        requirements = _items(value.get("requirements"))
        final_url = _canonical_http_url(
            _text(value.get("final_url") or metadata.get("final_url"))
        )
        content_sha256 = _valid_sha256(
            value.get("content_sha256") or metadata.get("content_sha256")
        )

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
        if not final_url:
            reasons.append("missing_final_url")
        if not content_sha256:
            reasons.append("missing_content_hash")
        page_type = _text(value.get("page_type")).casefold()
        error_code = _text(value.get("error_code")).casefold()
        if page_type == "listing" or error_code == "job_listing_page":
            reasons.append("listing_page")
        if page_type in {"login", "login_wall"} or error_code in {
            "authentication_required",
            "login_wall",
        }:
            reasons.append("login_wall")
        is_truncated = value.get("is_truncated") is True or metadata.get(
            "is_truncated"
        ) is True
        recovered = value.get("truncation_recovered") is True
        if is_truncated and not recovered:
            reasons.append("truncated_source")

        refs = {
            field: JobDescriptionSourceRef(
                field=field,
                source_url=final_url,
                content_sha256=content_sha256,
                call_id=call_id,
                snapshot_id=snapshot_id,
                schema_hash=schema_hash,
            )
            for field, item in fields.items()
            if item and final_url
        }
        return NormalizedJobDescription(
            title=title,
            company=company,
            location=location,
            responsibilities=responsibilities,
            requirements=requirements,
            source_url=final_url,
            source_content_sha256=content_sha256,
            is_complete=not reasons,
            completeness_reasons=tuple(dict.fromkeys(reasons)),
            field_source_refs=refs,
            call_id=call_id,
            snapshot_id=snapshot_id,
            schema_hash=schema_hash,
            raw_source_ref=raw_source_ref,
        )


class JobDescriptionTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str | None = None
    snapshot_id: str | None = None
    schema_hash: str | None = None
    source_url: str
    source_content_sha256: str
    raw_source_ref: str | None = None
    confirmation: str = "confirmed"
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
    """Confirmation barrier around the existing knowledge upload service."""

    def __init__(self, knowledge: KnowledgeApplicationService) -> None:
        self.knowledge = knowledge

    def ingest(
        self,
        job: NormalizedJobDescription,
        *,
        confirmed: bool,
        knowledge_base_id: UUID | None = None,
    ) -> JobDescriptionIngestionReceipt:
        if not confirmed:
            raise JobDescriptionIngestionError("confirmation_required")
        if not job.is_complete:
            raise JobDescriptionIngestionError("incomplete_job_description")
        base_id = knowledge_base_id or self.knowledge.default_knowledge_base_id
        filename = _source_filename(job.source_url)
        if any(
            document.document_type == "job_description"
            and document.filename == filename
            for document in self.knowledge.list_documents(base_id)
        ):
            raise JobDescriptionIngestionError("duplicate_source_url")
        try:
            upload = self.knowledge.upload(
                knowledge_base_id=base_id,
                filename=filename,
                content=job.to_markdown().encode("utf-8"),
                document_type="job_description",
                confirmed_authorized=True,
            )
        except KnowledgeError as exc:
            if exc.code == "duplicate_document_content":
                raise JobDescriptionIngestionError(
                    "duplicate_content_hash"
                ) from exc
            raise JobDescriptionIngestionError(exc.code) from exc
        trace = JobDescriptionTrace(
            call_id=job.call_id,
            snapshot_id=job.snapshot_id,
            schema_hash=job.schema_hash,
            source_url=job.source_url,
            source_content_sha256=job.source_content_sha256,
            raw_source_ref=job.raw_source_ref,
            document_id=upload.document.id,
            ingestion_job_id=upload.job.id,
        )
        return JobDescriptionIngestionReceipt(
            document_id=upload.document.id,
            version_id=upload.version.id,
            job_id=upload.job.id,
            trace=trace,
        )


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    text = _text(value)
    return text or None


def _items(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        item
        for item in (_text(candidate) for candidate in value)
        if item
    )


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
    query = urlencode(query_items)
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            f"{parsed.hostname.casefold()}{port}",
            parsed.path or "/",
            query,
            "",
        )
    )


def _source_filename(source_url: str) -> str:
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:24]
    return f"job-{digest}.md"
