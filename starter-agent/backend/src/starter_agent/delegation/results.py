"""Fail-closed Child result validation and deterministic fact merging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from jsonschema import Draft202012Validator, FormatChecker

from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.delegation.models import BudgetLimits, ChildRun, ChildTask, MergeReport, ParentRun, ResultEnvelope
from starter_agent.delegation.registry import SpecialistRegistry, SpecialistRegistryError
from starter_agent.delegation.store import (
    CandidateMergeWrite,
    ParentMergeFinalization,
    ResultRepairCompletion,
    SQLiteRunStore,
    ValidatedResultAcceptance,
)


@dataclass(frozen=True, slots=True)
class ValidationContext:
    parent: ParentRun
    task: ChildTask
    child: ChildRun
    envelope_ref: str
    principal: str
    authorized_artifact_refs: frozenset[str] = frozenset()
    authorized_source_urls: frozenset[str] = frozenset()
    authorized_chunk_ids: frozenset[str] = frozenset()
    authorized_chunk_source_pairs: frozenset[tuple[str, str]] = frozenset()
    ledger_limit: BudgetLimits | None = None
    max_envelope_bytes: int = 1_000_000


@dataclass(frozen=True, slots=True)
class ValidatedEnvelope:
    envelope: ResultEnvelope
    envelope_ref: str
    envelope_hash: str
    parent_run_id: str
    task_id: str
    child_run_id: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    accepted: bool
    code: str
    repair_allowed: bool = False
    envelope_hash: str | None = None
    validated: ValidatedEnvelope | None = None


@dataclass(frozen=True, slots=True)
class MergeResult:
    report: MergeReport
    final_output: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ParentMergeResult:
    """Reference-only outcome for the coordinator's validating phase."""

    status: str
    merge_report_id: str | None = None
    final_output_ref: str | None = None


class StructuredResultRepair:
    """Builds the bounded no-tool request for a single schema-only repair."""

    def request(self, *, output: Mapping[str, Any], schema: Mapping[str, Any], errors: tuple[str, ...]) -> dict[str, Any]:
        return {"invalid_output": dict(output), "output_schema": dict(schema), "errors": tuple(errors), "tools": ()}

    def repair_once(self, provider, *, output: Mapping[str, Any], schema: Mapping[str, Any], errors: tuple[str, ...]) -> tuple[dict[str, Any], "BudgetUsage"]:
        repaired, usage = provider(self.request(output=output, schema=schema, errors=errors))
        if usage.cost_status == "unknown":
            raise ValueError("repair usage cost unknown")
        if not isinstance(repaired, dict):
            raise ValueError("repair output must be an object")
        return repaired, usage


