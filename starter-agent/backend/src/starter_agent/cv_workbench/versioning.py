"""Draft editing, immutable version creation, lineage and diff services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import unified_diff
from hashlib import sha256
from typing import Protocol

from starter_agent.cv_workbench.contracts import (
    CONTRACT_VERSION,
    ContentReference,
    Resume,
    ResumeBranch,
    ResumeBranchType,
    ResumeDraft,
    ResumeDraftStatus,
    MergeDecision,
    MergeDecisionType,
    MergeProposal,
    MergeProposalStatus,
    ResumeNodeType,
    ResumeVersion,
    ResumeVersionStatus,
    VersionMap,
    VersionMapEdge,
    VersionMapNode,
)
from starter_agent.cv_workbench.resume_import import ResumeMarkdownNormalizer
from starter_agent.cv_workbench.store import SQLiteWorkbenchStore


class VersioningError(RuntimeError):
    code = "versioning_error"


class StaleContentError(VersioningError):
    code = "source_stale"


class BlockNotFoundError(VersioningError):
    code = "block_not_found"


class VersionContentRepository(Protocol):
    def read(
        self,
        reference: ContentReference,
        *,
        principal: str,
        workspace_id: str,
    ) -> str: ...

    def write_draft(
        self,
        *,
        draft_id: str,
        revision: int,
        markdown: str,
        content_sha256: str,
        principal: str,
        workspace_id: str,
    ) -> ContentReference: ...

    def publish_version(
        self,
        *,
        version_id: str,
        markdown: str,
        content_sha256: str,
        principal: str,
        workspace_id: str,
    ) -> ContentReference: ...


@dataclass(frozen=True)
class BlockPatch:
    block_id: str
    expected_sha256: str
    replacement: str


@dataclass(frozen=True)
class BlockDiff:
    block_id: str
    change: str
    left_sha256: str | None
    right_sha256: str | None


@dataclass(frozen=True)
class VersionDiff:
    left_version_id: str
    right_version_id: str
    common_ancestor_version_id: str | None
    blocks: tuple[BlockDiff, ...]
    unified: tuple[str, ...]


@dataclass(frozen=True)
class _TextBlock:
    block_id: str
    text: str
    content_sha256: str


class ResumeVersionService:
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
        self.normalizer = ResumeMarkdownNormalizer()

    def create_branch(
        self,
        *,
        branch_id: str,
        resume_id: str,
        name: str,
        branch_type: ResumeBranchType,
        base_version_id: str,
        principal: str,
        job_snapshot_id: str | None = None,
    ) -> ResumeBranch:
        base = self.store.get(
            ResumeVersion, base_version_id, principal=principal
        )
        if base.resume_id != resume_id or base.status != ResumeVersionStatus.CONFIRMED:
            raise VersioningError("branch_base_must_be_confirmed_same_resume")
        now = self.clock()
        branch = ResumeBranch.model_validate(
            {
                "contract_version": CONTRACT_VERSION,
                "branch_id": branch_id,
                "resume_id": resume_id,
                "name": name,
                "branch_type": branch_type,
                "base_version_id": base_version_id,
                "job_snapshot_id": job_snapshot_id,
                "archived": False,
                "revision": 1,
                "created_at": now,
                "updated_at": now,
                "allowed_actions": ("create_version", "archive"),
            }
        )
        return self.store.create(branch, principal=principal)

    def create_draft(
        self,
        *,
        draft_id: str,
        workspace_id: str,
        base_version_id: str,
        branch_id: str,
        principal: str,
    ) -> ResumeDraft:
        self.store.assert_entity_in_workspace(
            base_version_id, workspace_id, principal=principal
        )
        base = self.store.get(
            ResumeVersion, base_version_id, principal=principal
        )
        branch = self.store.get(ResumeBranch, branch_id, principal=principal)
        if branch.resume_id != base.resume_id:
            raise VersioningError("draft_branch_resume_mismatch")
        markdown = self.content.read(
            base.content, principal=principal, workspace_id=workspace_id
        )
        normalized = self.normalizer.normalize(markdown)
        reference = self.content.write_draft(
            draft_id=draft_id,
            revision=1,
            markdown=normalized.markdown,
            content_sha256=normalized.content_sha256,
            principal=principal,
            workspace_id=workspace_id,
        )
        now = self.clock()
        draft = ResumeDraft.model_validate(
            {
                "contract_version": CONTRACT_VERSION,
                "draft_id": draft_id,
                "resume_id": base.resume_id,
                "base_version_id": base.version_id,
                "branch_id": branch_id,
                "content": reference,
                "revision": 1,
                "status": ResumeDraftStatus.ACTIVE,
                "updated_by": principal,
                "created_at": now,
                "updated_at": now,
            }
        )
        stored = self.store.create(draft, principal=principal, workspace_id=workspace_id)
        self._event(stored.draft_id, principal, "draft_created", {"base_version_id": base.version_id})
        return stored

    def autosave(
        self,
        draft_id: str,
        markdown: str,
        *,
        workspace_id: str,
        principal: str,
        expected_revision: int,
        expected_content_sha256: str,
    ) -> ResumeDraft:
        draft = self.store.get(ResumeDraft, draft_id, principal=principal)
        if draft.status not in {ResumeDraftStatus.ACTIVE, ResumeDraftStatus.CONFLICT}:
            raise VersioningError("draft_not_editable")
        if draft.content.content_sha256 != expected_content_sha256:
            raise StaleContentError("draft_content_changed")
        normalized = self.normalizer.normalize(markdown)
        reference = self.content.write_draft(
            draft_id=draft_id,
            revision=draft.revision + 1,
            markdown=normalized.markdown,
            content_sha256=normalized.content_sha256,
            principal=principal,
            workspace_id=workspace_id,
        )
        updated = ResumeDraft.model_validate(
            draft.model_dump()
            | {
                "content": reference,
                "revision": draft.revision + 1,
                "status": ResumeDraftStatus.ACTIVE,
                "updated_by": principal,
                "updated_at": self.clock(),
            }
        )
        stored = self.store.update(
            updated, principal=principal, expected_revision=expected_revision
        )
        self._event(
            draft_id,
            principal,
            "draft_autosaved",
            {
                "before": draft.content.model_dump(mode="json"),
                "after": reference.model_dump(mode="json"),
                "parser_version": normalized.parser_version,
            },
        )
        return stored

    def apply_patch(
        self,
        draft_id: str,
        patch: BlockPatch,
        *,
        workspace_id: str,
        principal: str,
        expected_revision: int,
        expected_content_sha256: str,
    ) -> ResumeDraft:
        draft = self.store.get(ResumeDraft, draft_id, principal=principal)
        if draft.content.content_sha256 != expected_content_sha256:
            raise StaleContentError("patch_source_changed")
        markdown = self.content.read(
            draft.content, principal=principal, workspace_id=workspace_id
        )
        normalized = self.normalizer.normalize(markdown)
        block = next(
            (item for item in normalized.blocks if item.block_id == patch.block_id),
            None,
        )
        if block is None:
            raise BlockNotFoundError(patch.block_id)
        if block.content_sha256 != patch.expected_sha256:
            raise StaleContentError("patch_block_changed")
        lines = normalized.markdown.rstrip("\n").splitlines()
        replacement = patch.replacement.strip("\n").splitlines()
        lines[block.start_line - 1 : block.end_line] = replacement
        return self.autosave(
            draft_id,
            "\n".join(lines) + "\n",
            workspace_id=workspace_id,
            principal=principal,
            expected_revision=expected_revision,
            expected_content_sha256=expected_content_sha256,
        )

    def reorder_blocks(
        self,
        draft_id: str,
        ordered_block_ids: tuple[str, ...],
        *,
        workspace_id: str,
        principal: str,
        expected_revision: int,
        expected_content_sha256: str,
    ) -> ResumeDraft:
        draft = self.store.get(ResumeDraft, draft_id, principal=principal)
        if draft.content.content_sha256 != expected_content_sha256:
            raise StaleContentError("reorder_source_changed")
        markdown = self.content.read(
            draft.content, principal=principal, workspace_id=workspace_id
        )
        normalized = self.normalizer.normalize(markdown)
        if set(ordered_block_ids) != {
            item.block_id for item in normalized.blocks
        } or len(ordered_block_ids) != len(normalized.blocks):
            raise VersioningError("reorder_must_include_each_block_once")
        lines = normalized.markdown.rstrip("\n").splitlines()
        texts = {
            item.block_id: "\n".join(lines[item.start_line - 1 : item.end_line])
            for item in normalized.blocks
        }
        return self.autosave(
            draft_id,
            "\n\n".join(texts[item] for item in ordered_block_ids) + "\n",
            workspace_id=workspace_id,
            principal=principal,
            expected_revision=expected_revision,
            expected_content_sha256=expected_content_sha256,
        )

    def undo(
        self,
        draft_id: str,
        *,
        principal: str,
        expected_revision: int,
    ) -> ResumeDraft:
        draft = self.store.get(ResumeDraft, draft_id, principal=principal)
        events = self.store.list_events(draft_id, principal=principal)
        candidate = next(
            (
                event
                for event in reversed(events)
                if event.event_type == "draft_autosaved"
                and event.payload.get("after", {}).get("content_sha256")
                == draft.content.content_sha256
            ),
            None,
        )
        if candidate is None:
            raise VersioningError("nothing_to_undo")
        before = ContentReference.model_validate(candidate.payload["before"])
        updated = ResumeDraft.model_validate(
            draft.model_dump()
            | {
                "content": before,
                "revision": draft.revision + 1,
                "updated_by": principal,
                "updated_at": self.clock(),
            }
        )
        stored = self.store.update(
            updated, principal=principal, expected_revision=expected_revision
        )
        self._event(
            draft_id,
            principal,
            "draft_undo",
            {
                "from": draft.content.model_dump(mode="json"),
                "to": before.model_dump(mode="json"),
            },
        )
        return stored

    def redo(
        self,
        draft_id: str,
        *,
        principal: str,
        expected_revision: int,
    ) -> ResumeDraft:
        draft = self.store.get(ResumeDraft, draft_id, principal=principal)
        events = self.store.list_events(draft_id, principal=principal)
        if not events or events[-1].event_type != "draft_undo":
            raise VersioningError("nothing_to_redo")
        undo = events[-1]
        if undo.payload.get("to", {}).get("content_sha256") != draft.content.content_sha256:
            raise StaleContentError("redo_source_changed")
        restored = ContentReference.model_validate(undo.payload["from"])
        updated = ResumeDraft.model_validate(
            draft.model_dump()
            | {
                "content": restored,
                "revision": draft.revision + 1,
                "updated_by": principal,
                "updated_at": self.clock(),
            }
        )
        stored = self.store.update(
            updated, principal=principal, expected_revision=expected_revision
        )
        self._event(
            draft_id,
            principal,
            "draft_redo",
            {
                "from": draft.content.model_dump(mode="json"),
                "to": restored.model_dump(mode="json"),
            },
        )
        return stored

    def save_pending_version(
        self,
        draft_id: str,
        *,
        workspace_id: str,
        version_id: str,
        label: str,
        principal: str,
        expected_draft_revision: int,
    ) -> ResumeVersion:
        draft = self.store.get(ResumeDraft, draft_id, principal=principal)
        if draft.status != ResumeDraftStatus.ACTIVE:
            raise VersioningError("draft_not_active")
        base = self.store.get(
            ResumeVersion, draft.base_version_id, principal=principal
        )
        branch = self.store.get(
            ResumeBranch, draft.branch_id, principal=principal
        )
        markdown = self.content.read(
            draft.content, principal=principal, workspace_id=workspace_id
        )
        normalized = self.normalizer.normalize(markdown)
        reference = self.content.publish_version(
            version_id=version_id,
            markdown=normalized.markdown,
            content_sha256=normalized.content_sha256,
            principal=principal,
            workspace_id=workspace_id,
        )
        branch_versions = [
            item
            for item in self.store.lineage(draft.resume_id, principal=principal)
            if item.branch_id == draft.branch_id
        ]
        version = ResumeVersion.model_validate(
            {
                "contract_version": CONTRACT_VERSION,
                "version_id": version_id,
                "resume_id": draft.resume_id,
                "branch_id": draft.branch_id,
                "parent_version_id": base.version_id,
                "branch_base_version_id": branch.base_version_id,
                "node_type": self._node_type(branch.branch_type),
                "version_number": max((item.version_number for item in branch_versions), default=0) + 1,
                "label": label,
                "content": reference,
                "status": ResumeVersionStatus.PENDING_CONFIRMATION,
                "job_snapshot_id": branch.job_snapshot_id,
                "upstream_changes_available": False,
                "revision": 1,
                "created_by": principal,
                "created_at": self.clock(),
                "confirmed_at": None,
                "allowed_actions": ("confirm", "archive", "compare"),
            }
        )
        self.store.create(version, principal=principal)
        saved = ResumeDraft.model_validate(
            draft.model_dump()
            | {
                "status": ResumeDraftStatus.SAVED,
                "revision": draft.revision + 1,
                "updated_at": self.clock(),
            }
        )
        self.store.update(
            saved,
            principal=principal,
            expected_revision=expected_draft_revision,
        )
        self._event(draft_id, principal, "draft_saved_as_version", {"version_id": version_id})
        return version

    def confirm_version(
        self,
        version_id: str,
        *,
        principal: str,
        expected_revision: int,
    ) -> ResumeVersion:
        version = self.store.get(ResumeVersion, version_id, principal=principal)
        if version.status != ResumeVersionStatus.PENDING_CONFIRMATION:
            raise VersioningError("version_not_pending_confirmation")
        confirmed = ResumeVersion.model_validate(
            version.model_dump()
            | {
                "status": ResumeVersionStatus.CONFIRMED,
                "revision": version.revision + 1,
                "confirmed_at": self.clock(),
                "allowed_actions": ("open_in_workbench", "compare", "export"),
            }
        )
        stored = self.store.update(
            confirmed, principal=principal, expected_revision=expected_revision
        )
        resume = self.store.get(Resume, version.resume_id, principal=principal)
        updated_resume = Resume.model_validate(
            resume.model_dump()
            | {
                "latest_version_id": version_id,
                "revision": resume.revision + 1,
                "updated_at": self.clock(),
            }
        )
        self.store.update(
            updated_resume, principal=principal, expected_revision=resume.revision
        )
        self._event(version_id, principal, "version_confirmed", {})
        return stored

    def diff(
        self,
        left_version_id: str,
        right_version_id: str,
        *,
        workspace_id: str,
        principal: str,
    ) -> VersionDiff:
        left = self.store.get(ResumeVersion, left_version_id, principal=principal)
        right = self.store.get(ResumeVersion, right_version_id, principal=principal)
        if left.resume_id != right.resume_id:
            raise VersioningError("cross_resume_diff")
        left_text = self.content.read(left.content, principal=principal, workspace_id=workspace_id)
        right_text = self.content.read(right.content, principal=principal, workspace_id=workspace_id)
        left_blocks = {item.block_id: item for item in self.normalizer.normalize(left_text).blocks}
        right_blocks = {item.block_id: item for item in self.normalizer.normalize(right_text).blocks}
        changes: list[BlockDiff] = []
        for block_id in sorted(set(left_blocks) | set(right_blocks)):
            before, after = left_blocks.get(block_id), right_blocks.get(block_id)
            if before and after and before.content_sha256 == after.content_sha256:
                continue
            changes.append(
                BlockDiff(
                    block_id=block_id,
                    change="modified" if before and after else "removed" if before else "added",
                    left_sha256=None if before is None else before.content_sha256,
                    right_sha256=None if after is None else after.content_sha256,
                )
            )
        return VersionDiff(
            left_version_id=left_version_id,
            right_version_id=right_version_id,
            common_ancestor_version_id=self.common_ancestor(
                left_version_id, right_version_id, principal=principal
            ),
            blocks=tuple(changes),
            unified=tuple(
                unified_diff(
                    left_text.splitlines(),
                    right_text.splitlines(),
                    fromfile=left_version_id,
                    tofile=right_version_id,
                    lineterm="",
                )
            ),
        )

    def common_ancestor(
        self, left_version_id: str, right_version_id: str, *, principal: str
    ) -> str | None:
        left = self.store.get(ResumeVersion, left_version_id, principal=principal)
        right = self.store.get(ResumeVersion, right_version_id, principal=principal)
        if left.resume_id != right.resume_id:
            return None
        versions = {
            item.version_id: item
            for item in self.store.lineage(left.resume_id, principal=principal)
        }
        left_chain: set[str] = set()
        cursor: str | None = left_version_id
        while cursor is not None:
            left_chain.add(cursor)
            cursor = versions[cursor].parent_version_id
        cursor = right_version_id
        while cursor is not None:
            if cursor in left_chain:
                return cursor
            cursor = versions[cursor].parent_version_id
        return None

    def version_map(self, resume_id: str, *, principal: str) -> VersionMap:
        versions = self.store.lineage(resume_id, principal=principal)
        nodes = tuple(
            VersionMapNode(
                version_id=item.version_id,
                branch_id=item.branch_id,
                parent_version_id=item.parent_version_id,
                node_type=item.node_type,
                label=item.label,
                status=item.status,
                job_snapshot_id=item.job_snapshot_id,
                upstream_changes_available=self._has_upstream_change(item, versions),
                allowed_actions=item.allowed_actions,
            )
            for item in versions
        )
        return VersionMap(
            resume_id=resume_id,
            revision=max((item.revision for item in versions), default=1),
            nodes=nodes,
            edges=tuple(
                VersionMapEdge(
                    parent_version_id=item.parent_version_id,
                    child_version_id=item.version_id,
                )
                for item in versions
                if item.parent_version_id is not None
            ),
        )

    @staticmethod
    def _has_upstream_change(
        version: ResumeVersion, versions: tuple[ResumeVersion, ...]
    ) -> bool:
        if version.parent_version_id is None:
            return False
        by_id = {item.version_id: item for item in versions}
        base = by_id.get(version.branch_base_version_id)
        if base is None:
            return False
        ancestors: set[str] = set()
        cursor: str | None = version.version_id
        while cursor is not None:
            ancestors.add(cursor)
            cursor = by_id[cursor].parent_version_id
        return any(
            item.branch_id == base.branch_id
            and item.status == ResumeVersionStatus.CONFIRMED
            and item.version_id not in ancestors
            and item.created_at > base.created_at
            for item in versions
        )

    @staticmethod
    def _node_type(branch_type: ResumeBranchType) -> ResumeNodeType:
        return {
            ResumeBranchType.MASTER: ResumeNodeType.DERIVED,
            ResumeBranchType.DIRECTION: ResumeNodeType.DIRECTION,
            ResumeBranchType.COMPANY: ResumeNodeType.COMPANY,
            ResumeBranchType.DERIVED: ResumeNodeType.DERIVED,
        }[branch_type]

    def _event(self, entity_id: str, principal: str, event_type: str, payload: dict) -> None:
        self.store.append_event(
            entity_id,
            principal=principal,
            event_type=event_type,
            payload=payload,
            occurred_at=self.clock(),
        )
