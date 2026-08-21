from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from starter_agent.delegation.context import RunContext
from starter_agent.delegation.models import RunOutcome, RunSpec


_PROFILE_SCHEMA_VERSION = "profile-evidence-output-v1"
_AUTHORIZED_SCOPE = "resume"


class _Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)


class _Match(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_ref: str = Field(min_length=1)
    match_status: Literal["matched", "partial", "missing", "conflict"]
    evidence_strength: Literal["strong", "moderate", "weak", "none"]
    evidence: list[_Evidence] = Field(default_factory=list)


class _ProfileOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[_Match]
    missing: list[Any]
    conflicts: list[Any]


@dataclass(frozen=True, slots=True)
class ProfileEvidenceResult:
    outcome: RunOutcome
    output: dict[str, Any]


class ProfileEvidenceAnalyst:
    """Evidence-only specialist adapter over the shared AgentRuntime loop."""

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    async def run(
        self,
        spec: RunSpec,
        context: RunContext,
        inputs: Mapping[str, Any],
        *,
        on_tool_artifact=None,
    ) -> ProfileEvidenceResult:
        denied = self._scope_denial(inputs, context)
        if denied is not None:
            self._audit_scope_denial(context, denied)
            return ProfileEvidenceResult(self._failed(spec, denied), self._empty(denied))

        retrieved: dict[str, set[str]] = {}

        async def observe(event: dict[str, Any]) -> None:
            if event.get("type") != "tool_completed" or event.get("name") != "retrieve_resume_evidence":
                return
            if not event.get("ok"):
                return
            for item in event.get("evidence_refs", []):
                if not isinstance(item, dict):
                    continue
                chunk_id, source_ref = item.get("chunk_id"), item.get("source_ref")
                if isinstance(chunk_id, str) and isinstance(source_ref, str):
                    retrieved.setdefault(chunk_id, set()).add(source_ref)

        outcome = await self.runtime.run(
            spec=spec.model_copy(update={"allowed_tools": ("retrieve_resume_evidence",)}),
            context=context,
            on_tool_event=observe,
            **({"on_tool_artifact": on_tool_artifact} if on_tool_artifact is not None else {}),
        )
        if outcome.status != "succeeded":
            return ProfileEvidenceResult(outcome, self._empty(outcome.status))
        try:
            parsed = _ProfileOutput.model_validate_json(context.output_buffer[-1])
        except (IndexError, ValidationError, ValueError):
            return ProfileEvidenceResult(
                self._failed(spec, "profile_output_schema_invalid"),
                self._empty("profile_output_schema_invalid"),
            )
        requested_chunks = set(self._candidate_chunks(inputs))
        allowed_chunks = requested_chunks or set(retrieved)
        error = self._validate_evidence(parsed, retrieved, allowed_chunks)
        if error is not None:
            return ProfileEvidenceResult(self._failed(spec, error), self._empty(error))
        return ProfileEvidenceResult(outcome, parsed.model_dump(mode="json"))

    @staticmethod
    def _scope_denial(inputs: Mapping[str, Any], context: RunContext) -> str | None:
        scope = inputs.get("knowledge_scope")
        scope_type = scope.get("type") if isinstance(scope, Mapping) else None
        if scope_type != _AUTHORIZED_SCOPE or context.knowledge_scope != _AUTHORIZED_SCOPE:
            return "profile_knowledge_scope_forbidden"
        if context.knowledge_base_id is None or context.user_id is None or context.project_id is None:
            return "profile_knowledge_scope_unavailable"
        if inputs.get("output_schema_version") != _PROFILE_SCHEMA_VERSION:
            return "profile_schema_version_unsupported"
        if not isinstance(inputs.get("normalized_job_requirements_ref"), str):
            return "profile_requirements_reference_required"
        return None

    @staticmethod
    def _candidate_chunks(inputs: Mapping[str, Any]) -> tuple[str, ...]:
        value = inputs.get("candidate_chunk_ids", inputs.get("chunk_refs", ()))
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(item for item in value if isinstance(item, str) and item)

    @staticmethod
    def _validate_evidence(
        output: _ProfileOutput,
        retrieved: Mapping[str, set[str]],
        allowed_chunks: set[str],
    ) -> str | None:
        for match in output.matches:
            positive = match.match_status in {"matched", "partial"}
            if positive and not match.evidence:
                return "profile_evidence_unbound"
            for evidence in match.evidence:
                if evidence.chunk_id not in allowed_chunks:
                    return "profile_evidence_unbound"
                if evidence.source_ref not in retrieved.get(evidence.chunk_id, set()):
                    return "profile_evidence_unbound"
            if not positive and match.evidence:
                return "profile_evidence_unbound"
        return None

    @staticmethod
    def _failed(spec: RunSpec, code: str) -> RunOutcome:
        return RunOutcome(disposition="failed", run_id=spec.run_id, status="failed", error_code=code)

    @staticmethod
    def _empty(reason: str) -> dict[str, Any]:
        return {"matches": [], "missing": [{"reason": reason}], "conflicts": []}

    def _audit_scope_denial(self, context: RunContext, code: str) -> None:
        audit = getattr(self.runtime, "_append_audit", None)
        if callable(audit):
            audit(
                action="policy.profile_knowledge_scope",
                target="tool:retrieve_resume_evidence",
                decision="deny",
                reason_code=code,
                session_id=str(context.session_id),
                turn_id=str(context.turn_id),
                call_id=f"profile-scope:{context.run_id}",
                payload={
                    "parent_run_id": context.parent_run_id,
                    "child_task_id": context.child_task_id,
                    "child_run_id": context.trace_context.child_run_id or context.run_id,
                    "principal": context.principal,
                    "access_level": "child_restricted",
                    "policy_decision_id": (
                        context.trace_context.policy_decision_id
                        or f"policy:profile-scope:{context.run_id}"
                    ),
                    "approval_id": context.trace_context.approval_id,
                },
            )
