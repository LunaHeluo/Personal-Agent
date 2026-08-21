"""Three-way, decision-driven resume merge proposals."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from starter_agent.cv_workbench.contracts import (
    BusinessOperation,
    MergeDecision,
    MergeDecisionType,
    MergeProposal,
    MergeProposalStatus,
    OperationStatus,
    Resume,
    ResumeBranch,
    ResumeVersion,
    ResumeVersionStatus,
)
from starter_agent.cv_workbench.store import ObjectNotFoundError, SQLiteWorkbenchStore
from starter_agent.cv_workbench.operations import (
    BusinessOperationService,
    CommitReceipt,
    OperationCommand,
    RunBinding,
    RunOutcome,
    SafetyDecision,
    ValidationDecision,
)
from starter_agent.cv_workbench.versioning import (
    ResumeVersionService,
    VersionContentRepository,
    VersioningError,
    _TextBlock,
)


EMPTY_SHA256 = sha256(b"").hexdigest()


class MergeConflictError(VersioningError):
    code = "merge_conflict"


class MergeStaleError(VersioningError):
    code = "source_stale"


class ResumeMergeService:
    def __init__(
        self,
        *,
        store: SQLiteWorkbenchStore,
        content: VersionContentRepository,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.content = content
        self.clock = clock
        self.versions = ResumeVersionService(
            store=store, content=content, clock=clock
        )

    def create_proposal(
        self,
        *,
        proposal_id: str,
        workspace_id: str,
        target_branch_id: str,
        base_version_id: str,
        upstream_version_id: str,
        target_version_id: str,
        principal: str,
    ) -> MergeProposal:
        base, upstream, target = (
            self.store.get(ResumeVersion, item, principal=principal)
            for item in (base_version_id, upstream_version_id, target_version_id)
        )
        if len({base.resume_id, upstream.resume_id, target.resume_id}) != 1:
            raise MergeConflictError("merge_inputs_cross_resume")
        if target.branch_id != target_branch_id:
            raise MergeConflictError("merge_target_branch_mismatch")
        if any(
            item.status != ResumeVersionStatus.CONFIRMED
            for item in (base, upstream, target)
        ):
            raise MergeConflictError("merge_inputs_must_be_confirmed")
        if (
            self.versions.common_ancestor(
                upstream_version_id, target_version_id, principal=principal
            )
            != base_version_id
        ):
            raise MergeConflictError("merge_base_is_not_common_ancestor")
        base_blocks = self._blocks(base, workspace_id, principal)
        upstream_blocks = self._blocks(upstream, workspace_id, principal)
        target_blocks = self._blocks(target, workspace_id, principal)
        decisions: list[MergeDecision] = []
        for block_id in sorted(set(base_blocks) | set(upstream_blocks) | set(target_blocks)):
            before = base_blocks.get(block_id)
            incoming = upstream_blocks.get(block_id)
            current = target_blocks.get(block_id)
            before_hash = self._hash(before)
            incoming_hash = self._hash(incoming)
            current_hash = self._hash(current)
            if incoming_hash == current_hash:
                continue
            if current_hash == before_hash:
                decision = MergeDecisionType.ACCEPT_UPSTREAM
                result_hash = incoming_hash or EMPTY_SHA256
                actor = principal
                decided_at = self.clock()
            elif incoming_hash == before_hash:
                decision = MergeDecisionType.KEEP_TARGET
                result_hash = current_hash or EMPTY_SHA256
                actor = principal
                decided_at = self.clock()
            else:
                decision = MergeDecisionType.UNRESOLVED
                result_hash = actor = decided_at = None
            decisions.append(
                MergeDecision(
                    block_id=block_id,
                    decision=decision,
                    base_sha256=before_hash,
                    upstream_sha256=incoming_hash,
                    target_sha256=current_hash,
                    result_sha256=result_hash,
                    decided_by=actor,
                    decided_at=decided_at,
                )
            )
        unresolved = any(
            item.decision == MergeDecisionType.UNRESOLVED for item in decisions
        )
        now = self.clock()
        proposal = MergeProposal.model_validate(
            {
                "proposal_id": proposal_id,
                "resume_id": base.resume_id,
                "target_branch_id": target_branch_id,
                "base_version_id": base_version_id,
                "upstream_version_id": upstream_version_id,
                "target_version_id": target_version_id,
                "base_content_sha256": base.content.content_sha256,
                "upstream_content_sha256": upstream.content.content_sha256,
                "target_content_sha256": target.content.content_sha256,
                "decisions": tuple(decisions),
                "status": (
                    MergeProposalStatus.CONFLICTED
                    if unresolved
                    else MergeProposalStatus.READY
                ),
                "revision": 1,
                "operation_id": None,
                "result_version_id": None,
                "created_by": principal,
                "created_at": now,
                "updated_at": now,
                "allowed_actions": (
                    ("decide_item", "cancel")
                    if unresolved
                    else ("commit", "cancel")
                ),
            }
        )
        return self.store.create(proposal, principal=principal, workspace_id=workspace_id)

    def decide(
        self,
        proposal_id: str,
        *,
        block_id: str,
        decision: MergeDecisionType,
        principal: str,
        expected_revision: int,
        manual_content: str | None = None,
    ) -> MergeProposal:
        proposal = self.store.get(MergeProposal, proposal_id, principal=principal)
        if proposal.status not in {
            MergeProposalStatus.CONFLICTED,
            MergeProposalStatus.DRAFT,
        }:
            raise MergeConflictError("merge_proposal_not_decidable")
        if decision == MergeDecisionType.UNRESOLVED:
            raise MergeConflictError("decision_must_resolve_item")
        items: list[MergeDecision] = []
        found = False
        for item in proposal.decisions:
            if item.block_id != block_id:
                items.append(item)
                continue
            found = True
            if decision == MergeDecisionType.ACCEPT_UPSTREAM:
                result_hash = item.upstream_sha256 or EMPTY_SHA256
            elif decision == MergeDecisionType.KEEP_TARGET:
                result_hash = item.target_sha256 or EMPTY_SHA256
            else:
                if manual_content is None:
                    raise MergeConflictError("manual_content_required")
                result_hash = sha256(manual_content.encode()).hexdigest()
            items.append(
                MergeDecision(
                    block_id=item.block_id,
                    decision=decision,
                    base_sha256=item.base_sha256,
                    upstream_sha256=item.upstream_sha256,
                    target_sha256=item.target_sha256,
                    result_sha256=result_hash,
                    manual_content=manual_content,
                    decided_by=principal,
                    decided_at=self.clock(),
                )
            )
        if not found:
            raise MergeConflictError("merge_item_not_found")
        ready = all(
            item.decision != MergeDecisionType.UNRESOLVED for item in items
        )
        updated = MergeProposal.model_validate(
            proposal.model_dump()
            | {
                "decisions": tuple(items),
                "status": (
                    MergeProposalStatus.READY
                    if ready
                    else MergeProposalStatus.CONFLICTED
                ),
                "revision": proposal.revision + 1,
                "updated_at": self.clock(),
                "allowed_actions": (
                    ("commit", "cancel")
                    if ready
                    else ("decide_item", "cancel")
                ),
            }
        )
        return self.store.update(
            updated, principal=principal, expected_revision=expected_revision
        )

    def commit_proposal(
        self,
        proposal_id: str,
        *,
        operation_id: str,
        idempotency_key: str,
        workspace_id: str,
        principal: str,
    ) -> BusinessOperation:
        """Validate and commit a ready proposal through the operation boundary."""
        proposal = self.store.get(MergeProposal, proposal_id, principal=principal)
        if proposal.status == MergeProposalStatus.COMMITTED and proposal.operation_id:
            return self.store.get(
                BusinessOperation, proposal.operation_id, principal=principal
            )
        self.store.assert_entity_in_workspace(
            proposal_id, workspace_id, principal=principal
        )
        input_sha256 = self._proposal_input_sha256(proposal)
        operations = BusinessOperationService(
            store=self.store,
            validator=_MergeValidator(proposal_id, input_sha256),
            safety_gate=_MergeSafetyGate(),
            committer=MergeBusinessCommitter(self, principal=principal),
            clock=self.clock,
        )
        operation, _created = operations.create(
            OperationCommand(
                operation_id=operation_id,
                workspace_id=workspace_id,
                operation_type="commit_resume_merge",
                idempotency_key=idempotency_key,
                input_sha256=input_sha256,
                expected_revision=proposal.revision,
            ),
            principal=principal,
        )
        if operation.status == OperationStatus.COMMITTED:
            return operation
        if operation.status == OperationStatus.COMMIT_FAILED:
            return operations.retry_commit(operation_id, principal=principal)
        run_id = f"local-merge:{operation_id}"
        if operation.status == OperationStatus.CREATED:
            operations.bind_run(
                operation_id,
                RunBinding(parent_run_id=run_id),
                principal=principal,
            )
        return operations.process_run_outcome(
            operation_id,
            RunOutcome(
                parent_run_id=run_id,
                status="succeeded",
                result_ref=f"merge://{proposal_id}",
                result_sha256=input_sha256,
            ),
            principal=principal,
        )

    @staticmethod
    def _proposal_input_sha256(proposal: MergeProposal) -> str:
        values = (
            proposal.proposal_id,
            str(proposal.revision),
            proposal.base_content_sha256,
            proposal.upstream_content_sha256,
            proposal.target_content_sha256,
            *(f"{item.block_id}:{item.decision}:{item.result_sha256 or ''}" for item in proposal.decisions),
        )
        return sha256("\0".join(values).encode()).hexdigest()

    def commit(
        self,
        proposal_id: str,
        *,
        operation_id: str,
        workspace_id: str,
        result_version_id: str,
        label: str,
        principal: str,
        expected_revision: int,
    ) -> ResumeVersion:
        proposal = self.store.get(MergeProposal, proposal_id, principal=principal)
        if proposal.status == MergeProposalStatus.COMMITTED:
            return self.store.get(
                ResumeVersion, proposal.result_version_id, principal=principal
            )
        if proposal.status not in {
            MergeProposalStatus.READY,
            MergeProposalStatus.COMMITTING,
        }:
            raise MergeConflictError("merge_proposal_not_ready")
        operation = self.store.get(
            BusinessOperation, operation_id, principal=principal
        )
        if operation.status != OperationStatus.COMMITTING:
            raise MergeConflictError("merge_operation_not_committing")
        base, upstream, target = (
            self.store.get(ResumeVersion, item, principal=principal)
            for item in (
                proposal.base_version_id,
                proposal.upstream_version_id,
                proposal.target_version_id,
            )
        )
        latest_target = max(
            (
                item
                for item in self.store.lineage(proposal.resume_id, principal=principal)
                if item.branch_id == proposal.target_branch_id
                and item.status == ResumeVersionStatus.CONFIRMED
            ),
            key=lambda item: (item.created_at, item.version_id),
        )
        hashes_match = (
            base.content.content_sha256 == proposal.base_content_sha256
            and upstream.content.content_sha256 == proposal.upstream_content_sha256
            and target.content.content_sha256 == proposal.target_content_sha256
            and latest_target.version_id == proposal.target_version_id
        )
        if not hashes_match:
            self._mark_stale(proposal, principal, expected_revision)
            raise MergeStaleError("merge_inputs_or_target_tip_changed")
        if proposal.status == MergeProposalStatus.READY:
            committing = MergeProposal.model_validate(
                proposal.model_dump()
                | {
                    "status": MergeProposalStatus.COMMITTING,
                    "operation_id": operation_id,
                    "revision": proposal.revision + 1,
                    "updated_at": self.clock(),
                    "allowed_actions": (),
                }
            )
            proposal = self.store.update(
                committing,
                principal=principal,
                expected_revision=expected_revision,
            )
        markdown = self._render_result(proposal, base, upstream, target, workspace_id, principal)
        normalized = self.versions.normalizer.normalize(markdown)
        reference = self.content.publish_version(
            version_id=result_version_id,
            markdown=normalized.markdown,
            content_sha256=normalized.content_sha256,
            principal=principal,
            workspace_id=workspace_id,
        )
        try:
            result = self.store.get(
                ResumeVersion, result_version_id, principal=principal
            )
        except ObjectNotFoundError:
            branch = self.store.get(
                ResumeBranch, proposal.target_branch_id, principal=principal
            )
            branch_versions = [
                item
                for item in self.store.lineage(proposal.resume_id, principal=principal)
                if item.branch_id == branch.branch_id
            ]
            now = self.clock()
            result = ResumeVersion.model_validate(
                {
                    "version_id": result_version_id,
                    "resume_id": proposal.resume_id,
                    "branch_id": branch.branch_id,
                    "parent_version_id": target.version_id,
                    "branch_base_version_id": branch.base_version_id,
                    "node_type": self.versions._node_type(branch.branch_type),
                    "version_number": max(item.version_number for item in branch_versions) + 1,
                    "label": label,
                    "content": reference,
                    "status": ResumeVersionStatus.CONFIRMED,
                    "job_snapshot_id": branch.job_snapshot_id,
                    "upstream_changes_available": False,
                    "revision": 1,
                    "created_by": principal,
                    "created_at": now,
                    "confirmed_at": now,
                    "allowed_actions": ("open_in_workbench", "compare", "export"),
                }
            )
            self.store.create(result, principal=principal)
        committed = MergeProposal.model_validate(
            proposal.model_dump()
            | {
                "status": MergeProposalStatus.COMMITTED,
                "result_version_id": result.version_id,
                "revision": proposal.revision + 1,
                "updated_at": self.clock(),
                "allowed_actions": (),
            }
        )
        self.store.update(
            committed,
            principal=principal,
            expected_revision=proposal.revision,
        )
        resume = self.store.get(Resume, proposal.resume_id, principal=principal)
        self.store.update(
            Resume.model_validate(
                resume.model_dump()
                | {
                    "latest_version_id": result.version_id,
                    "revision": resume.revision + 1,
                    "updated_at": self.clock(),
                }
            ),
            principal=principal,
            expected_revision=resume.revision,
        )
        return result

    def _render_result(self, proposal, base, upstream, target, workspace_id, principal) -> str:
        base_blocks = self._blocks(base, workspace_id, principal)
        upstream_blocks = self._blocks(upstream, workspace_id, principal)
        target_blocks = self._blocks(target, workspace_id, principal)
        decisions = {item.block_id: item for item in proposal.decisions}
        order = list(target_blocks) + [
            item for item in upstream_blocks if item not in target_blocks
        ]
        output: list[str] = []
        for block_id in order:
            decision = decisions.get(block_id)
            block = target_blocks.get(block_id)
            if decision is not None:
                if decision.decision == MergeDecisionType.ACCEPT_UPSTREAM:
                    block = upstream_blocks.get(block_id)
                elif decision.decision == MergeDecisionType.MANUAL:
                    block = _TextBlock(
                        block_id=block_id,
                        text=decision.manual_content or "",
                        content_sha256=decision.result_sha256 or EMPTY_SHA256,
                    )
            if block is not None and block.text.strip():
                output.append(block.text.strip("\n"))
        return "\n\n".join(output) + "\n"

    def _blocks(self, version, workspace_id, principal) -> dict[str, _TextBlock]:
        text = self.content.read(
            version.content, principal=principal, workspace_id=workspace_id
        )
        normalized = self.versions.normalizer.normalize(text)
        lines = normalized.markdown.rstrip("\n").splitlines()
        return {
            item.block_id: _TextBlock(
                block_id=item.block_id,
                text="\n".join(lines[item.start_line - 1 : item.end_line]),
                content_sha256=item.content_sha256,
            )
            for item in normalized.blocks
        }

    @staticmethod
    def _hash(block: _TextBlock | None) -> str | None:
        return None if block is None else block.content_sha256

    def _mark_stale(self, proposal, principal, expected_revision) -> None:
        stale = MergeProposal.model_validate(
            proposal.model_dump()
            | {
                "status": MergeProposalStatus.STALE,
                "revision": proposal.revision + 1,
                "updated_at": self.clock(),
                "allowed_actions": (),
            }
        )
        self.store.update(
            stale, principal=principal, expected_revision=expected_revision
        )


class MergeBusinessCommitter:
    """Task 4 committer adapter; retries reuse the same deterministic version ID."""

    def __init__(self, service: ResumeMergeService, *, principal: str) -> None:
        self.service = service
        self.principal = principal

    def commit(self, operation, checkpoint) -> CommitReceipt:
        prefix = "merge://"
        if not checkpoint.result_ref.startswith(prefix):
            raise MergeConflictError("merge_checkpoint_reference_invalid")
        proposal_id = checkpoint.result_ref.removeprefix(prefix)
        proposal = self.service.store.get(
            MergeProposal, proposal_id, principal=self.principal
        )
        digest = sha256(
            f"{proposal_id}\0{operation.operation_id}".encode()
        ).hexdigest()
        version_id = f"rv_merge_{digest[:24]}"
        result = self.service.commit(
            proposal_id,
            operation_id=operation.operation_id,
            workspace_id=operation.workspace_id,
            result_version_id=version_id,
            label=f"Merged {proposal_id}",
            principal=self.principal,
            expected_revision=proposal.revision,
        )
        return CommitReceipt(result_object_id=result.version_id)


class _MergeValidator:
    def __init__(self, proposal_id: str, input_sha256: str) -> None:
        self.proposal_id = proposal_id
        self.input_sha256 = input_sha256

    def validate(self, operation, outcome) -> ValidationDecision:
        accepted = (
            outcome.result_ref == f"merge://{self.proposal_id}"
            and outcome.result_sha256 == self.input_sha256
            and operation.input_sha256 == self.input_sha256
        )
        return ValidationDecision(
            accepted=accepted,
            validator_version="resume-merge-v1",
            result_ref=outcome.result_ref,
            result_sha256=outcome.result_sha256,
            error_code=None if accepted else "merge_validation_failed",
        )


class _MergeSafetyGate:
    def evaluate(self, operation, decision) -> SafetyDecision:
        return SafetyDecision(
            allowed=decision.accepted,
            summary={"user_decisions_required": True, "lineage_mutation": "new_child_only"},
            error_code=None if decision.accepted else "merge_safety_rejected",
        )
