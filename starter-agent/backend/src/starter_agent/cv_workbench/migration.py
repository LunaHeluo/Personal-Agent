"""Dry-run-first, resumable migration from legacy local job-hunt data."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from starter_agent.cv_workbench.contracts import (
    JobCandidate,
    Resume,
    ResumeBranch,
    ResumeNodeType,
    ResumeVersion,
    ResumeVersionStatus,
)
from starter_agent.cv_workbench.runtime import WorkbenchRuntime
from starter_agent.cv_workbench.store import MODEL_TYPES, ObjectNotFoundError
from starter_agent.infrastructure.session_store import JobResearchCandidateRow
from starter_agent.knowledge.models import KnowledgeScope


MIGRATION_VERSION = "cv-workbench-legacy-v1"
SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}


class MigrationError(RuntimeError):
    code = "migration_error"


@dataclass(frozen=True)
class MigrationCandidate:
    source_kind: Literal["resume_file", "knowledge_claim", "research_candidate"]
    source_id: str
    source_key: str
    content_sha256: str | None
    status: Literal["ready", "manual_review", "error", "waiting_claim"]
    reason: str | None = None
    source_path: str | None = None
    old_version_id: str | None = None
    parent_source_key: str | None = None
    label: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class MigrationPlan:
    migration_version: str
    scanned_at: datetime
    resume_root: str | None
    candidates: tuple[MigrationCandidate, ...]
    warnings: tuple[str, ...]

    @property
    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in self.candidates:
            result[item.status] = result.get(item.status, 0) + 1
        return result


def _digest(content: bytes) -> str:
    return sha256(content).hexdigest()


def _source_key(kind: str, source_id: str, digest: str | None) -> str:
    return sha256("\0".join((MIGRATION_VERSION, kind, source_id, digest or "")).encode()).hexdigest()


def _stable_id(prefix: str, source_key: str) -> str:
    return f"{prefix}_{source_key[:24]}"


class LegacyMigrationService:
    def __init__(self, runtime: WorkbenchRuntime, *, registry_path: Path, clock=lambda: datetime.now(UTC)) -> None:
        self.runtime = runtime
        self.registry_path = registry_path
        self.clock = clock

    def scan(
        self,
        *,
        resume_root: Path | None = None,
        knowledge_scopes: tuple[KnowledgeScope, ...] = (),
        include_research_candidates: bool = True,
    ) -> MigrationPlan:
        candidates: list[MigrationCandidate] = []
        warnings: list[str] = ["历史 Chat 未扫描为业务对象；只能由用户显式提取候选。"]
        resolved_root: Path | None = None
        if resume_root is not None:
            resolved_root = resume_root.resolve()
            candidates.extend(self._scan_resumes(resolved_root, warnings))
        for scope in knowledge_scopes:
            candidates.extend(self._scan_knowledge(scope, warnings))
        if include_research_candidates:
            candidates.extend(self._scan_research_candidates())
        return MigrationPlan(MIGRATION_VERSION, self.clock(), str(resolved_root) if resolved_root else None, tuple(candidates), tuple(warnings))

    def _scan_resumes(self, root: Path, warnings: list[str]) -> list[MigrationCandidate]:
        if not root.is_dir():
            return [MigrationCandidate("resume_file", str(root), _source_key("resume_file", str(root), None), None, "error", "resume_root_not_found")]
        manifest_path = root / "versions.json"
        manifest: list[dict[str, object]] = []
        if manifest_path.exists():
            try:
                value = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(value, list): raise ValueError("manifest_not_array")
                manifest = [item for item in value if isinstance(item, dict)]
                if len(manifest) != len(value): warnings.append("versions.json 含非对象条目；已标记并跳过。")
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                warnings.append(f"versions.json 无法解析：{type(exc).__name__}")
                manifest = []
        old_by_id = {str(item.get("version_id")): item for item in manifest if item.get("version_id")}
        key_by_old: dict[str, str] = {}
        provisional: list[tuple[dict[str, object], Path | None, str | None, str, str | None]] = []
        referenced_sources = {str(item.get("source_path") or "") for item in manifest}
        for item in manifest:
            old_id = str(item.get("version_id") or "")
            relative = str(item.get("version_path") or "")
            path, reason = self._safe_legacy_path(root, relative)
            actual = None
            if path is not None:
                try: actual = _digest(path.read_bytes())
                except OSError: reason = "resume_file_unreadable"
            expected = str(item.get("sha256") or "") or None
            if actual is not None and expected != actual: reason = "manifest_content_hash_mismatch"
            source_id = f"{relative}|{old_id}"
            key = _source_key("resume_file", source_id, actual)
            if old_id: key_by_old[old_id] = key
            provisional.append((item, path, actual, key, reason))
        result: list[MigrationCandidate] = []
        for item, path, actual, key, reason in provisional:
            old_id = str(item.get("version_id") or "") or None
            parent_old = str(item.get("parent_id") or "") or None
            parent_key = key_by_old.get(parent_old or "")
            if reason is None and parent_old:
                parent = old_by_id.get(parent_old)
                if parent is None or parent_key is None:
                    reason = "parent_version_evidence_missing"
                elif str(parent.get("source_path") or "") != str(item.get("source_path") or ""):
                    reason = "parent_crosses_resume_family"
            status = "error" if reason and reason.startswith(("unsafe_", "resume_file", "manifest_content")) else "manual_review" if reason else "ready"
            result.append(MigrationCandidate(
                "resume_file", f"{item.get('version_path')}|{old_id or ''}", key, actual, status,
                reason, str(path) if path else None, old_id, parent_key,
                str(item.get("label") or old_id or "Legacy resume"),
                {"source_path": str(item.get("source_path") or ""), "parent_old_version_id": parent_old},
            ))
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.suffix.casefold() not in SUPPORTED_SUFFIXES:
                continue
            relative = path.relative_to(root).as_posix()
            if relative in referenced_sources: continue
            try: actual = _digest(path.read_bytes())
            except OSError:
                actual = None
            key = _source_key("resume_file", relative, actual)
            result.append(MigrationCandidate("resume_file", relative, key, actual, "ready" if actual else "error", None if actual else "resume_file_unreadable", str(path), None, None, path.stem, {"source_path": relative}))
        return result

    @staticmethod
    def _safe_legacy_path(root: Path, relative: str) -> tuple[Path | None, str | None]:
        if not relative or Path(relative).is_absolute(): return None, "unsafe_or_missing_path"
        path = (root / relative).resolve()
        try: path.relative_to(root)
        except ValueError: return None, "unsafe_path_escape"
        if path.suffix.casefold() not in SUPPORTED_SUFFIXES: return None, "unsafe_or_unsupported_format"
        if not path.is_file(): return None, "resume_file_missing"
        return path, None

    def _scan_knowledge(self, scope: KnowledgeScope, warnings: list[str]) -> list[MigrationCandidate]:
        values: list[MigrationCandidate] = []
        for base in self.runtime.knowledge.list_knowledge_bases(scope):
            for document in self.runtime.knowledge.list_documents(scope, base.id):
                if document.document_type not in {"resume", "job_description"} or not document.active_version_id:
                    continue
                source_id = f"{base.id}:{document.id}:{document.active_version_id}"
                key = _source_key("knowledge_claim", source_id, document.content_sha256)
                values.append(MigrationCandidate(
                    "knowledge_claim", source_id, key, document.content_sha256, "waiting_claim",
                    "workspace_claim_required", None, None, None, document.filename,
                    {"knowledge_base_id": str(base.id), "document_id": str(document.id), "document_version_id": str(document.active_version_id), "document_type": document.document_type, "user_id": scope.user_id, "project_id": scope.project_id},
                ))
        if values: warnings.append("Knowledge 文档仅生成待认领候选；未猜测 Workspace。")
        return values

    def _scan_research_candidates(self) -> list[MigrationCandidate]:
        with Session(self.runtime.artifacts.engine) as db:
            rows = tuple(db.scalars(select(JobResearchCandidateRow).order_by(JobResearchCandidateRow.created_at, JobResearchCandidateRow.id)))
        result = []
        for row in rows:
            digest = _digest(row.payload_json.encode())
            key = _source_key("research_candidate", row.id, digest)
            result.append(MigrationCandidate(
                "research_candidate", row.id, key, digest, "ready", None, None, None, None,
                row.title or "Untitled role",
                {"title": row.title, "company": row.company, "location": row.location, "source_url": row.source_url, "evidence_level": row.evidence_level, "created_at": row.created_at.isoformat(), "expires_at": row.expires_at.isoformat()},
            ))
        return result

    def commit(self, plan: MigrationPlan, *, batch_id: str, workspace_id: str, principal: str) -> dict[str, object]:
        if plan.migration_version != MIGRATION_VERSION: raise MigrationError("migration_version_mismatch")
        from starter_agent.cv_workbench.contracts import Workspace
        self.runtime.store.get(Workspace, workspace_id, principal=principal)
        registry = self._load_registry()
        batch = registry["batches"].get(batch_id)
        if batch is not None and batch.get("status") == "committed": return batch
        if batch is None:
            batch = {"batch_id": batch_id, "migration_version": MIGRATION_VERSION, "workspace_id": workspace_id, "principal": principal, "status": "committing", "created_at": self.clock().isoformat(), "items": []}
            registry["batches"][batch_id] = batch
            self._save_registry(registry)
        elif batch.get("workspace_id") != workspace_id or batch.get("principal") != principal:
            raise MigrationError("migration_batch_scope_mismatch")
        processed = {item["source_key"] for item in batch["items"]}
        for candidate in plan.candidates:
            if candidate.source_key in processed: continue
            item_result = self._commit_candidate(candidate, workspace_id, principal, registry)
            batch["items"].append(item_result)
            self._save_registry(registry)
        batch["status"] = "committed"
        batch["updated_at"] = self.clock().isoformat()
        self._save_registry(registry)
        return batch

    def _commit_candidate(self, candidate: MigrationCandidate, workspace_id: str, principal: str, registry: dict) -> dict[str, object]:
        existing = registry["sources"].get(candidate.source_key)
        if existing and existing.get("status") == "committed":
            return {"source_key": candidate.source_key, "status": "reused", "target_ids": existing.get("target_ids", [])}
        if candidate.status in {"error", "manual_review", "waiting_claim"}:
            value = {"source_key": candidate.source_key, "source_kind": candidate.source_kind, "source_id": candidate.source_id, "content_sha256": candidate.content_sha256, "status": candidate.status, "error": candidate.reason, "target_ids": [], "created_new": False}
            registry["sources"][candidate.source_key] = value
            return value
        try:
            if candidate.source_kind == "resume_file":
                target_ids = self._commit_resume(candidate, workspace_id, principal, registry)
            elif candidate.source_kind == "research_candidate":
                target_ids = self._commit_research(candidate, principal)
            else:
                target_ids = []
            value = {"source_key": candidate.source_key, "source_kind": candidate.source_kind, "source_id": candidate.source_id, "content_sha256": candidate.content_sha256, "status": "committed", "error": None, "target_ids": target_ids, "created_new": True}
        except Exception as exc:
            value = {"source_key": candidate.source_key, "source_kind": candidate.source_kind, "source_id": candidate.source_id, "content_sha256": candidate.content_sha256, "status": "failed", "error": getattr(exc, "code", type(exc).__name__), "target_ids": [], "created_new": False}
        registry["sources"][candidate.source_key] = value
        return value

    def _commit_resume(self, candidate: MigrationCandidate, workspace_id: str, principal: str, registry: dict) -> list[str]:
        if not candidate.source_path or not candidate.content_sha256: raise MigrationError("resume_candidate_content_missing")
        raw_content = Path(candidate.source_path).read_bytes()
        if _digest(raw_content) != candidate.content_sha256: raise MigrationError("migration_source_changed")
        content = raw_content.decode("utf-8")
        normalized = self.runtime.versions.normalizer.normalize(content)
        if candidate.parent_source_key:
            parent_mapping = registry["sources"].get(candidate.parent_source_key)
            if not parent_mapping or parent_mapping.get("status") != "committed": raise MigrationError("migration_parent_not_committed")
            parent_version_id = next((value for value in reversed(parent_mapping["target_ids"]) if str(value).startswith("rv_")), None)
            if not parent_version_id: raise MigrationError("migration_parent_version_missing")
            parent = self.runtime.store.get(ResumeVersion, parent_version_id, principal=principal)
            version_id = _stable_id("rv", candidate.source_key)
            try:
                existing_version = self.runtime.store.get(ResumeVersion, version_id, principal=principal)
            except ObjectNotFoundError:
                existing_version = None
            if existing_version is not None:
                if existing_version.content.content_sha256 != normalized.content_sha256 or existing_version.parent_version_id != parent.version_id:
                    raise MigrationError("migration_stable_version_conflict")
                return [version_id]
            reference = self.runtime.versions.content.publish_version(version_id=version_id, markdown=normalized.markdown, content_sha256=normalized.content_sha256, principal=principal, workspace_id=workspace_id)
            now = self.clock()
            version = ResumeVersion(
                version_id=version_id, resume_id=parent.resume_id, branch_id=parent.branch_id,
                parent_version_id=parent.version_id, branch_base_version_id=parent.branch_base_version_id,
                node_type=ResumeNodeType.DERIVED, version_number=parent.version_number + 1,
                label=candidate.label or "Legacy version", content=reference,
                status=ResumeVersionStatus.CONFIRMED, revision=1, created_by=principal,
                created_at=now, confirmed_at=now, allowed_actions=("open_in_workbench", "compare", "export"),
            )
            self.runtime.store.create(version, principal=principal, workspace_id=workspace_id)
            resume = self.runtime.store.get(Resume, parent.resume_id, principal=principal)
            self.runtime.store.update(Resume.model_validate(resume.model_dump() | {"latest_version_id": version.version_id, "revision": resume.revision + 1, "updated_at": now}), principal=principal, expected_revision=resume.revision)
            return [version.version_id]
        resume_id = _stable_id("res", candidate.source_key); branch_id = _stable_id("rb", candidate.source_key); version_id = _stable_id("rv", candidate.source_key)
        now = self.clock()
        resume = Resume(resume_id=resume_id, owner_id=principal, name=candidate.label or Path(candidate.source_path).stem, status="active", latest_version_id=None, revision=1, created_at=now, updated_at=now, allowed_actions=("create_branch", "archive"))
        try: stored_resume = self.runtime.store.get(Resume, resume_id, principal=principal)
        except ObjectNotFoundError:
            stored_resume = self.runtime.store.create(resume, principal=principal)
        branch = ResumeBranch(branch_id=branch_id, resume_id=resume_id, name="master", branch_type="master", base_version_id=version_id, archived=False, revision=1, created_at=now, updated_at=now, allowed_actions=("create_version",))
        try: self.runtime.store.get(ResumeBranch, branch_id, principal=principal)
        except ObjectNotFoundError: self.runtime.store.create(branch, principal=principal)
        try:
            version = self.runtime.store.get(ResumeVersion, version_id, principal=principal)
            if version.content.content_sha256 != normalized.content_sha256: raise MigrationError("migration_stable_version_conflict")
        except ObjectNotFoundError:
            reference = self.runtime.versions.content.publish_version(version_id=version_id, markdown=normalized.markdown, content_sha256=normalized.content_sha256, principal=principal, workspace_id=workspace_id)
            version = ResumeVersion(version_id=version_id, resume_id=resume_id, branch_id=branch_id, parent_version_id=None, branch_base_version_id=version_id, node_type=ResumeNodeType.BASE, version_number=1, label=candidate.label or "Legacy root", content=reference, status=ResumeVersionStatus.CONFIRMED, revision=1, created_by=principal, created_at=now, confirmed_at=now, allowed_actions=("open_in_workbench", "compare", "export"))
            self.runtime.store.create(version, principal=principal, workspace_id=workspace_id)
        current_resume = self.runtime.store.get(Resume, resume_id, principal=principal)
        if current_resume.latest_version_id != version_id:
            self.runtime.store.update(Resume.model_validate(current_resume.model_dump() | {"latest_version_id": version_id, "revision": current_resume.revision + 1, "updated_at": now}), principal=principal, expected_revision=current_resume.revision)
        self.runtime.store.link_to_workspace(workspace_id, resume_id, principal=principal)
        return [resume_id, branch_id, version_id]

    def _commit_research(self, candidate: MigrationCandidate, principal: str) -> list[str]:
        metadata = candidate.metadata or {}; candidate_id = _stable_id("jc", candidate.source_key)
        url = str(metadata.get("source_url") or "")
        if not url.startswith(("http://", "https://")): url = ""
        created_at = datetime.fromisoformat(str(metadata["created_at"])); expires_at = datetime.fromisoformat(str(metadata["expires_at"]))
        if created_at.tzinfo is None: created_at = created_at.replace(tzinfo=UTC)
        if expires_at.tzinfo is None: expires_at = expires_at.replace(tzinfo=UTC)
        value = JobCandidate(
            candidate_id=candidate_id, title=str(metadata.get("title") or "Untitled role"),
            company=str(metadata.get("company") or "Unknown company"), location=str(metadata.get("location") or "") or None,
            source_kind="research", source_url=url or None, final_url=None, verified=False,
            risk_flags=("legacy_partial_evidence",) if metadata.get("evidence_level") != "complete" else (),
            created_at=created_at,
            expires_at=expires_at,
        )
        try: self.runtime.store.create(value, principal=principal)
        except Exception:
            existing = self.runtime.store.get(JobCandidate, candidate_id, principal=principal)
            if existing != value: raise
        return [candidate_id]

    def validate(self, batch_id: str, *, principal: str) -> dict[str, object]:
        registry = self._load_registry(); batch = registry["batches"].get(batch_id)
        if not batch: raise MigrationError("migration_batch_not_found")
        results = []
        for item in batch["items"]:
            targets = item.get("target_ids", []); valid = True; error = None
            for target_id in targets:
                model = next((model for name, model in MODEL_TYPES.items() if str(target_id).startswith({"Resume": "res_", "ResumeBranch": "rb_", "ResumeVersion": "rv_", "JobCandidate": "jc_"}.get(name, "!"))), None)
                try:
                    if model is None: raise MigrationError("migration_target_type_unknown")
                    self.runtime.store.get(model, target_id, principal=principal)
                except Exception as exc: valid = False; error = getattr(exc, "code", type(exc).__name__); break
            results.append({"source_key": item["source_key"], "valid": valid, "error": error})
        return {"batch_id": batch_id, "valid": all(item["valid"] for item in results), "items": results}

    def rollback(self, batch_id: str, *, principal: str) -> dict[str, object]:
        registry = self._load_registry(); batch = registry["batches"].get(batch_id)
        if not batch: raise MigrationError("migration_batch_not_found")
        removed, blocked = [], []
        for item in reversed(batch["items"]):
            if not item.get("created_new"): continue
            for target_id in reversed(item.get("target_ids", [])):
                try:
                    if str(target_id).startswith("rv_") and not self.runtime.store.prepare_migration_version_delete(target_id, principal=principal):
                        blocked.append(target_id); continue
                    if self.runtime.store.delete_migration_entity_if_unreferenced(target_id, principal=principal): removed.append(target_id)
                    else: blocked.append(target_id)
                except ObjectNotFoundError:
                    removed.append(target_id)
            if not any(target in blocked for target in item.get("target_ids", [])):
                registry["sources"].pop(item["source_key"], None)
        batch["status"] = "rolled_back" if not blocked else "rollback_partial"
        batch["rollback_at"] = self.clock().isoformat(); batch["removed_target_ids"] = removed; batch["blocked_target_ids"] = blocked
        self._save_registry(registry)
        return {"batch_id": batch_id, "status": batch["status"], "removed_target_ids": removed, "blocked_target_ids": blocked}

    def _load_registry(self) -> dict:
        if not self.registry_path.exists(): return {"format_version": 1, "migration_version": MIGRATION_VERSION, "sources": {}, "batches": {}}
        try: value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc: raise MigrationError("migration_registry_invalid") from exc
        if value.get("migration_version") != MIGRATION_VERSION: raise MigrationError("migration_registry_version_mismatch")
        return value

    def _save_registry(self, registry: dict) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.registry_path.with_suffix(self.registry_path.suffix + ".tmp")
        temporary.write_text(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.registry_path)

    @staticmethod
    def plan_dict(plan: MigrationPlan) -> dict[str, object]:
        return {"migration_version": plan.migration_version, "scanned_at": plan.scanned_at.isoformat(), "resume_root": plan.resume_root, "counts": plan.counts, "warnings": plan.warnings, "candidates": [asdict(item) for item in plan.candidates]}
