from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sqlite3
from threading import Lock, RLock
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml

from starter_agent.capabilities.models import (
    BoundedJsonObject,
    Identifier,
    Sha256,
    UtcDateTime,
    canonical_json_sha256,
)
from starter_agent.delegation.models import BudgetLimits, FailureBehavior, Reference
from starter_agent.mcp.config import contains_high_confidence_secret


DependencyResolver = Callable[[str], bool]


class SpecialistRegistryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SpecialistDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    specialist_id: Identifier
    version: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    disabled_reason: str | None = Field(default=None, min_length=1, max_length=500)
    system_prompt_ref: Reference
    system_prompt: str = Field(min_length=1, max_length=100_000)
    prompt_hash: Sha256
    prompt_version: str = Field(min_length=1, max_length=100)
    capability_tags: tuple[Identifier, ...] = Field(min_length=1, max_length=64)
    input_schema: BoundedJsonObject
    output_schema: BoundedJsonObject
    input_schema_hash: Sha256
    output_schema_hash: Sha256
    schema_version: str = Field(min_length=1, max_length=100)
    allowed_tools: tuple[Identifier, ...] = Field(max_length=64)
    allowed_knowledge_scope_types: tuple[Identifier, ...] = Field(max_length=32)
    allowed_artifact_types: tuple[Identifier, ...] = Field(max_length=32)
    default_budget: BudgetLimits
    max_budget: BudgetLimits
    max_steps: int = Field(ge=1, le=1_000)
    max_concurrency: int = Field(ge=1, le=100)
    default_deadline_ms: int = Field(ge=1, le=86_400_000)
    per_step_timeout_ms: int = Field(ge=1, le=3_600_000)
    retry_policy: BoundedJsonObject
    failure_behavior: FailureBehavior
    dependency_requirements: tuple[Identifier, ...] = Field(max_length=64)
    dependency_state: Literal["available", "dependency_unavailable"] = "available"
    missing_dependencies: tuple[Identifier, ...] = Field(default_factory=tuple)
    definition_hash: Sha256

    @model_validator(mode="after")
    def validate_limits(self) -> "SpecialistDefinition":
        for dimension in BudgetLimits.model_fields:
            if getattr(self.default_budget, dimension) > getattr(
                self.max_budget, dimension
            ):
                raise ValueError("default budget cannot exceed maximum budget")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("allowed_tools must be unique")
        if "delegate_task" in self.allowed_tools:
            raise ValueError("specialists cannot recursively delegate")
        return self


class SpecialistSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int = Field(ge=0)
    definitions: tuple[SpecialistDefinition, ...]
    loaded_at: UtcDateTime
    snapshot_hash: Sha256


