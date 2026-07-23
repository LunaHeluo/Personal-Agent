from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from starter_agent.capabilities.models import SkillRecord
from starter_agent.capabilities.store import RevisionConflictError
from starter_agent.skills.models import (
    SkillDefinition,
    SkillDependency,
    SkillSnapshot,
)
from starter_agent.skills.parser import SkillParseError, SkillParser


DependencyResolver = Callable[[SkillDependency], bool]


class SkillRegistry:
    """Publish immutable Skill snapshots after complete candidate validation."""

    def __init__(
        self,
        root: Path,
        *,
        parser: SkillParser | None = None,
        dependency_resolver: DependencyResolver | None = None,
        store=None,
    ) -> None:
        self.root = root.resolve()
        self.parser = parser or SkillParser()
        self.dependency_resolver = dependency_resolver or (lambda _item: True)
        self.store = store
        self._write_lock = Lock()
        self._enabled_overrides: dict[str, bool] = {}
        self._snapshot = SkillSnapshot(
            revision=0,
            loaded_at=datetime.now(UTC),
        )

    def snapshot(self) -> SkillSnapshot:
        return self._snapshot

    def reload(self) -> SkillSnapshot:
        try:
            candidates = self._load_candidates()
        except Exception as exc:
            return self._mark_stale(exc)
        with self._write_lock:
            try:
                self._persist_candidates(candidates)
            except Exception as exc:
                current = self._snapshot
                self._snapshot = current.model_copy(
                    update={
                        "revision": current.revision + 1,
                        "loaded_at": datetime.now(UTC),
                        "stale": True,
                        "last_error": str(exc)[:2_000],
                    }
                )
                return self._snapshot
            current = self._snapshot
            self._snapshot = SkillSnapshot(
                revision=current.revision + 1,
                skills=candidates,
                loaded_at=datetime.now(UTC),
            )
            return self._snapshot

    def set_enabled(
        self,
        name: str,
        enabled: bool,
        *,
        expected_revision: int | None = None,
    ) -> SkillSnapshot:
        with self._write_lock:
            current = self._snapshot
            skill = next(
                (item for item in current.skills if item.name == name),
                None,
            )
            if skill is None:
                raise KeyError(name)
            if self.store is not None:
                record = self.store.get_skill(name)
                if record is None:
                    raise KeyError(name)
                if (
                    expected_revision is not None
                    and record.revision != expected_revision
                ):
                    raise RevisionConflictError(
                        f"Skill revision conflict: {name} expected {expected_revision}"
                    )
                self.store.update_skill(
                    name,
                    expected_revision=(
                        record.revision
                        if expected_revision is None
                        else expected_revision
                    ),
                    enabled=enabled,
                    load_state=(
                        "disabled"
                        if not enabled
                        else (
                            "dependency_unavailable"
                            if skill.missing_dependencies
                            else "loaded"
                        )
                    ),
                )
            self._enabled_overrides[name] = enabled
            skills = tuple(
                item.model_copy(update={"enabled": enabled})
                if item.name == name
                else item
                for item in current.skills
            )
            self._snapshot = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "skills": skills,
                    "loaded_at": datetime.now(UTC),
                    "stale": False,
                    "last_error": None,
                }
            )
            return self._snapshot

    def get(self, name: str) -> SkillDefinition | None:
        return next(
            (item for item in self._snapshot.skills if item.name == name),
            None,
        )

    def enabled(self) -> tuple[SkillDefinition, ...]:
        return tuple(item for item in self._snapshot.skills if item.enabled)

    def lightweight_catalog(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "name": item.name,
                "version": item.version,
                "description": item.description,
                "enabled": item.enabled,
                "dependency_state": item.dependency_state,
            }
            for item in self.enabled()
        )

    def _load_candidates(self) -> tuple[SkillDefinition, ...]:
        skills: list[SkillDefinition] = []
        names: set[str] = set()
        for path in sorted(self.root.glob("*/SKILL.md")):
            resolved = path.resolve()
            try:
                resolved.relative_to(self.root)
            except ValueError as exc:
                raise SkillParseError("skill_path_outside_root") from exc
            skill = self.parser.parse_file(resolved)
            if skill.name != path.parent.name:
                raise SkillParseError("skill_directory_name_mismatch")
            if skill.name in names:
                raise SkillParseError("skill_name_duplicate")
            names.add(skill.name)
            missing = tuple(
                item.key
                for item in skill.dependencies
                if item.required and not self.dependency_resolver(item)
            )
            persisted = (
                None
                if self.store is None
                else self.store.get_skill(skill.name)
            )
            enabled = self._enabled_overrides.get(
                skill.name,
                persisted.enabled if persisted is not None else skill.enabled,
            )
            skills.append(
                skill.model_copy(
                    update={
                        "enabled": enabled,
                        "dependency_state": (
                            "dependency_unavailable" if missing else "available"
                        ),
                        "missing_dependencies": missing,
                    }
                )
            )
        return tuple(skills)

    def _persist_candidates(
        self,
        candidates: tuple[SkillDefinition, ...],
    ) -> None:
        if self.store is None:
            return
        now = datetime.now(UTC)
        for skill in candidates:
            state = (
                "disabled"
                if not skill.enabled
                else (
                    "dependency_unavailable"
                    if skill.missing_dependencies
                    else "loaded"
                )
            )
            dependencies = tuple(item.key for item in skill.dependencies)
            record = self.store.get_skill(skill.name)
            if record is None:
                self.store.create_skill(
                    SkillRecord(
                        name=skill.name,
                        source_path=skill.source_path,
                        version=skill.version,
                        updated_at=now,
                        enabled=skill.enabled,
                        load_state=state,
                        snapshot_hash=skill.snapshot_hash,
                        dependencies=dependencies,
                    )
                )
            else:
                self.store.update_skill(
                    skill.name,
                    expected_revision=record.revision,
                    source_path=skill.source_path,
                    version=skill.version,
                    updated_at=now,
                    enabled=skill.enabled,
                    load_state=state,
                    snapshot_hash=skill.snapshot_hash,
                    dependencies=dependencies,
                    last_error=None,
                )

    def _mark_stale(self, exc: Exception) -> SkillSnapshot:
        error = str(exc)[:2_000]
        if self.store is not None:
            for skill in self._snapshot.skills:
                try:
                    record = self.store.get_skill(skill.name)
                    if record is None:
                        continue
                    self.store.update_skill(
                        skill.name,
                        expected_revision=record.revision,
                        load_state="stale",
                        last_error=error,
                    )
                except Exception:
                    pass
        with self._write_lock:
            current = self._snapshot
            self._snapshot = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "loaded_at": datetime.now(UTC),
                    "stale": True,
                    "last_error": error,
                }
            )
            return self._snapshot