class ResultAcceptanceService:
    """Single production seam for accepting an already-persisted Child result."""

    def __init__(self, *, store: SQLiteRunStore, validator: "ResultValidator | None", repair: StructuredResultRepair | None = None, artifact_store=None) -> None:
        self.store = store
        self.validator = validator
        self.repair = repair or StructuredResultRepair()
        self.artifact_store = artifact_store

    def validate_and_accept(self, envelope: ResultEnvelope, context: ValidationContext | None = None, *, now: datetime, child_run_id: str | None = None, envelope_ref: str | None = None, merge_report: MergeReport | None = None, candidates: tuple[CandidateMergeWrite, ...] = ()) -> ValidationResult:
        if self.validator is None:
            raise ValueError("result_validator_required")
        if context is None:
            if child_run_id is None or envelope_ref is None:
                raise ValueError("result_acceptance_identity_required")
            context = self._context_from_store(child_run_id, envelope_ref)
        result = self.validator.validate(envelope, context)
        if not result.accepted or result.validated is None:
            return result
        # The Store's accepted-result CAS is the authority boundary.  Its Child
        # terminal/result hash preconditions reject late, duplicate and lease-lost runs.
        self.store.accept_validated_result(ValidatedResultAcceptance(
            child_run_id=context.child.id, envelope_ref=context.envelope_ref,
            envelope_hash=result.envelope_hash or envelope.canonical_hash,
            usage=envelope.usage, expected_parent_version=context.parent.version,
            expected_task_version=context.task.version, accepted_at=now, merge_report=merge_report, candidates=candidates,
        ))
        return result

    def merge_report_for(self, *, parent_run_id: str, result_version: int, envelopes: tuple[ValidatedEnvelope, ...], now: datetime) -> MergeResult:
        """Create the report from already-validated references only."""
        return DeterministicResultMerger().merge(parent_run_id=parent_run_id, result_version=result_version, envelopes=envelopes, created_at=now)

    def merge_ready_parent(self, parent_run_id: str, *, expected_version: int, now: datetime) -> ParentMergeResult:
        """Finalize only DB-authorized, accepted envelope artifacts.

        Callers supply no results or completion flags: readiness is derived from
        ChildTask acceptance and terminal ChildRun state under the Parent.
        """
        tree = self.store.get_run_tree(parent_run_id)
        parent = tree.parent
        if parent.merge_report_id is not None:
            report = next((item for item in tree.merge_reports if item.id == parent.merge_report_id), None)
            return ParentMergeResult("merged", parent.merge_report_id, None if report is None else report.final_output_ref)
        if parent.status in {"cancelling", "cancelled"}:
            raise ValueError("result_parent_not_mergeable")
        if parent.status != "running" or parent.phase != "validating":
            return ParentMergeResult("waiting")
        if parent.version != expected_version:
            raise ValueError("merge_conflict: parent version changed")

        runs = {item.id: item for item in tree.child_runs}
        terminal = {"succeeded", "partial", "failed", "timed_out", "budget_exhausted", "cancelled"}
        missing: list[str] = []
        rejected: list[dict[str, str]] = []
        partial = False
        accepted: list[tuple[ChildTask, ChildRun]] = []
        # First determine readiness entirely from durable task/run state.  A
        # waiting Parent must not require a configured artifact reader yet.
        if not tree.child_tasks:
            return ParentMergeResult("waiting")
        for task in tree.child_tasks:
            if task.accepted_child_run_id is not None and task.accepted_result_envelope_ref is not None:
                child = runs.get(task.accepted_child_run_id)
                if child is None or child.result_hash != task.accepted_result_hash:
                    raise ValueError("accepted_result_authority_invalid")
                accepted.append((task, child))
                if child.status == "partial":
                    partial = True
                continue
            task_runs = [item for item in tree.child_runs if item.child_task_id == task.id]
            latest = max(task_runs, key=lambda item: (item.attempt, item.created_at, item.id), default=None)
            if latest is None or latest.status not in terminal or latest.status in {"succeeded", "partial"}:
                return ParentMergeResult("waiting")
            if task.failure_behavior == "fail_parent":
                self.store.set_parent_phase(parent.id, phase="terminal", expected_version=parent.version, occurred_at=now, terminal_status="failed")
                return ParentMergeResult("failed")
            if task.failure_behavior == "wait_for_user":
                return ParentMergeResult("waiting")
            partial = True
            missing.append(f"task:{task.id}")
            rejected.append({"task_id": task.id, "child_run_id": latest.id, "error_code": latest.error_code or latest.status})

        if self.artifact_store is None or self.validator is None:
            raise ValueError("result_merge_authority_unavailable")
        validated: list[ValidatedEnvelope] = []
        for task, child in accepted:
            artifact = self.artifact_store.get_tool_artifact_for_principal(task.accepted_result_envelope_ref, principal=parent.principal)
            if artifact is None or not isinstance(artifact.get("content"), str):
                raise ValueError("accepted_result_artifact_unavailable")
            envelope = ResultEnvelope.model_validate_json(artifact["content"])
            validation = self.validator.validate(envelope, self._context_from_store(child.id, task.accepted_result_envelope_ref))
            if not validation.accepted or validation.validated is None:
                raise ValueError("accepted_result_revalidation_failed")
            validated.append(validation.validated)
        merged = self.merge_report_for(parent_run_id=parent.id, result_version=parent.result_version + 1, envelopes=tuple(validated), now=now)
        final_output = {**merged.final_output, "missing": sorted(set(merged.final_output["missing"]) | set(missing))}
        final_hash = canonical_json_sha256(final_output)
        report_seed = {"parent": parent.id, "version": parent.result_version + 1, "inputs": list(merged.report.input_hashes), "missing": final_output["missing"], "rejected": rejected}
        report = merged.report.model_copy(update={
            "id": f"merge:{canonical_json_sha256(report_seed)[:32]}",
            "missing": tuple(final_output["missing"]),
            "rejected": tuple([*merged.report.rejected, *rejected]),
            "final_output_ref": f"artifact:merged:{final_hash}", "final_output_hash": final_hash,
        })
        candidates = tuple(
            CandidateMergeWrite(
                id=f"candidate-merge:{parent.id}:{canonical_json_sha256(job)[:24]}",
                parent_run_id=parent.id, candidate_key=f"candidate:{canonical_json_sha256(job)[:32]}",
                payload_hash=canonical_json_sha256(job), payload=job,
                idempotency_key=f"candidate:{canonical_json_sha256(job)[:32]}",
                expected_parent_version=parent.version, created_at=now,
            )
            for job in final_output.get("jobs", [])
        )
        self.store.finalize_parent_merge(ParentMergeFinalization(
            parent_run_id=parent.id, expected_parent_version=parent.version,
            report=report, candidates=candidates,
            terminal_status="partial" if partial else "succeeded", occurred_at=now,
        ))
        return ParentMergeResult("merged", report.id, report.final_output_ref)

    def accept_pending_terminal(self, child_run_id: str, *, now: datetime) -> ValidationResult:
        """Crash-safe reaper for terminal Children with a bound envelope artifact."""
        child = self.store.get_child_run(child_run_id)
        if child is None or child.status not in {"succeeded", "partial"} or child.result_envelope_ref is None:
            raise ValueError("terminal_result_envelope_required")
        if self.artifact_store is None:
            raise ValueError("result_artifact_store_required")
        task = self.store.get_child_task(child.child_task_id)
        parent = self.store.get_parent(child.parent_run_id)
        if task is not None and task.accepted_child_run_id == child.id:
            return ValidationResult(True, "accepted", envelope_hash=child.result_hash)
        if parent is None:
            raise ValueError("result_parent_not_found")
        artifact = self.artifact_store.get_tool_artifact_for_principal(child.result_envelope_ref, principal=parent.principal)
        if artifact is None or not isinstance(artifact.get("content"), str):
            raise ValueError("result_envelope_artifact_unavailable")
        envelope = ResultEnvelope.model_validate_json(artifact["content"])
        return self.validate_and_accept(envelope, now=now, child_run_id=child.id, envelope_ref=child.result_envelope_ref)

    def repair_once(self, *, child_run_id: str, envelope_hash: str, output: Mapping[str, Any], schema: Mapping[str, Any], errors: tuple[str, ...], provider, expected_parent_version: int) -> tuple[dict[str, Any], "BudgetUsage"]:
        # Store begins the one permitted attempt and reserves its configured
        # budget dimensions
        # minimum before the Provider sees any material.
        self.store.begin_result_repair_attempt(child_run_id=child_run_id, envelope_hash=envelope_hash, expected_parent_version=expected_parent_version)
        usage = None
        try:
            repaired, usage = self.repair.repair_once(provider, output=output, schema=schema, errors=errors)
            return repaired, usage
        except Exception:
            self.store.complete_result_repair_attempt(ResultRepairCompletion(child_run_id=child_run_id, envelope_hash=envelope_hash, usage=usage, status="provider_failed", expected_parent_version=expected_parent_version, occurred_at=datetime.now(UTC)))
            raise
        finally:
            if usage is not None:
                self.store.complete_result_repair_attempt(ResultRepairCompletion(child_run_id=child_run_id, envelope_hash=envelope_hash, usage=usage, status="completed", expected_parent_version=expected_parent_version, occurred_at=datetime.now(UTC)))

    def _context_from_store(self, child_run_id: str, envelope_ref: str) -> ValidationContext:
        child = self.store.get_child_run(child_run_id)
        if child is None:
            raise ValueError("child_run_not_found")
        task = self.store.get_child_task(child.child_task_id)
        parent = self.store.get_parent(child.parent_run_id)
        if task is None or parent is None:
            raise ValueError("result_authority_not_found")
        tree = self.store.get_run_tree(parent.id)
        links = [item for item in tree.artifact_links if item.child_run_id == child.id]
        if not any(item.artifact_ref == envelope_ref and item.principal == parent.principal for item in links):
            raise ValueError("result_envelope_artifact_unauthorized")
        return ValidationContext(
            parent=parent, task=task, child=child, envelope_ref=envelope_ref,
            principal=parent.principal,
            authorized_artifact_refs=frozenset(item.artifact_ref for item in links if item.principal == parent.principal),
            authorized_source_urls=frozenset(item.source_url for item in links if item.principal == parent.principal and item.source_url),
            authorized_chunk_ids=frozenset(item.chunk_id for item in links if item.principal == parent.principal and item.chunk_id),
            authorized_chunk_source_pairs=frozenset(
                (item.chunk_id, item.artifact_ref)
                for item in links
                if item.principal == parent.principal and item.kind == "rag_evidence"
                and item.chunk_id is not None
            ),
            ledger_limit=task.requested_budget,
        )