class SQLiteSpecialistRegistryStore:
    """Persists published snapshots and disable-only administrative overrides."""

    def __init__(self, database_url: str, project_root: Path) -> None:
        if not database_url.startswith("sqlite:///"):
            raise ValueError("specialist registry store requires SQLite")
        raw = database_url.removeprefix("sqlite:///")
        path = Path(raw)
        if not path.is_absolute():
            path = Path(project_root) / path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self._lock = RLock()
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA busy_timeout=5000")
            self._connection.executescript(
                """
            CREATE TABLE IF NOT EXISTS delegation_specialist_snapshots (
                snapshot_hash TEXT PRIMARY KEY,
                revision INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                is_current INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_current_specialist_snapshot
            ON delegation_specialist_snapshots(is_current) WHERE is_current = 1;
            CREATE TABLE IF NOT EXISTS delegation_specialist_overrides (
                specialist_id TEXT PRIMARY KEY,
                disabled INTEGER NOT NULL,
                reason TEXT NOT NULL
            );
            """
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def save_snapshot(self, snapshot: SpecialistSnapshot) -> None:
        with self._lock:
            with self._connection:
                self._connection.execute(
                    "UPDATE delegation_specialist_snapshots SET is_current = 0 WHERE is_current = 1"
                )
                self._connection.execute(
                """INSERT INTO delegation_specialist_snapshots
                   (snapshot_hash, revision, payload_json, is_current)
                   VALUES (?, ?, ?, 1)
                   ON CONFLICT(snapshot_hash) DO UPDATE SET
                     revision=excluded.revision,
                     payload_json=excluded.payload_json,
                     is_current=1""",
                    (snapshot.snapshot_hash, snapshot.revision, snapshot.model_dump_json()),
                )

    def get_snapshot(self, snapshot_hash: str | None = None) -> SpecialistSnapshot | None:
        with self._lock:
            if snapshot_hash is None:
                row = self._connection.execute(
                    "SELECT payload_json FROM delegation_specialist_snapshots WHERE is_current = 1"
                ).fetchone()
            else:
                row = self._connection.execute(
                    "SELECT payload_json FROM delegation_specialist_snapshots WHERE snapshot_hash = ?",
                    (snapshot_hash,),
                ).fetchone()
        return None if row is None else SpecialistSnapshot.model_validate_json(row[0])

    def set_disabled(self, specialist_id: str, reason: str) -> None:
        with self._lock:
            with self._connection:
                self._connection.execute(
                """INSERT INTO delegation_specialist_overrides
                   (specialist_id, disabled, reason) VALUES (?, 1, ?)
                   ON CONFLICT(specialist_id) DO UPDATE SET disabled=1, reason=excluded.reason""",
                    (specialist_id, reason),
                )

    def clear_disabled(self, specialist_id: str) -> None:
        with self._lock:
            with self._connection:
                self._connection.execute(
                    "DELETE FROM delegation_specialist_overrides WHERE specialist_id = ?",
                    (specialist_id,),
                )

    def disabled_reason(self, specialist_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT reason FROM delegation_specialist_overrides WHERE specialist_id = ? AND disabled = 1",
                (specialist_id,),
            ).fetchone()
        return None if row is None else str(row[0])


class SpecialistRegistry:
    """Atomically publishes immutable, versioned specialist definitions."""

    def __init__(
        self,
        root: Path,
        *,
        project_root: Path,
        dependency_resolver: DependencyResolver | None = None,
        store: SQLiteSpecialistRegistryStore | None = None,
    ) -> None:
        self.root = root.resolve()
        self.project_root = project_root.resolve()
        self.dependency_resolver = dependency_resolver or (lambda _dependency: False)
        self.store = store
        self._lock = Lock()
        self._disabled_overrides: dict[str, str] = {}
        self._current: SpecialistSnapshot | None = None
        self._history: dict[str, SpecialistSnapshot] = {}

    def reload(self) -> SpecialistSnapshot:
        try:
            paths = sorted((*self.root.glob("*.yaml"), *self.root.glob("*.json")))
            definitions = tuple(self._load_definition(path) for path in paths)
            if not definitions:
                raise ValueError("specialist registry contains no definitions")
            ids = [item.specialist_id for item in definitions]
            if len(ids) != len(set(ids)):
                raise ValueError("specialist IDs must be unique")
        except Exception as exc:
            raise SpecialistRegistryError(
                "specialist_registry_invalid", f"specialist reload failed: {exc}"
            ) from exc
        snapshot_hash = canonical_json_sha256(
            [item.model_dump(mode="json") for item in definitions]
        )
        with self._lock:
            revision = 1 if self._current is None else self._current.revision + 1
            snapshot = SpecialistSnapshot(
                revision=revision,
                definitions=definitions,
                loaded_at=datetime.now(UTC),
                snapshot_hash=snapshot_hash,
            )
            if self.store is not None:
                self.store.save_snapshot(snapshot)
            self._current = snapshot
            self._history[snapshot_hash] = snapshot
            return snapshot

    def snapshot(self, snapshot_hash: str | None = None) -> SpecialistSnapshot:
        if snapshot_hash is not None:
            snapshot = self._history.get(snapshot_hash)
            if snapshot is None and self.store is not None:
                snapshot = self.store.get_snapshot(snapshot_hash)
                if snapshot is not None:
                    if snapshot.snapshot_hash != snapshot_hash:
                        raise SpecialistRegistryError(
                            "specialist_registry_invalid",
                            "persisted snapshot key does not match payload hash",
                        )
                    self._validate_snapshot_integrity(snapshot)
                    self._history[snapshot_hash] = snapshot
        else:
            snapshot = self._current
        if snapshot is None:
            raise SpecialistRegistryError(
                "specialist_registry_stale", "specialist registry is not loaded"
            )
        return snapshot

    def set_disabled(self, specialist_id: str, *, reason: str) -> None:
        if not reason.strip():
            raise ValueError("disabled reason is required")
        self._disabled_overrides[specialist_id] = reason.strip()
        if self.store is not None:
            self.store.set_disabled(specialist_id, reason.strip())

    def clear_disabled(self, specialist_id: str) -> None:
        self._disabled_overrides.pop(specialist_id, None)
        if self.store is not None:
            self.store.clear_disabled(specialist_id)

    def resolve(
        self,
        specialist_id: str,
        *,
        required_capabilities: tuple[str, ...] = (),
        inputs: dict[str, Any] | None = None,
        requested_budget: BudgetLimits | None = None,
        snapshot_hash: str | None = None,
    ) -> SpecialistDefinition:
        definition, _snapshot = self.resolve_with_snapshot(
            specialist_id,
            required_capabilities=required_capabilities,
            inputs=inputs,
            requested_budget=requested_budget,
            snapshot_hash=snapshot_hash,
        )
        return definition

    def resolve_with_snapshot(
        self,
        specialist_id: str,
        *,
        required_capabilities: tuple[str, ...] = (),
        inputs: dict[str, Any] | None = None,
        requested_budget: BudgetLimits | None = None,
        snapshot_hash: str | None = None,
    ) -> tuple[SpecialistDefinition, SpecialistSnapshot]:
        """Resolve and validate against one immutable Registry snapshot."""

        snapshot = self.snapshot(snapshot_hash)
        definition = next(
            (item for item in snapshot.definitions if item.specialist_id == specialist_id),
            None,
        )
        if definition is None:
            raise SpecialistRegistryError(
                "specialist_not_found", f"specialist not found: {specialist_id}"
            )
        if not set(required_capabilities).issubset(definition.capability_tags):
            raise SpecialistRegistryError(
                "specialist_capability_mismatch",
                f"specialist lacks required capabilities: {required_capabilities}",
            )
        if inputs is not None:
            try:
                Draft202012Validator(
                    definition.input_schema, format_checker=FormatChecker()
                ).validate(inputs)
            except JsonSchemaValidationError as exc:
                raise SpecialistRegistryError(
                    "specialist_schema_invalid", f"specialist input invalid: {exc.message}"
                ) from exc
        missing_dependencies: list[str] = []
        for dependency in definition.dependency_requirements:
            try:
                healthy = self.dependency_resolver(dependency)
            except Exception:
                healthy = False
            if not healthy:
                missing_dependencies.append(dependency)
        if missing_dependencies:
            raise SpecialistRegistryError(
                "specialist_dependency_unavailable",
                "specialist dependencies unavailable: "
                + ", ".join(missing_dependencies),
            )
        override_reason = self._disabled_overrides.get(specialist_id)
        if override_reason is None and self.store is not None:
            override_reason = self.store.disabled_reason(specialist_id)
        if not definition.enabled or override_reason is not None:
            reason = override_reason or definition.disabled_reason or "disabled by definition"
            raise SpecialistRegistryError(
                "specialist_disabled", f"specialist disabled: {reason}"
            )
        if requested_budget is not None:
            for dimension in BudgetLimits.model_fields:
                if getattr(requested_budget, dimension) > getattr(
                    definition.max_budget, dimension
                ):
                    raise SpecialistRegistryError(
                        "specialist_budget_exceeded",
                        f"requested {dimension} exceeds specialist maximum",
                    )
        return definition, snapshot

    def resolve_pinned(
        self,
        specialist_id: str,
        *,
        snapshot_hash: str,
    ) -> SpecialistDefinition:
        snapshot = self.snapshot(snapshot_hash)
        definition = next(
            (item for item in snapshot.definitions if item.specialist_id == specialist_id),
            None,
        )
        if definition is None:
            raise SpecialistRegistryError(
                "specialist_not_found", f"specialist not found: {specialist_id}"
            )
        return definition

    def _validate_snapshot_integrity(self, snapshot: SpecialistSnapshot) -> None:
        for definition in snapshot.definitions:
            prompt_hash = canonical_json_sha256(definition.system_prompt)
            input_hash = canonical_json_sha256(definition.input_schema)
            output_hash = canonical_json_sha256(definition.output_schema)
            if (
                definition.prompt_hash != prompt_hash
                or definition.input_schema_hash != input_hash
                or definition.output_schema_hash != output_hash
            ):
                raise SpecialistRegistryError(
                    "specialist_registry_invalid", "persisted definition hash mismatch"
                )
            material = definition.model_dump(mode="json", exclude={"definition_hash"})
            if definition.definition_hash != canonical_json_sha256(material):
                raise SpecialistRegistryError(
                    "specialist_registry_invalid", "persisted definition was modified"
                )
            try:
                self._validate_role_boundaries(definition)
            except ValueError as exc:
                raise SpecialistRegistryError(
                    "specialist_registry_invalid", str(exc)
                ) from exc
        material = [item.model_dump(mode="json") for item in snapshot.definitions]
        if snapshot.snapshot_hash != canonical_json_sha256(material):
            raise SpecialistRegistryError(
                "specialist_registry_invalid", "persisted snapshot hash mismatch"
            )

    def _load_definition(self, path: Path) -> SpecialistDefinition:
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
        if not isinstance(raw, dict):
            raise ValueError(f"specialist definition must be an object: {path.name}")
        specialist_id = raw.get("specialist_id")
        if path.stem != specialist_id:
            raise ValueError("specialist file name must match specialist_id")
        prompt_path = self._resolve_project_path(raw.pop("system_prompt_ref"))
        prompt = prompt_path.read_text(encoding="utf-8")
        input_schema = raw.get("input_schema")
        output_schema = raw.get("output_schema")
        if not isinstance(input_schema, dict) or not isinstance(output_schema, dict):
            raise ValueError("input_schema and output_schema must be objects")
        try:
            Draft202012Validator.check_schema(input_schema)
            Draft202012Validator.check_schema(output_schema)
        except SchemaError as exc:
            raise ValueError(f"invalid JSON Schema: {exc.message}") from exc
        dependencies = tuple(raw.get("dependency_requirements", ()))
        material = {
            **raw,
            "system_prompt_ref": prompt_path.relative_to(self.project_root).as_posix(),
            "system_prompt": prompt,
            "prompt_hash": canonical_json_sha256(prompt),
            "input_schema_hash": canonical_json_sha256(input_schema),
            "output_schema_hash": canonical_json_sha256(output_schema),
            "dependency_state": "available",
            "missing_dependencies": (),
        }
        material["definition_hash"] = "0" * 64
        definition = SpecialistDefinition.model_validate(material)
        definition = definition.model_copy(
            update={
                "definition_hash": canonical_json_sha256(
                    definition.model_dump(mode="json", exclude={"definition_hash"})
                )
            }
        )
        self._validate_role_boundaries(definition)
        return definition

    @staticmethod
    def _validate_role_boundaries(definition: SpecialistDefinition) -> None:
        tools = set(definition.allowed_tools)
        if definition.specialist_id == "job_web_researcher":
            forbidden = {"retrieve_resume_evidence", "delegate_task"}
            if tools & forbidden or definition.allowed_knowledge_scope_types:
                raise ValueError("job_web_researcher may only use Search/Browser tools")
            if not tools or any(
                tool != "search_jobs_serpapi" and "playwright" not in tool
                for tool in tools
            ):
                raise ValueError("job_web_researcher may only use Search/Browser tools")
        elif definition.specialist_id == "profile_evidence_analyst":
            if tools != {"retrieve_resume_evidence"}:
                raise ValueError(
                    "profile_evidence_analyst may only use authorized RAG"
                )

    def _resolve_project_path(self, reference: object) -> Path:
        if not isinstance(reference, str) or not reference:
            raise ValueError("system_prompt_ref must be a project-relative path")
        path = (self.project_root / reference).resolve()
        prompt_root = (self.project_root / "config" / "specialists" / "prompts").resolve()
        try:
            path.relative_to(prompt_root)
        except ValueError as exc:
            raise ValueError("system_prompt_ref escapes specialist prompt root") from exc
        if not path.is_file():
            raise ValueError(f"system prompt not found: {reference}")
        prompt = path.read_text(encoding="utf-8")
        if (
            contains_high_confidence_secret(prompt)
            or re.search(
                r"(?:authorization|api[_-]?key|password|secret|token)\s*[=:]\s*\S+",
                prompt,
                flags=re.IGNORECASE,
            )
        ):
            raise ValueError("system prompt contains a possible secret")
        return path
