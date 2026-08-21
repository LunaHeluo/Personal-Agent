"""Versioned, side-effect-free contracts for the CV workbench.

These models deliberately do not import the Agent runtime, stores, or HTTP
layer. They freeze the boundary between candidate output, user-confirmed
business objects, and immutable resume history before persistence is added.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)


CONTRACT_VERSION = "cv-workbench.v1"

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
WorkspaceId = Annotated[str, Field(pattern=r"^ws_[A-Za-z0-9_-]{1,120}$")]
ResumeId = Annotated[str, Field(pattern=r"^res_[A-Za-z0-9_-]{1,120}$")]
ResumeBranchId = Annotated[str, Field(pattern=r"^rb_[A-Za-z0-9_-]{1,120}$")]
ResumeVersionId = Annotated[str, Field(pattern=r"^rv_[A-Za-z0-9_-]{1,120}$")]
ResumeDraftId = Annotated[str, Field(pattern=r"^rd_[A-Za-z0-9_-]{1,120}$")]
MergeProposalId = Annotated[str, Field(pattern=r"^mp_[A-Za-z0-9_-]{1,120}$")]
JobCandidateId = Annotated[str, Field(pattern=r"^jc_[A-Za-z0-9_-]{1,120}$")]
JobId = Annotated[str, Field(pattern=r"^job_[A-Za-z0-9_-]{1,120}$")]
JobSnapshotId = Annotated[str, Field(pattern=r"^js_[A-Za-z0-9_-]{1,120}$")]
MatchAnalysisId = Annotated[str, Field(pattern=r"^ma_[A-Za-z0-9_-]{1,120}$")]
SuggestionId = Annotated[str, Field(pattern=r"^sg_[A-Za-z0-9_-]{1,120}$")]
ApplicationId = Annotated[str, Field(pattern=r"^app_[A-Za-z0-9_-]{1,120}$")]
ApplicationEventId = Annotated[str, Field(pattern=r"^ae_[A-Za-z0-9_-]{1,120}$")]
InterviewReviewId = Annotated[str, Field(pattern=r"^ir_[A-Za-z0-9_-]{1,120}$")]
InterviewRoundId = Annotated[str, Field(pattern=r"^round_[A-Za-z0-9_-]{1,120}$")]
InterviewSummaryId = Annotated[str, Field(pattern=r"^is_[A-Za-z0-9_-]{1,120}$")]
ExportId = Annotated[str, Field(pattern=r"^exp_[A-Za-z0-9_-]{1,120}$")]
OperationId = Annotated[str, Field(pattern=r"^op_[A-Za-z0-9_-]{1,120}$")]
RequirementId = Annotated[str, Field(pattern=r"^req_[A-Za-z0-9_-]{1,120}$")]
BlockId = Annotated[str, Field(pattern=r"^blk_[A-Za-z0-9_-]{1,120}$")]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class WorkspaceStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class ResumeStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ResumeBranchType(StrEnum):
    MASTER = "master"
    DIRECTION = "direction"
    COMPANY = "company"
    DERIVED = "derived"


class ResumeNodeType(StrEnum):
    BASE = "base"
    DIRECTION = "direction"
    COMPANY = "company"
    DERIVED = "derived"


class ResumeVersionStatus(StrEnum):
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"
    ARCHIVED = "archived"
    FAILED = "failed"


class ResumeDraftStatus(StrEnum):
    ACTIVE = "active"
    CONFLICT = "conflict"
    SAVED = "saved"
    DISCARDED = "discarded"


class MergeProposalStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    CONFLICTED = "conflicted"
    COMMITTING = "committing"
    COMMITTED = "committed"
    STALE = "stale"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MergeDecisionType(StrEnum):
    UNRESOLVED = "unresolved"
    KEEP_TARGET = "keep_target"
    ACCEPT_UPSTREAM = "accept_upstream"
    MANUAL = "manual"


class JobUserStatus(StrEnum):
    TO_ANALYZE = "to_analyze"
    SAVED = "saved"
    IGNORED = "ignored"
    ARCHIVED = "archived"


class MatchStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    VALIDATED = "validated"
    PARTIAL = "partial"
    FAILED = "failed"
    STALE = "stale"


class RequirementVerdict(StrEnum):
    MATCHED = "matched"
    PARTIAL = "partial"
    MISSING = "missing"
    CONFLICT = "conflict"


class SuggestionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"


class ApplicationStatus(StrEnum):
    TO_DECIDE = "to_decide"
    TO_APPLY = "to_apply"
    APPLIED = "applied"
    ASSESSMENT = "assessment"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    ARCHIVED = "archived"


class ExportStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AVAILABLE = "available"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OperationStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    VALIDATING = "validating"
    COMMITTING = "committing"
    COMMITTED = "committed"
    PARTIAL = "partial"
    FAILED = "failed"
    REJECTED = "rejected"
    COMMIT_FAILED = "commit_failed"
    CANCELLED = "cancelled"


class WorkbenchErrorCode(StrEnum):
    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"
    REVISION_CONFLICT = "revision_conflict"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    EVIDENCE_MISSING = "evidence_missing"
    SOURCE_STALE = "source_stale"
    APPROVAL_REQUIRED = "approval_required"
    RELEASE_GATE_CLOSED = "release_gate_closed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    BUSINESS_COMMIT_FAILED = "business_commit_failed"
    VALIDATION_FAILED = "validation_failed"


class ContentReference(ContractModel):
    content_sha256: Sha256
    knowledge_base_id: str | None = Field(default=None, min_length=1, max_length=160)
    document_id: str | None = Field(default=None, min_length=1, max_length=160)
    document_version_id: str | None = Field(default=None, min_length=1, max_length=160)
    artifact_id: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_storage_reference(self) -> "ContentReference":
        if self.document_version_id is None and self.artifact_id is None:
            raise ValueError("content_reference_requires_document_version_or_artifact")
        document_fields = (
            self.knowledge_base_id,
            self.document_id,
            self.document_version_id,
        )
        if any(document_fields) and not all(document_fields):
            raise ValueError("knowledge_content_reference_is_incomplete")
        return self


class EvidenceReference(ContractModel):
    chunk_id: str = Field(min_length=1, max_length=160)
    source_ref: str = Field(min_length=1, max_length=500)
    content_sha256: Sha256
    quote: str | None = Field(default=None, max_length=1000)


class Workspace(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    workspace_id: WorkspaceId
    owner_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=160)
    target_roles: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    target_cities: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    remote_preference: str | None = Field(default=None, max_length=80)
    seniority: str | None = Field(default=None, max_length=80)
    keywords: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    excluded_keywords: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    allowed_actions: tuple[str, ...] = Field(default_factory=tuple)


class Resume(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    resume_id: ResumeId
    owner_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    status: ResumeStatus = ResumeStatus.ACTIVE
    latest_version_id: ResumeVersionId | None = None
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    allowed_actions: tuple[str, ...] = Field(default_factory=tuple)


class ResumeBranch(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    branch_id: ResumeBranchId
    resume_id: ResumeId
    name: str = Field(min_length=1, max_length=160)
    branch_type: ResumeBranchType
    base_version_id: ResumeVersionId
    job_snapshot_id: JobSnapshotId | None = None
    archived: bool = False
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    allowed_actions: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_company_binding(self) -> "ResumeBranch":
        if self.branch_type != ResumeBranchType.COMPANY and self.job_snapshot_id is not None:
            raise ValueError("only_company_branch_can_bind_job_snapshot")
        return self


class ResumeVersion(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    version_id: ResumeVersionId
    resume_id: ResumeId
    branch_id: ResumeBranchId
    parent_version_id: ResumeVersionId | None = None
    branch_base_version_id: ResumeVersionId
    node_type: ResumeNodeType
    version_number: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=160)
    content: ContentReference
    status: ResumeVersionStatus
    job_snapshot_id: JobSnapshotId | None = None
    upstream_changes_available: bool = False
    revision: int = Field(ge=1)
    created_by: str = Field(min_length=1, max_length=200)
    created_at: AwareDatetime
    confirmed_at: AwareDatetime | None = None
    allowed_actions: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_lineage_and_confirmation(self) -> "ResumeVersion":
        if self.node_type == ResumeNodeType.BASE:
            if self.parent_version_id is not None:
                raise ValueError("base_version_cannot_have_parent")
            if self.branch_base_version_id != self.version_id:
                raise ValueError("base_version_must_be_its_branch_base")
        elif self.parent_version_id is None:
            raise ValueError("non_base_version_requires_parent")
        if self.node_type != ResumeNodeType.COMPANY and self.job_snapshot_id is not None:
            raise ValueError("only_company_version_can_bind_job_snapshot")
        if self.status == ResumeVersionStatus.CONFIRMED and self.confirmed_at is None:
            raise ValueError("confirmed_version_requires_confirmed_at")
        if self.status != ResumeVersionStatus.CONFIRMED and self.confirmed_at is not None:
            raise ValueError("unconfirmed_version_cannot_have_confirmed_at")
        return self


class ResumeDraft(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    draft_id: ResumeDraftId
    resume_id: ResumeId
    base_version_id: ResumeVersionId
    branch_id: ResumeBranchId
    content: ContentReference
    revision: int = Field(ge=1)
    status: ResumeDraftStatus = ResumeDraftStatus.ACTIVE
    updated_by: str = Field(min_length=1, max_length=200)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class MergeDecision(ContractModel):
    block_id: BlockId
    decision: MergeDecisionType
    base_sha256: Sha256 | None = None
    upstream_sha256: Sha256 | None = None
    target_sha256: Sha256 | None = None
    result_sha256: Sha256 | None = None
    manual_content: str | None = Field(default=None, max_length=100_000)
    decided_by: str | None = Field(default=None, min_length=1, max_length=200)
    decided_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> "MergeDecision":
        if self.decision == MergeDecisionType.UNRESOLVED:
            if self.decided_by is not None or self.decided_at is not None:
                raise ValueError("unresolved_merge_item_cannot_have_actor")
            return self
        if self.decided_by is None or self.decided_at is None or self.result_sha256 is None:
            raise ValueError("resolved_merge_item_requires_actor_time_and_result_hash")
        if self.decision == MergeDecisionType.MANUAL and self.manual_content is None:
            raise ValueError("manual_merge_item_requires_content")
        if self.decision != MergeDecisionType.MANUAL and self.manual_content is not None:
            raise ValueError("non_manual_merge_item_cannot_have_manual_content")
        return self


class MergeProposal(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    proposal_id: MergeProposalId
    resume_id: ResumeId
    target_branch_id: ResumeBranchId
    base_version_id: ResumeVersionId
    upstream_version_id: ResumeVersionId
    target_version_id: ResumeVersionId
    base_content_sha256: Sha256
    upstream_content_sha256: Sha256
    target_content_sha256: Sha256
    decisions: tuple[MergeDecision, ...]
    status: MergeProposalStatus
    revision: int = Field(ge=1)
    operation_id: OperationId | None = None
    result_version_id: ResumeVersionId | None = None
    created_by: str = Field(min_length=1, max_length=200)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    allowed_actions: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_merge_state(self) -> "MergeProposal":
        if len({self.base_version_id, self.upstream_version_id, self.target_version_id}) < 2:
            raise ValueError("merge_proposal_requires_distinct_inputs")
        unresolved = any(
            item.decision == MergeDecisionType.UNRESOLVED for item in self.decisions
        )
        if self.status in {MergeProposalStatus.READY, MergeProposalStatus.COMMITTING} and unresolved:
            raise ValueError("ready_merge_proposal_cannot_have_unresolved_items")
        if self.status == MergeProposalStatus.CONFLICTED and not unresolved:
            raise ValueError("conflicted_merge_proposal_requires_unresolved_item")
        if self.status == MergeProposalStatus.COMMITTED:
            if unresolved or self.operation_id is None or self.result_version_id is None:
                raise ValueError("committed_merge_proposal_requires_result")
        elif self.result_version_id is not None:
            raise ValueError("uncommitted_merge_proposal_cannot_have_result_version")
        return self


class VersionMapNode(ContractModel):
    version_id: ResumeVersionId
    branch_id: ResumeBranchId
    parent_version_id: ResumeVersionId | None
    node_type: ResumeNodeType
    label: str = Field(min_length=1, max_length=160)
    status: ResumeVersionStatus
    job_snapshot_id: JobSnapshotId | None = None
    upstream_changes_available: bool = False
    allowed_actions: tuple[str, ...] = Field(default_factory=tuple)


class VersionMapEdge(ContractModel):
    parent_version_id: ResumeVersionId
    child_version_id: ResumeVersionId

    @model_validator(mode="after")
    def reject_self_edge(self) -> "VersionMapEdge":
        if self.parent_version_id == self.child_version_id:
            raise ValueError("version_map_self_edge_is_invalid")
        return self


class VersionMap(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    resume_id: ResumeId
    revision: int = Field(ge=1)
    nodes: tuple[VersionMapNode, ...]
    edges: tuple[VersionMapEdge, ...]
    next_cursor: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_edges_reference_nodes(self) -> "VersionMap":
        node_ids = {node.version_id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("version_map_contains_duplicate_node")
        for edge in self.edges:
            if edge.parent_version_id not in node_ids or edge.child_version_id not in node_ids:
                raise ValueError("version_map_edge_references_missing_node")
        return self


class VersionViewPreference(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    owner_id: str = Field(min_length=1, max_length=200)
    resume_id: ResumeId
    node_positions: dict[ResumeVersionId, tuple[float, float]] = Field(default_factory=dict)
    collapsed_branch_ids: tuple[ResumeBranchId, ...] = Field(default_factory=tuple)
    viewport_x: float = 0
    viewport_y: float = 0
    viewport_zoom: float = Field(default=1, gt=0, le=8)
    revision: int = Field(ge=1)
    updated_at: AwareDatetime


class JobCandidate(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    candidate_id: JobCandidateId
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=300)
    location: str | None = Field(default=None, max_length=300)
    source_kind: Literal["manual", "text", "stable_url", "research"] = "manual"
    source_url: HttpUrl | None = None
    final_url: HttpUrl | None = None
    content_sha256: Sha256 | None = None
    content: ContentReference | None = None
    source_artifact_ref: str | None = Field(default=None, min_length=1, max_length=1000)
    source_content_sha256: Sha256 | None = None
    verified: bool = False
    risk_flags: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    candidate_only: Literal[True] = True

    @model_validator(mode="after")
    def validate_candidate_content(self) -> "JobCandidate":
        if self.content is not None and self.content_sha256 != self.content.content_sha256:
            raise ValueError("candidate_content_hash_mismatch")
        if self.verified and self.source_kind not in {"stable_url", "research"}:
            raise ValueError("only_controlled_source_candidate_can_be_verified")
        if self.verified and self.final_url is None:
            raise ValueError("verified_candidate_requires_final_url")
        if (self.source_artifact_ref is None) != (self.source_content_sha256 is None):
            raise ValueError("candidate_source_evidence_incomplete")
        if self.source_kind == "stable_url" and self.source_artifact_ref is None:
            raise ValueError("stable_url_candidate_requires_source_evidence")
        return self


class Job(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    job_id: JobId
    owner_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=300)
    location: str | None = Field(default=None, max_length=300)
    user_status: JobUserStatus
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    allowed_actions: tuple[str, ...] = Field(default_factory=tuple)


class JobSnapshot(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    snapshot_id: JobSnapshotId
    job_id: JobId
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=300)
    location: str | None = Field(default=None, max_length=300)
    source_url: HttpUrl | None = None
    final_url: HttpUrl | None = None
    content: ContentReference
    verified: bool
    source_status: Literal["live", "manual", "stale", "unavailable"] = "manual"
    risk_flags: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    captured_at: AwareDatetime
    verified_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_verification_time(self) -> "JobSnapshot":
        if self.verified != (self.verified_at is not None):
            raise ValueError("job_snapshot_verification_state_mismatch")
        return self


class RequirementResult(ContractModel):
    requirement_id: RequirementId
    original_text: str = Field(min_length=1, max_length=5000)
    category: Literal["responsibility", "required", "preferred"]
    importance: int = Field(ge=1, le=5)
    verdict: RequirementVerdict
    evidence: tuple[EvidenceReference, ...] = Field(default_factory=tuple)
    explanation: str = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def positive_verdict_requires_evidence(self) -> "RequirementResult":
        if self.verdict in {RequirementVerdict.MATCHED, RequirementVerdict.PARTIAL} and not self.evidence:
            raise ValueError("positive_requirement_verdict_requires_evidence")
        return self


class ScoreDimension(ContractModel):
    name: str = Field(min_length=1, max_length=120)
    weight: float = Field(ge=0, le=1)
    score: float = Field(ge=0, le=100)


class MatchAnalysis(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    analysis_id: MatchAnalysisId
    workspace_id: WorkspaceId
    resume_version_id: ResumeVersionId
    resume_content_sha256: Sha256
    job_snapshot_id: JobSnapshotId
    job_content_sha256: Sha256
    status: MatchStatus
    rule_version: str = Field(min_length=1, max_length=120)
    validator_version: str = Field(min_length=1, max_length=120)
    total_score: float | None = Field(default=None, ge=0, le=100)
    dimensions: tuple[ScoreDimension, ...] = Field(default_factory=tuple)
    requirements: tuple[RequirementResult, ...] = Field(default_factory=tuple)
    parent_run_id: str | None = Field(default=None, max_length=160)
    child_run_ids: tuple[str, ...] = Field(default_factory=tuple)
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    stale_reason: str | None = Field(default=None, max_length=500)
    allowed_actions: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_analysis_state(self) -> "MatchAnalysis":
        if self.status in {MatchStatus.VALIDATED, MatchStatus.PARTIAL, MatchStatus.STALE}:
            if self.total_score is None or not self.requirements:
                raise ValueError("completed_analysis_requires_score_and_requirements")
        elif self.total_score is not None:
            raise ValueError("incomplete_analysis_cannot_expose_total_score")
        if self.status == MatchStatus.STALE and self.stale_reason is None:
            raise ValueError("stale_analysis_requires_reason")
        return self


class CandidateResult(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    operation_id: OperationId
    candidate_kind: Literal["match_analysis", "suggestions", "job_candidates"]
    result_sha256: Sha256
    validator_version: str = Field(min_length=1, max_length=120)
    validated: bool = False
    business_object_id: None = None


class Suggestion(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    suggestion_id: SuggestionId
    analysis_id: MatchAnalysisId
    target_version_id: ResumeVersionId
    target_draft_id: ResumeDraftId
    target_draft_revision: int = Field(ge=1)
    block_id: BlockId
    original_text: str = Field(min_length=1, max_length=100_000)
    proposed_text: str = Field(min_length=1, max_length=100_000)
    change_type: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=5000)
    resume_evidence: tuple[EvidenceReference, ...]
    requirement_ids: tuple[RequirementId, ...]
    risk: str | None = Field(default=None, max_length=1000)
    status: SuggestionStatus = SuggestionStatus.PENDING
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    decided_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_suggestion_evidence_and_decision(self) -> "Suggestion":
        if not self.resume_evidence or not self.requirement_ids:
            raise ValueError("suggestion_requires_resume_and_job_evidence")
        decided = self.status in {SuggestionStatus.ACCEPTED, SuggestionStatus.REJECTED}
        if decided != (self.decided_at is not None):
            raise ValueError("suggestion_decision_time_mismatch")
        return self


class ApplicationEvent(ContractModel):
    event_id: ApplicationEventId
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)
    from_status: ApplicationStatus | None = None
    to_status: ApplicationStatus
    confirmed_by: str = Field(min_length=1, max_length=200)
    occurred_at: AwareDatetime
    note: str | None = Field(default=None, max_length=2000)


class Application(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    application_id: ApplicationId
    workspace_id: WorkspaceId
    job_snapshot_id: JobSnapshotId
    resume_version_id: ResumeVersionId
    current_status: ApplicationStatus
    priority: int = Field(default=0, ge=0, le=100)
    next_action: str | None = Field(default=None, max_length=500)
    remind_at: AwareDatetime | None = None
    events: tuple[ApplicationEvent, ...]
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    allowed_actions: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_event_projection(self) -> "Application":
        if not self.events or self.events[-1].to_status != self.current_status:
            raise ValueError("application_status_must_match_latest_event")
        return self


class InterviewRound(ContractModel):
    round_id: InterviewRoundId
    round_type: str = Field(min_length=1, max_length=120)
    occurred_at: AwareDatetime
    questions: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    answers: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    feedback: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    result: str | None = Field(default=None, max_length=500)
    improvement_items: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    created_by: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_answer_alignment(self) -> "InterviewRound":
        if self.answers and len(self.answers) != len(self.questions):
            raise ValueError("interview_answers_must_align_with_questions")
        return self


class InterviewSummaryCandidate(ContractModel):
    summary_id: InterviewSummaryId
    text: str = Field(min_length=1, max_length=10000)
    cited_round_ids: tuple[InterviewRoundId, ...] = Field(min_length=1)
    source_facts_sha256: Sha256
    status: Literal["pending", "accepted", "rejected"] = "pending"
    created_at: AwareDatetime
    decided_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_decision_time(self) -> "InterviewSummaryCandidate":
        if (self.status != "pending") != (self.decided_at is not None):
            raise ValueError("interview_summary_decision_time_mismatch")
        return self


class InterviewReview(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    review_id: InterviewReviewId
    workspace_id: WorkspaceId
    application_id: ApplicationId
    rounds: tuple[InterviewRound, ...] = Field(default_factory=tuple)
    summary_candidates: tuple[InterviewSummaryCandidate, ...] = Field(default_factory=tuple)
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_summary_citations(self) -> "InterviewReview":
        round_ids = {item.round_id for item in self.rounds}
        if any(not set(item.cited_round_ids).issubset(round_ids) for item in self.summary_candidates):
            raise ValueError("interview_summary_cites_unknown_round")
        return self


class ExportRecord(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    export_id: ExportId
    resume_version_id: ResumeVersionId
    format: Literal["pdf", "docx"]
    template_id: str = Field(min_length=1, max_length=120)
    template_version: str = Field(min_length=1, max_length=120)
    settings_sha256: Sha256
    status: ExportStatus
    artifact_id: str | None = Field(default=None, max_length=500)
    content_sha256: Sha256 | None = None
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    available_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_available_export(self) -> "ExportRecord":
        complete = self.status == ExportStatus.AVAILABLE
        has_result = (
            self.artifact_id is not None
            and self.content_sha256 is not None
            and self.available_at is not None
        )
        if complete != has_result:
            raise ValueError("export_availability_state_mismatch")
        return self


class BusinessOperation(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    operation_id: OperationId
    workspace_id: WorkspaceId
    operation_type: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=1, max_length=200)
    input_sha256: Sha256
    expected_revision: int | None = Field(default=None, ge=1)
    status: OperationStatus
    parent_run_id: str | None = Field(default=None, max_length=160)
    task_id: str | None = Field(default=None, max_length=160)
    result_object_id: str | None = Field(default=None, max_length=160)
    error_code: str | None = Field(default=None, max_length=120)
    retryable: bool = False
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_operation_result(self) -> "BusinessOperation":
        if self.status == OperationStatus.COMMITTED:
            if self.result_object_id is None or self.error_code is not None:
                raise ValueError("committed_operation_requires_clean_result")
        elif self.result_object_id is not None:
            raise ValueError("uncommitted_operation_cannot_have_result_object")
        if self.status in {
            OperationStatus.FAILED,
            OperationStatus.REJECTED,
            OperationStatus.COMMIT_FAILED,
        } and self.error_code is None:
            raise ValueError("failed_operation_requires_error_code")
        if self.status not in {
            OperationStatus.FAILED,
            OperationStatus.REJECTED,
            OperationStatus.COMMIT_FAILED,
        } and self.error_code is not None:
            raise ValueError("non_failed_operation_cannot_have_error_code")
        return self


class WorkbenchContext(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    context_epoch: int = Field(ge=1)
    workspace_id: WorkspaceId | None = None
    resume_version_id: ResumeVersionId | None = None
    resume_branch_id: ResumeBranchId | None = None
    lineage_focus_version_id: ResumeVersionId | None = None
    job_snapshot_id: JobSnapshotId | None = None
    match_analysis_id: MatchAnalysisId | None = None
    draft_id: ResumeDraftId | None = None
    merge_proposal_id: MergeProposalId | None = None
    ui_route: str = Field(min_length=1, max_length=160)
    selected_block_ids: tuple[BlockId, ...] = Field(default_factory=tuple)


class FeatureAvailability(ContractModel):
    manual_jd: bool = True
    stable_url: bool = True
    delegated_research: bool = False
    export_pdf: bool = False
    export_docx: bool = False
    email: bool = False
    unavailable_reasons: dict[str, str] = Field(default_factory=dict)


class WorkbenchHomeStats(ContractModel):
    resume_count: int = Field(default=0, ge=0)
    job_count: int = Field(default=0, ge=0)
    todo_count: int = Field(default=0, ge=0)
    active_operation_count: int = Field(default=0, ge=0)
    application_count: int = Field(default=0, ge=0)


class RecentVersionSummary(ContractModel):
    version_id: ResumeVersionId
    resume_id: ResumeId
    label: str = Field(min_length=1, max_length=160)
    status: ResumeVersionStatus
    created_at: AwareDatetime


class PriorityJobSummary(ContractModel):
    job_id: JobId
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=300)
    location: str | None = Field(default=None, max_length=300)
    user_status: JobUserStatus
    priority: int = Field(default=0, ge=0, le=100)


class RecentApplicationEventSummary(ContractModel):
    event_id: ApplicationEventId
    application_id: ApplicationId
    to_status: ApplicationStatus
    occurred_at: AwareDatetime


class WorkbenchHome(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    workspace: Workspace | None = None
    resume_ids: tuple[ResumeId, ...] = Field(default_factory=tuple)
    job_ids: tuple[JobId, ...] = Field(default_factory=tuple)
    active_operation_ids: tuple[OperationId, ...] = Field(default_factory=tuple)
    recent_application_ids: tuple[ApplicationId, ...] = Field(default_factory=tuple)
    stats: WorkbenchHomeStats = Field(default_factory=WorkbenchHomeStats)
    recent_versions: tuple[RecentVersionSummary, ...] = Field(default_factory=tuple)
    priority_jobs: tuple[PriorityJobSummary, ...] = Field(default_factory=tuple)
    recent_application_events: tuple[RecentApplicationEventSummary, ...] = Field(
        default_factory=tuple
    )
    features: FeatureAvailability = Field(default_factory=FeatureAvailability)


class WorkbenchError(ContractModel):
    code: WorkbenchErrorCode
    message: str = Field(min_length=1, max_length=1000)
    operation_id: OperationId | None = None
    retryable: bool = False
    authoritative_revision: int | None = Field(default=None, ge=1)
    recovery_action: str | None = Field(default=None, max_length=120)
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class WorkbenchErrorEnvelope(ContractModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    error: WorkbenchError


TRANSITIONS: dict[type[StrEnum], dict[StrEnum, frozenset[StrEnum]]] = {
    WorkspaceStatus: {
        WorkspaceStatus.ACTIVE: frozenset({WorkspaceStatus.PAUSED, WorkspaceStatus.ARCHIVED}),
        WorkspaceStatus.PAUSED: frozenset({WorkspaceStatus.ACTIVE, WorkspaceStatus.ARCHIVED}),
        WorkspaceStatus.ARCHIVED: frozenset(),
    },
    ResumeStatus: {
        ResumeStatus.ACTIVE: frozenset({ResumeStatus.ARCHIVED}),
        ResumeStatus.ARCHIVED: frozenset(),
    },
    ResumeVersionStatus: {
        ResumeVersionStatus.PENDING_CONFIRMATION: frozenset(
            {
                ResumeVersionStatus.CONFIRMED,
                ResumeVersionStatus.ARCHIVED,
                ResumeVersionStatus.FAILED,
            }
        ),
        ResumeVersionStatus.CONFIRMED: frozenset({ResumeVersionStatus.ARCHIVED}),
        ResumeVersionStatus.ARCHIVED: frozenset(),
        ResumeVersionStatus.FAILED: frozenset(),
    },
    ResumeDraftStatus: {
        ResumeDraftStatus.ACTIVE: frozenset(
            {ResumeDraftStatus.CONFLICT, ResumeDraftStatus.SAVED, ResumeDraftStatus.DISCARDED}
        ),
        ResumeDraftStatus.CONFLICT: frozenset(
            {ResumeDraftStatus.ACTIVE, ResumeDraftStatus.DISCARDED}
        ),
        ResumeDraftStatus.SAVED: frozenset(),
        ResumeDraftStatus.DISCARDED: frozenset(),
    },
    MergeProposalStatus: {
        MergeProposalStatus.DRAFT: frozenset(
            {
                MergeProposalStatus.READY,
                MergeProposalStatus.CONFLICTED,
                MergeProposalStatus.STALE,
                MergeProposalStatus.CANCELLED,
            }
        ),
        MergeProposalStatus.CONFLICTED: frozenset(
            {
                MergeProposalStatus.READY,
                MergeProposalStatus.STALE,
                MergeProposalStatus.CANCELLED,
            }
        ),
        MergeProposalStatus.READY: frozenset(
            {
                MergeProposalStatus.COMMITTING,
                MergeProposalStatus.STALE,
                MergeProposalStatus.CANCELLED,
            }
        ),
        MergeProposalStatus.COMMITTING: frozenset(
            {
                MergeProposalStatus.COMMITTED,
                MergeProposalStatus.FAILED,
                MergeProposalStatus.STALE,
            }
        ),
        MergeProposalStatus.COMMITTED: frozenset(),
        MergeProposalStatus.STALE: frozenset(),
        MergeProposalStatus.FAILED: frozenset(),
        MergeProposalStatus.CANCELLED: frozenset(),
    },
    MatchStatus: {
        MatchStatus.QUEUED: frozenset({MatchStatus.RUNNING, MatchStatus.FAILED}),
        MatchStatus.RUNNING: frozenset(
            {MatchStatus.VALIDATED, MatchStatus.PARTIAL, MatchStatus.FAILED}
        ),
        MatchStatus.VALIDATED: frozenset({MatchStatus.STALE}),
        MatchStatus.PARTIAL: frozenset({MatchStatus.STALE}),
        MatchStatus.FAILED: frozenset(),
        MatchStatus.STALE: frozenset(),
    },
    SuggestionStatus: {
        SuggestionStatus.PENDING: frozenset(
            {
                SuggestionStatus.ACCEPTED,
                SuggestionStatus.REJECTED,
                SuggestionStatus.INVALIDATED,
            }
        ),
        SuggestionStatus.ACCEPTED: frozenset(),
        SuggestionStatus.REJECTED: frozenset(),
        SuggestionStatus.INVALIDATED: frozenset(),
    },
    ApplicationStatus: {
        ApplicationStatus.TO_DECIDE: frozenset(
            {ApplicationStatus.TO_APPLY, ApplicationStatus.ARCHIVED}
        ),
        ApplicationStatus.TO_APPLY: frozenset(
            {
                ApplicationStatus.APPLIED,
                ApplicationStatus.WITHDRAWN,
                ApplicationStatus.ARCHIVED,
            }
        ),
        ApplicationStatus.APPLIED: frozenset(
            {
                ApplicationStatus.ASSESSMENT,
                ApplicationStatus.INTERVIEW,
                ApplicationStatus.REJECTED,
                ApplicationStatus.WITHDRAWN,
                ApplicationStatus.ARCHIVED,
            }
        ),
        ApplicationStatus.ASSESSMENT: frozenset(
            {
                ApplicationStatus.INTERVIEW,
                ApplicationStatus.REJECTED,
                ApplicationStatus.WITHDRAWN,
            }
        ),
        ApplicationStatus.INTERVIEW: frozenset(
            {
                ApplicationStatus.OFFER,
                ApplicationStatus.REJECTED,
                ApplicationStatus.WITHDRAWN,
            }
        ),
        ApplicationStatus.OFFER: frozenset(
            {ApplicationStatus.WITHDRAWN, ApplicationStatus.ARCHIVED}
        ),
        ApplicationStatus.REJECTED: frozenset({ApplicationStatus.ARCHIVED}),
        ApplicationStatus.WITHDRAWN: frozenset({ApplicationStatus.ARCHIVED}),
        ApplicationStatus.ARCHIVED: frozenset(),
    },
    ExportStatus: {
        ExportStatus.QUEUED: frozenset(
            {ExportStatus.RUNNING, ExportStatus.FAILED, ExportStatus.CANCELLED}
        ),
        ExportStatus.RUNNING: frozenset(
            {ExportStatus.AVAILABLE, ExportStatus.FAILED, ExportStatus.CANCELLED}
        ),
        ExportStatus.AVAILABLE: frozenset(),
        ExportStatus.FAILED: frozenset(),
        ExportStatus.CANCELLED: frozenset(),
    },
    OperationStatus: {
        OperationStatus.CREATED: frozenset(
            {OperationStatus.RUNNING, OperationStatus.CANCELLED}
        ),
        OperationStatus.RUNNING: frozenset(
            {
                OperationStatus.WAITING_FOR_USER,
                OperationStatus.VALIDATING,
                OperationStatus.PARTIAL,
                OperationStatus.FAILED,
                OperationStatus.CANCELLED,
            }
        ),
        OperationStatus.WAITING_FOR_USER: frozenset(
            {OperationStatus.RUNNING, OperationStatus.CANCELLED}
        ),
        OperationStatus.PARTIAL: frozenset(
            {OperationStatus.VALIDATING, OperationStatus.REJECTED}
        ),
        OperationStatus.VALIDATING: frozenset(
            {OperationStatus.COMMITTING, OperationStatus.REJECTED}
        ),
        OperationStatus.COMMITTING: frozenset(
            {OperationStatus.COMMITTED, OperationStatus.COMMIT_FAILED}
        ),
        OperationStatus.COMMIT_FAILED: frozenset({OperationStatus.COMMITTING}),
        OperationStatus.COMMITTED: frozenset(),
        OperationStatus.FAILED: frozenset(),
        OperationStatus.REJECTED: frozenset(),
        OperationStatus.CANCELLED: frozenset(),
    },
}


def transition_allowed(current: StrEnum, target: StrEnum) -> bool:
    """Return whether a same-state-family transition is permitted."""

    if type(current) is not type(target):
        return False
    return target in TRANSITIONS.get(type(current), {}).get(current, frozenset())


def assert_transition(current: StrEnum, target: StrEnum) -> None:
    if not transition_allowed(current, target):
        raise ValueError(f"illegal_transition:{type(current).__name__}:{current}->{target}")