class ResultValidator:
    """Checks the immutable task/snapshot boundary before Parent may reference it."""

    def __init__(self, registry: SpecialistRegistry) -> None:
        self.registry = registry

    def validate(self, envelope: ResultEnvelope, context: ValidationContext, *, repair_attempt: int = 0) -> ValidationResult:
        def reject(code: str, *, repair: bool = False) -> ValidationResult:
            return ValidationResult(False, code, repair_allowed=repair and repair_attempt == 0 and self._has_remaining_budget(context))

        if context.parent.status in {"cancelling", "cancelled", "succeeded", "partial", "failed", "timed_out", "budget_exhausted"}:
            return reject("result_parent_not_mergeable")
        if context.child.status not in {"succeeded", "partial"} or context.child.status != envelope.status:
            return reject("result_terminal_order_invalid")
        if context.child.parent_run_id != context.parent.id or context.task.parent_run_id != context.parent.id or context.child.child_task_id != context.task.id:
            return reject("result_parent_task_child_mismatch")
        if envelope.task_id != context.task.id or envelope.child_run_id != context.child.id:
            return reject("result_identity_mismatch")
        if not envelope.trace_ref.endswith(context.child.id):
            return reject("result_trace_mismatch")
        encoded = envelope.model_dump_json().encode("utf-8")
        if len(encoded) > context.max_envelope_bytes:
            return reject("result_size_exceeded")
        if envelope.usage.cost_status == "unknown":
            return reject("result_cost_unknown")
        if not self._within_budget(envelope, context):
            return reject("result_budget_exceeded")
        try:
            definition = self.registry.resolve_pinned(context.task.specialist_id, snapshot_hash=context.task.specialist_snapshot_id)
        except SpecialistRegistryError:
            return reject("result_registry_snapshot_invalid")
        if definition.schema_version != context.task.output_schema_version:
            return reject("result_schema_version_mismatch")
        try:
            # Pydantic freezes bounded JSON arrays as tuples; validate the JSON value
            # that crossed the Child boundary, not that in-memory representation.
            Draft202012Validator(definition.output_schema, format_checker=FormatChecker()).validate(
                json.loads(json.dumps(envelope.output))
            )
        except Exception:
            return reject("result_schema_invalid", repair=True)
        authority_error = self._validate_authority(envelope, context)
        if authority_error is not None:
            return reject(authority_error)
        validated = ValidatedEnvelope(envelope, context.envelope_ref, envelope.canonical_hash, context.parent.id, context.task.id, context.child.id)
        return ValidationResult(True, "accepted", envelope_hash=envelope.canonical_hash, validated=validated)

    @staticmethod
    def _has_remaining_budget(context: ValidationContext) -> bool:
        return context.ledger_limit is None or any(getattr(context.ledger_limit, field) > 0 for field in BudgetLimits.model_fields)

    @staticmethod
    def _within_budget(envelope: ResultEnvelope, context: ValidationContext) -> bool:
        limits = (context.task.requested_budget, context.ledger_limit)
        return all(limit is None or all(getattr(envelope.usage, name) <= getattr(limit, name) for name in BudgetLimits.model_fields) for limit in limits)

    @staticmethod
    def _validate_authority(envelope: ResultEnvelope, context: ValidationContext) -> str | None:
        artifact_refs: set[str] = set()
        source_refs: set[str] = set()
        source_urls: set[str] = set()
        chunk_ids: set[str] = set()
        chunk_source_pairs: set[tuple[str, str]] = set()

        def scan(value: Any, key: str | None = None) -> None:
            if isinstance(value, Mapping):
                pair_chunk, pair_source = value.get("chunk_id"), value.get("source_ref")
                if isinstance(pair_chunk, str) and isinstance(pair_source, str):
                    chunk_source_pairs.add((pair_chunk, pair_source))
                for child_key, child_value in value.items():
                    scan(child_value, str(child_key))
            elif isinstance(value, (list, tuple)):
                for child_value in value:
                    scan(child_value, key)
            elif isinstance(value, str):
                if key in {"artifact_ref", "artifact_refs"} and value.startswith("artifact:"):
                    artifact_refs.add(value)
                if key == "source_ref":
                    source_refs.add(value)
                if key in {"source_url", "final_url"} and value.strip():
                    normalized_url = _normalized_url(value)
                    source_urls.add(normalized_url)
                if key == "chunk_id":
                    chunk_ids.add(value)

        scan(envelope.output)
        scan(envelope.evidence)
        if artifact_refs and not context.authorized_artifact_refs:
            return "result_artifact_authority_missing"
        if artifact_refs and not artifact_refs.issubset(context.authorized_artifact_refs):
            return "result_artifact_unauthorized"
        if source_refs and not context.authorized_artifact_refs:
            return "result_source_ref_authority_missing"
        if source_refs and not source_refs.issubset(context.authorized_artifact_refs):
            return "result_source_ref_unauthorized"
        normalized_allowed = {_normalized_url(value) for value in context.authorized_source_urls}
        if source_urls and not context.authorized_source_urls:
            return "result_source_authority_missing"
        if source_urls and not source_urls.issubset(normalized_allowed):
            return "result_source_unauthorized"
        if chunk_ids and not context.authorized_chunk_ids:
            return "result_chunk_authority_missing"
        if chunk_ids and not chunk_ids.issubset(context.authorized_chunk_ids):
            return "result_chunk_unauthorized"
        if chunk_source_pairs and not context.authorized_chunk_source_pairs:
            return "result_chunk_source_authority_missing"
        if chunk_source_pairs and not chunk_source_pairs.issubset(context.authorized_chunk_source_pairs):
            return "result_chunk_source_unauthorized"
        return None


class DeterministicResultMerger:
    """A stable, facts-only merge. Semantic synthesis is deliberately disabled."""

    def merge(self, *, parent_run_id: str, result_version: int, envelopes: tuple[ValidatedEnvelope, ...], created_at: datetime | None = None) -> MergeResult:
        ordered = tuple(sorted(envelopes, key=lambda item: (item.envelope_ref, item.envelope_hash)))
        jobs: dict[str, dict[str, Any]] = {}
        profile_matches: list[dict[str, Any]] = []
        groups: dict[str, list[str]] = {}
        missing: set[str] = set()
        conflicts: list[dict[str, Any]] = []
        for item in ordered:
            missing.update(item.envelope.missing)
            output = item.envelope.output
            raw_matches = output.get("matches", []) if isinstance(output, Mapping) else []
            if isinstance(raw_matches, (list, tuple)):
                for raw_match in raw_matches:
                    if isinstance(raw_match, Mapping):
                        profile_matches.append({**dict(raw_match), "task_id": item.task_id, "child_run_id": item.child_run_id, "envelope_ref": item.envelope_ref})
            raw_jobs = output.get("jobs", []) if isinstance(output, Mapping) else []
            if not isinstance(raw_jobs, (list, tuple)):
                continue
            for raw in raw_jobs:
                if not isinstance(raw, Mapping):
                    continue
                job = dict(raw)
                key = self._job_key(job)
                groups.setdefault(key, []).append(item.envelope_ref)
                old = jobs.get(key)
                if old is None:
                    jobs[key] = {**job, "source_refs": [item.envelope_ref], "task_id": item.task_id, "child_run_id": item.child_run_id}
                    continue
                if any(old.get(field) != job.get(field) for field in ("title", "company", "location") if old.get(field) is not None and job.get(field) is not None):
                    conflicts.append({"key": key, "existing": {field: old.get(field) for field in ("title", "company", "location")}, "incoming": {field: job.get(field) for field in ("title", "company", "location")}, "source_ref": item.envelope_ref})
                old["source_refs"] = sorted(set(old["source_refs"] + [item.envelope_ref]))
        ordered_keys = tuple(sorted(jobs, key=lambda key: (-int(bool(jobs[key].get("validation_state") == "verified")), key)))
        profile_matches.sort(key=lambda value: (str(value.get("job_ref", "")), str(value.get("requirement_ref", "")), str(value.get("task_id", "")), str(value.get("child_run_id", ""))))
        source_count = sum(len(value.get("source_refs", ())) for value in jobs.values()) + sum(len(value.get("evidence", ())) for value in profile_matches)
        evidence_count = sum(len(value.get("evidence", ())) for value in profile_matches)
        final_output = {"jobs": [jobs[key] for key in ordered_keys], "profile_matches": profile_matches, "missing": sorted(missing), "conflicts": conflicts, "semantic_synthesis": "disabled"}
        final_hash = canonical_json_sha256(final_output)
        report = MergeReport(
            id=f"merge:{canonical_json_sha256({'parent': parent_run_id, 'version': result_version, 'inputs': [item.envelope_hash for item in ordered]})[:32]}",
            parent_run_id=parent_run_id, result_version=result_version,
            input_envelope_refs=tuple(item.envelope_ref for item in ordered), input_hashes=tuple(item.envelope_hash for item in ordered),
            accepted=tuple({"task_id": item.task_id, "child_run_id": item.child_run_id, "envelope_ref": item.envelope_ref} for item in ordered), rejected=(),
            dedup_groups=tuple({"key": key, "source_refs": sorted(refs)} for key, refs in sorted(groups.items()) if len(refs) > 1),
            missing=tuple(sorted(missing)), conflicts=tuple(conflicts), source_validation=({"accepted_count": source_count, "status": "validated"},), evidence_validation=({"authorized_count": evidence_count, "status": "validated"},),
            ranking_features={key: {"verified": jobs[key].get("validation_state") == "verified"} for key in ordered_keys}, deterministic_order=tuple(f"candidate:{canonical_json_sha256(key)[:32]}" for key in ordered_keys),
            semantic_synthesis_version="disabled", final_output_ref=f"artifact:merged:{final_hash}", final_output_hash=final_hash, created_at=created_at or datetime.now(UTC),
        )
        return MergeResult(report, final_output)

    @staticmethod
    def _job_key(job: Mapping[str, Any]) -> str:
        url = job.get("final_url") or job.get("source_url")
        if isinstance(url, str) and url:
            return _normalized_url(url)
        content_hash = job.get("content_hash")
        return f"content:{content_hash}" if isinstance(content_hash, str) else canonical_json_sha256(dict(job))


def _normalized_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") or "/", parsed.query, ""))
