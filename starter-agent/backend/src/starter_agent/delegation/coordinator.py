from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal, Mapping
from pydantic import ValidationError

from starter_agent.delegation.context import RunContext
from starter_agent.delegation.models import RunOutcome, RunSpec
from starter_agent.delegation.registry import SpecialistRegistryError
from starter_agent.delegation.store import CoordinatorCheckpoint, DelegateBatch, SQLiteRunStore
from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.domain.models import ToolCall
from starter_agent.delegation.service import CoordinatorTaskContract, DelegationService


class CoordinatorPhase(str, Enum):
    PLANNING = "planning"
    WAITING_CHILDREN = "waiting_children"
    VALIDATING = "validating"
    MERGING = "merging"
    TERMINAL = "terminal"


class CoordinatorError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class FailureResolution:
    status: Literal["waiting_for_user", "partial", "failed"]
    missing: tuple[str, ...]
    errors: tuple[dict[str, str], ...]
    accepted_envelope_refs: tuple[str, ...]
    facts: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EnvelopeAuthority:
    parent_run_id: str
    task_id: str
    child_run_id: str
    envelope_ref: str
    result_hash: str
    principal: str
    trace_ref: str


@dataclass(frozen=True, slots=True)
class EnvelopeValidation(EnvelopeAuthority):
    authorized: bool


def _validation_matches(validation: object, authority: EnvelopeAuthority) -> bool:
    return isinstance(validation, EnvelopeValidation) and validation.authorized and all(
        getattr(validation, name) == getattr(authority, name)
        for name in EnvelopeAuthority.__dataclass_fields__
    )


class Coordinator:
    """Persistent phase driver around the shared AgentRuntime.

    It does not navigate, retrieve resume evidence, or interpret failed fields.
    """

    tool_view = (
        "delegate_task",
        "inspect_delegated_results",
        "merge_delegated_results",
        "request_user_confirmation",
    )

    def __init__(
        self,
        *,
        store: SQLiteRunStore,
        now: Callable[[], datetime] | None = None,
        envelope_validator: Callable[[EnvelopeAuthority], EnvelopeValidation] | None = None,
    ) -> None:
        self.store = store
        self._now = now or (lambda: datetime.now(UTC))
        # Task14 supplies the full Result Validator.  Until then, resume is
        # fail-closed unless composition explicitly supplies a validator.
        self._validate_envelope = envelope_validator

    async def run(
        self,
        *,
        runtime: Any,
        spec: RunSpec,
        context: RunContext,
        on_tool_event: Callable[[dict[str, Any]], Any] | None = None,
    ) -> RunOutcome:
        if spec.role != "coordinator" or context.child_task_id is not None:
            raise ValueError("Coordinator requires a Parent RunSpec and RunContext")
        self.validate_tool_view(context.effective_tool_view)
        if self.store.get_coordinator_checkpoint(context.parent_run_id) is None:
            self.persist_planning_checkpoint(context)

        def suspend_after_batch(current: RunContext) -> str | None:
            tree = self.store.get_run_tree(current.parent_run_id)
            terminal = {"succeeded", "partial", "failed", "timed_out", "budget_exhausted", "cancelled"}
            if not tree.child_runs:
                return None
            checkpoint_ref = f"coordinator-checkpoint:{current.parent_run_id}:{tree.parent.version + 1}"
            current.suspension_checkpoint_ref = checkpoint_ref
            self.store.save_coordinator_checkpoint(
                CoordinatorCheckpoint(
                    parent_run_id=current.parent_run_id,
                    parent_version=tree.parent.version,
                    payload=current.to_checkpoint(),
                    created_at=self._now(),
                ),
                expected_parent_version=tree.parent.version,
                suspend=True,
                phase=CoordinatorPhase.WAITING_CHILDREN.value,
            )
            self.store.wake_parent_if_children_terminal(
                current.parent_run_id,
                occurred_at=self._now(),
            )
            return checkpoint_ref

        context.suspension_probe = suspend_after_batch
        context.delegate_batch_probe = lambda calls: self.record_delegate_batch(context.parent_run_id, calls, model_request_id=context.trace_context.model_request_id or f"model:{context.budget.consumed.model_calls}", context_checkpoint=context.to_checkpoint())
        context.delegate_call_completed_probe = lambda call_id, receipt: self.mark_delegate_call_completed(context.parent_run_id, call_id, receipt, context_checkpoint=context.to_checkpoint())
        try:
            return await runtime.run(
                spec=spec,
                context=context,
                **({"on_tool_event": on_tool_event} if on_tool_event is not None else {}),
            )
        finally:
            context.suspension_probe = None
            context.delegate_batch_probe = None
            context.delegate_call_completed_probe = None

    def validate_tool_view(self, names: Iterable[str]) -> None:
        if not set(names).issubset(self.tool_view):
            raise ValueError("coordinator_tool_view_forbidden")

    def persist_checkpoint(self, context: RunContext) -> str:
        parent = self.store.get_parent(context.parent_run_id)
        if parent is None:
            raise ValueError("parent_not_found")
        ref = f"coordinator-checkpoint:{parent.id}:{parent.version + 1}"
        context.suspension_checkpoint_ref = ref
        self.store.save_coordinator_checkpoint(
            CoordinatorCheckpoint(parent_run_id=parent.id, parent_version=parent.version, payload=context.to_checkpoint(), created_at=self._now()),
            expected_parent_version=parent.version,
            suspend=False,
            phase=parent.phase,
        )
        return ref

    def persist_planning_checkpoint(self, context: RunContext) -> str:
        parent = self.store.get_parent(context.parent_run_id)
        if parent is None or parent.status != "running" or parent.phase != CoordinatorPhase.PLANNING.value:
            raise ValueError("coordinator_parent_status_invalid")
        return self.persist_checkpoint(context)

    def reconcile_interrupted_planning(self, parent_run_id: str):
        parent = self.store.get_parent(parent_run_id)
        checkpoint = self.store.get_coordinator_checkpoint(parent_run_id)
        if parent is None or checkpoint is None:
            raise ValueError("coordinator_checkpoint_not_found")
        if parent.status != "running" or parent.phase != CoordinatorPhase.PLANNING.value:
            raise ValueError("coordinator_reconcile_not_required")
        tree = self.store.get_run_tree(parent_run_id)
        if not tree.child_runs:
            raise ValueError("coordinator_reconcile_no_children")
        batch = self.store.get_delegate_batch(parent_run_id)
        if batch is None:
            raise CoordinatorError("delegate_batch_ledger_missing")
        if len(batch.completed_call_ids) != len(batch.calls):
            raise CoordinatorError("delegate_batch_incomplete")
        if batch.context_checkpoint is None:
            raise CoordinatorError("delegate_batch_checkpoint_missing")
        context = RunContext.from_checkpoint(dict(batch.context_checkpoint))
        if context.parent_run_id != parent_run_id or context.principal != parent.principal:
            raise CoordinatorError("delegate_batch_authority_mismatch")
        durable = checkpoint.model_copy(update={"payload": context.to_checkpoint(), "parent_version": parent.version, "created_at": self._now()})
        return self.store.save_coordinator_checkpoint(
            durable,
            expected_parent_version=parent.version,
            suspend=True,
            phase=CoordinatorPhase.WAITING_CHILDREN.value,
        )

    def resume_context(self, parent_run_id: str) -> RunContext:
        try:
            checkpoint = self.store.get_coordinator_checkpoint(parent_run_id)
        except Exception as exc:
            code = getattr(exc, "code", None)
            raise CoordinatorError("coordinator_checkpoint_invalid" if code == "coordinator_checkpoint_invalid" else "coordinator_checkpoint_schema_unsupported") from exc
        if checkpoint is None:
            raise ValueError("coordinator_checkpoint_not_found")
        tree = self.store.get_run_tree(parent_run_id)
        accepted: list[dict[str, str]] = []
        refs: list[str] = []
        runs_by_id = {run.id: run for run in tree.child_runs}
        for task in tree.child_tasks:
            if task.accepted_child_run_id is None or task.accepted_result_envelope_ref is None:
                continue
            run = runs_by_id.get(task.accepted_child_run_id)
            trace_ref = f"trace:child-run:{run.id}" if run is not None else ""
            authority = None if run is None or run.result_hash is None else EnvelopeAuthority(
                parent_run_id=parent_run_id,
                task_id=task.id,
                child_run_id=run.id,
                envelope_ref=task.accepted_result_envelope_ref,
                result_hash=run.result_hash,
                principal=tree.parent.principal,
                trace_ref=trace_ref,
            )
            validation = None if authority is None or self._validate_envelope is None else self._validate_envelope(authority)
            if (
                run is None
                or run.parent_run_id != parent_run_id
                or run.child_task_id != task.id
                or run.status not in {"succeeded", "partial"}
                or run.result_envelope_ref != task.accepted_result_envelope_ref
                or run.result_hash != task.accepted_result_hash
                or not _validation_matches(validation, authority)
            ):
                continue
            accepted.append({"task_id": task.id, "child_run_id": run.id, "envelope_ref": task.accepted_result_envelope_ref, "trace_ref": trace_ref})
            refs.extend((task.accepted_result_envelope_ref, trace_ref))
        try:
            context = RunContext.from_checkpoint(dict(checkpoint.payload))
        except Exception as exc:
            raise CoordinatorError("coordinator_checkpoint_invalid") from exc
        context.suspension_checkpoint_ref = None
        context.suspension_requested = False
        context.working_memory = {
            **dict(context.working_memory),
            "validated_child_results": accepted,
        }
        context.artifact_refs = refs
        return context

    def begin_resumed_attempt(self, parent_run_id: str):
        parent = self.store.get_parent(parent_run_id)
        if parent is None:
            raise ValueError("parent_not_found")
        if parent.status != "queued":
            raise ValueError("parent_not_ready_for_resume")
        return self.store.resume_parent_for_validation(
            parent_run_id,
            expected_version=parent.version,
            occurred_at=self._now(),
            idempotency_key=f"coordinator-resume:{parent_run_id}:{parent.version}",
        )

    def merge_ready_parent(self, acceptance_service, parent_run_id: str):
        """Drive the validating seam without admitting raw Child payloads."""
        parent = self.store.get_parent(parent_run_id)
        if parent is None:
            raise ValueError("parent_not_found")
        return acceptance_service.merge_ready_parent(
            parent_run_id, expected_version=parent.version, now=self._now(),
        )

    def absorb_validated_result_ref(self, context: RunContext, validation: EnvelopeValidation) -> RunContext:
        """Expose only a validated Envelope reference to the Parent Context.

        Raw Child output is intentionally neither parsed nor appended to messages.
        """
        if validation.parent_run_id != context.parent_run_id or validation.principal != context.principal or not validation.authorized:
            raise CoordinatorError("validated_result_authority_invalid")
        item = {
            "task_id": validation.task_id,
            "child_run_id": validation.child_run_id,
            "envelope_ref": validation.envelope_ref,
            "trace_ref": validation.trace_ref,
        }
        existing = list(context.working_memory.get("validated_child_results", []))
        if item not in existing:
            existing.append(item)
        context.working_memory = {**dict(context.working_memory), "validated_child_results": existing}
        context.artifact_refs = sorted(set(context.artifact_refs + [validation.envelope_ref, validation.trace_ref]))
        return context

    @staticmethod
    def profile_dependency_inputs(
        *,
        normalized_job_requirements_ref: str,
        web_task_id: str,
        knowledge_scope: Mapping[str, Any],
        candidate_chunk_ids: Iterable[str] = (),
        top_k: int = 6,
    ) -> dict[str, Any]:
        """Build only the frozen Profile specialist contract shape."""
        if not isinstance(knowledge_scope, Mapping) or knowledge_scope.get("type") != "resume":
            raise ValueError("profile_knowledge_scope_invalid")
        if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 50:
            raise ValueError("profile_top_k_invalid")
        chunks = list(candidate_chunk_ids)
        if any(not isinstance(value, str) or not value for value in chunks):
            raise ValueError("profile_candidate_chunk_invalid")
        return {
            "normalized_job_requirements_ref": normalized_job_requirements_ref,
            "depends_on_task_id": web_task_id,
            "knowledge_scope": dict(knowledge_scope),
            "candidate_chunk_ids": chunks,
            "top_k": top_k,
            "output_schema_version": "profile-evidence-output-v1",
        }

    def advance_phase(
        self,
        parent_run_id: str,
        phase: CoordinatorPhase,
        *,
        terminal_status: Literal["succeeded", "partial", "failed"] | None = None,
    ):
        parent = self.store.get_parent(parent_run_id)
        if parent is None:
            raise ValueError("parent_not_found")
        if parent.status != "running":
            raise ValueError("coordinator_parent_status_invalid")
        allowed = {
            CoordinatorPhase.PLANNING.value: {CoordinatorPhase.VALIDATING},
            "children_terminal": {CoordinatorPhase.VALIDATING},
            CoordinatorPhase.VALIDATING.value: {CoordinatorPhase.MERGING},
            CoordinatorPhase.MERGING.value: {CoordinatorPhase.TERMINAL},
        }
        if phase not in allowed.get(parent.phase, set()):
            raise ValueError("coordinator_phase_transition_invalid")
        if phase is CoordinatorPhase.TERMINAL and terminal_status is None:
            raise ValueError("terminal_status_required")
        if phase is not CoordinatorPhase.TERMINAL and terminal_status is not None:
            raise ValueError("terminal_status_only_for_terminal_phase")
        return self.store.set_parent_phase(
            parent_run_id,
            phase=phase.value,
            expected_version=parent.version,
            occurred_at=self._now(),
            terminal_status=terminal_status,
        )

    def delegate_profile_after_web(
        self,
        *,
        service: DelegationService,
        web_task_id: str,
        task_contract: CoordinatorTaskContract,
        context: RunContext,
    ):
        current_parent = self.store.get_parent(context.parent_run_id)
        if current_parent is None or current_parent.status != "running" or current_parent.phase != CoordinatorPhase.VALIDATING.value:
            raise ValueError("coordinator_parent_status_invalid")
        # Parent ownership comes from the accepted Web task, never model input.
        found = self.store.get_child_task(web_task_id)
        if found is not None and (
            found.parent_run_id != context.parent_run_id
            or current_parent.principal != context.principal
        ):
            raise ValueError("accepted_web_result_required")
        if found is None or found.specialist_id != "job_web_researcher" or found.accepted_result_envelope_ref is None:
            raise ValueError("accepted_web_result_required")
        tree = self.store.get_run_tree(found.parent_run_id)
        run = next((item for item in tree.child_runs if item.id == found.accepted_child_run_id), None)
        if (
            run is None
            or run.status not in {"succeeded", "partial"}
            or run.child_task_id != found.id
            or run.result_envelope_ref != found.accepted_result_envelope_ref
            or run.result_hash != found.accepted_result_hash
            or tree.parent.status != "running"
            or tree.parent.phase != CoordinatorPhase.VALIDATING.value
            or self._validate_envelope is None
        ):
            raise ValueError("accepted_web_result_required")
        authority = EnvelopeAuthority(found.parent_run_id, found.id, run.id, found.accepted_result_envelope_ref, run.result_hash, tree.parent.principal, f"trace:child-run:{run.id}")
        validation = self._validate_envelope(authority)
        if not _validation_matches(validation, authority):
            raise ValueError("web_result_reference_rejected")
        profile_inputs = task_contract.model_dump(mode="json")["inputs"]
        try:
            service.registry.resolve(
                "profile_evidence_analyst",
                inputs=profile_inputs,
                requested_budget=task_contract.requested_budget,
            )
        except SpecialistRegistryError as exc:
            if exc.code == "specialist_schema_invalid":
                raise ValueError("profile_input_schema_invalid") from exc
            raise
        supplied_ref = profile_inputs.get("normalized_job_requirements_ref")
        if supplied_ref != found.accepted_result_envelope_ref:
            raise ValueError("web_result_reference_mismatch")
        if profile_inputs.get("depends_on_task_id") != found.id:
            raise ValueError("profile_dependency_reference_mismatch")
        safe_contract = task_contract.model_copy(
            update={
                "inputs": profile_inputs
            }
        )
        profile_call = ToolCall(
            id=f"profile-delegate:{found.id}",
            name="delegate_task",
            arguments={
                "specialist_id": "profile_evidence_analyst",
                "task_contract": safe_contract.model_dump(mode="json"),
            },
        )
        self.record_delegate_batch(
            found.parent_run_id,
            (profile_call,),
            model_request_id=f"profile-stage:{found.id}",
            response_hash=canonical_json_sha256(profile_call.model_dump(mode="json")),
            context_checkpoint=context.to_checkpoint(),
        )
        receipt = service.delegate_task(
            parent_run_id=found.parent_run_id,
            specialist_id="profile_evidence_analyst",
            task_contract=safe_contract,
        )
        self.mark_delegate_call_completed(
            found.parent_run_id,
            profile_call.id,
            {"ok": True, "data": receipt.model_dump(mode="json"), "error_code": None},
            context_checkpoint=context.to_checkpoint(),
        )
        parent = self.store.get_parent(found.parent_run_id)
        assert parent is not None
        checkpoint = CoordinatorCheckpoint(parent_run_id=parent.id, parent_version=parent.version, payload=context.to_checkpoint(), created_at=self._now())
        self.store.checkpoint_and_transition_parent(checkpoint, target_status="waiting_children", phase="waiting_children", expected_version=parent.version)
        return receipt

    def reconcile_profile_intent(
        self,
        *,
        service: DelegationService,
        parent_run_id: str,
    ):
        parent = self.store.get_parent(parent_run_id)
        batch = self.store.get_delegate_batch(parent_run_id)
        if (
            parent is None
            or parent.status != "running"
            or parent.phase != CoordinatorPhase.VALIDATING.value
            or batch is None
            or len(batch.calls) != 1
        ):
            raise CoordinatorError("profile_intent_not_recoverable")
        raw = batch.calls[0]
        arguments = raw.get("arguments")
        if (
            raw.get("name") != "delegate_task"
            or not str(raw.get("id", "")).startswith("profile-delegate:")
            or not isinstance(arguments, dict)
            or arguments.get("specialist_id") != "profile_evidence_analyst"
        ):
            raise CoordinatorError("profile_intent_invalid")
        contract = CoordinatorTaskContract.model_validate(arguments.get("task_contract"))
        context = (
            RunContext.from_checkpoint(dict(batch.context_checkpoint))
            if batch.context_checkpoint is not None
            else None
        )
        dependency_id = contract.inputs.get("depends_on_task_id")
        web_task = self.store.get_child_task(dependency_id) if isinstance(dependency_id, str) else None
        tree = self.store.get_run_tree(parent_run_id)
        web_run = None if web_task is None else next((item for item in tree.child_runs if item.id == web_task.accepted_child_run_id), None)
        authority = None if web_task is None or web_run is None or web_run.result_hash is None or web_task.accepted_result_envelope_ref is None else EnvelopeAuthority(
            parent_run_id, web_task.id, web_run.id, web_task.accepted_result_envelope_ref, web_run.result_hash, parent.principal, f"trace:child-run:{web_run.id}"
        )
        validation = None if authority is None or self._validate_envelope is None else self._validate_envelope(authority)
        if (
            context is None
            or context.parent_run_id != parent_run_id
            or context.principal != parent.principal
            or web_task is None
            or web_task.parent_run_id != parent_run_id
            or web_task.specialist_id != "job_web_researcher"
            or web_run is None
            or web_run.status not in {"succeeded", "partial"}
            or web_run.result_envelope_ref != web_task.accepted_result_envelope_ref
            or web_run.result_hash != web_task.accepted_result_hash
            or contract.inputs.get("normalized_job_requirements_ref") != web_task.accepted_result_envelope_ref
            or authority is None
            or not _validation_matches(validation, authority)
        ):
            raise CoordinatorError("profile_intent_authority_invalid")
        receipt = service.delegate_task(
            parent_run_id=parent_run_id,
            specialist_id="profile_evidence_analyst",
            task_contract=contract,
        )
        self.mark_delegate_call_completed(
            parent_run_id,
            str(raw["id"]),
            {"ok": True, "data": receipt.model_dump(mode="json"), "error_code": None},
            context_checkpoint=context.to_checkpoint(),
        )
        current = self.store.get_parent(parent_run_id)
        assert current is not None
        self.store.checkpoint_and_transition_parent(
            CoordinatorCheckpoint(parent_run_id=parent_run_id, parent_version=current.version, payload=context.to_checkpoint(), created_at=self._now()),
            target_status="waiting_children",
            phase=CoordinatorPhase.WAITING_CHILDREN.value,
            expected_version=current.version,
        )
        return receipt

    def record_delegate_batch(self, parent_run_id: str, calls: Iterable[ToolCall], *, model_request_id: str = "model-request:unknown", response_hash: str | None = None, context_checkpoint: dict[str, Any] | None = None) -> DelegateBatch:
        serialized = tuple({"id": call.id, "name": call.name, "arguments": call.arguments} for call in calls)
        call_ids = [str(call["id"]) for call in serialized]
        if not serialized or any(call["name"] != "delegate_task" for call in serialized) or len(call_ids) != len(set(call_ids)):
            raise ValueError("delegate_batch_invalid")
        response_hash = response_hash or canonical_json_sha256(serialized)
        batch_id = f"delegate-batch:{canonical_json_sha256({'parent_run_id': parent_run_id, 'model_request_id': model_request_id, 'response_hash': response_hash})[:32]}"
        return self.store.save_delegate_batch(DelegateBatch(parent_run_id=parent_run_id, batch_id=batch_id, model_request_id=model_request_id, response_hash=response_hash, calls=serialized, context_checkpoint=context_checkpoint, created_at=self._now()))

    def mark_delegate_call_completed(self, parent_run_id: str, call_id: str, receipt: dict[str, Any] | None = None, *, context_checkpoint: dict[str, Any] | None = None) -> DelegateBatch:
        return self.store.complete_delegate_batch_call(parent_run_id, call_id, receipt, context_checkpoint=context_checkpoint)

    async def replay_incomplete_delegate_batch(self, parent_run_id: str, *, runtime: Any, spec: RunSpec, context: RunContext, on_tool_event=None) -> RunOutcome:
        parent = self.store.get_parent(parent_run_id)
        if parent is None or spec.run_id != parent_run_id or context.run_id != parent_run_id or context.parent_run_id != parent_run_id or context.principal != parent.principal:
            raise CoordinatorError("delegate_batch_authority_mismatch")
        batch = self.store.get_delegate_batch(parent_run_id)
        if batch is None:
            raise CoordinatorError("delegate_batch_not_found")
        call_ids = [str(item.get("id", "")) for item in batch.calls]
        receipt_ids = [str(item.get("call_id", "")) for item in batch.receipts]
        if (
            len(call_ids) != len(set(call_ids))
            or len(receipt_ids) != len(set(receipt_ids))
            or not set(receipt_ids).issubset(call_ids)
            or any(item.get("outcome_hash") != canonical_json_sha256(item.get("outcome")) for item in batch.receipts)
        ):
            raise CoordinatorError("delegate_batch_receipt_invalid")
        if batch.context_checkpoint is not None:
            persisted = RunContext.from_checkpoint(dict(batch.context_checkpoint))
            if persisted.budget.consumed.model_calls > context.budget.consumed.model_calls:
                context = persisted
        completed = set(batch.completed_call_ids)
        outcomes = {str(item["call_id"]): dict(item["outcome"]) for item in batch.receipts}
        calls = [ToolCall.model_validate(raw) for raw in batch.calls]
        if not any(message.role == "assistant" and tuple(call.id for call in message.tool_calls) == tuple(call.id for call in calls) for message in context.messages):
            from starter_agent.domain.models import Message
            context.messages.append(Message(role="assistant", content="", tool_calls=calls))
        for call in calls:
            if call.id in completed:
                outcome = outcomes[call.id]
                from starter_agent.domain.models import Message
                if not any(message.role == "tool" and message.tool_call_id == call.id for message in context.messages):
                    context.messages.append(Message(role="tool", name="delegate_task", tool_call_id=call.id, content=__import__("json").dumps(outcome, ensure_ascii=False)))
                continue
            result = await runtime.replay_persisted_delegate_call(spec=spec, context=context, call=call, on_tool_event=on_tool_event)
            outcome = {"ok": result.ok, "data": result.data if result.ok and isinstance(result.data, dict) else None, "error_code": result.error_code}
            batch = self.mark_delegate_call_completed(parent_run_id, call.id, outcome, context_checkpoint=context.to_checkpoint())
            from starter_agent.domain.models import Message
            if not any(message.role == "tool" and message.tool_call_id == call.id for message in context.messages):
                context.messages.append(Message(role="tool", name="delegate_task", tool_call_id=call.id, content=__import__("json").dumps(outcome, ensure_ascii=False)))
        parent = self.store.get_parent(parent_run_id)
        if parent is None:
            raise CoordinatorError("parent_not_found")
        if parent.status == "waiting_children" and len(batch.completed_call_ids) == len(batch.calls):
            checkpoint = self.store.get_coordinator_checkpoint(parent_run_id)
            return RunOutcome(
                disposition="suspended",
                run_id=parent_run_id,
                status="waiting_children",
                checkpoint_ref=(None if checkpoint is None else f"coordinator-checkpoint:{parent_run_id}:{checkpoint.parent_version}"),
            )
        ref = f"coordinator-checkpoint:{parent_run_id}:{parent.version + 1}"
        context.suspension_checkpoint_ref = ref
        updated = self.store.checkpoint_and_transition_parent(
            CoordinatorCheckpoint(parent_run_id=parent_run_id, parent_version=parent.version, payload=context.to_checkpoint(), created_at=self._now()),
            target_status="waiting_children", phase="waiting_children", expected_version=parent.version,
        )
        return RunOutcome(disposition="suspended", run_id=parent_run_id, status="waiting_children", checkpoint_ref=ref)

    @staticmethod
    def failure_resolution(*, failures: Iterable[tuple[str, str, str]], accepted_envelope_refs: Iterable[str]) -> FailureResolution:
        failures = tuple(failures)
        must_fail = any(behavior == "fail_parent" for _, behavior, _ in failures)
        must_wait = any(behavior == "wait_for_user" for _, behavior, _ in failures)
        return FailureResolution(
            status="failed" if must_fail else "waiting_for_user" if must_wait else "partial",
            missing=tuple(task_id for task_id, _, _ in failures),
            errors=tuple({"task_id": task_id, "error_code": error} for task_id, _, error in failures),
            accepted_envelope_refs=tuple(accepted_envelope_refs),
            facts={},
        )

    def resolve_persisted_failures(self, parent_run_id: str) -> FailureResolution:
        tree = self.store.get_run_tree(parent_run_id)
        runs_by_task = {run.child_task_id: run for run in tree.child_runs}
        terminal_failures = {
            "failed",
            "timed_out",
            "budget_exhausted",
            "cancelled",
        }
        failures = tuple(
            (
                task.id,
                task.failure_behavior,
                run.error_code or f"child_{run.status}",
            )
            for task in tree.child_tasks
            if (run := runs_by_task.get(task.id)) is not None
            and run.status in terminal_failures
        )
        accepted = tuple(
            task.accepted_result_envelope_ref
            for task in tree.child_tasks
            if task.accepted_result_envelope_ref is not None
        )
        return self.failure_resolution(
            failures=failures,
            accepted_envelope_refs=accepted,
        )

    def suspend_for_persisted_failures(self, parent_run_id: str, *, context: RunContext | None = None) -> RunOutcome:
        resolution = self.resolve_persisted_failures(parent_run_id)
        parent = self.store.get_parent(parent_run_id)
        if parent is None:
            raise ValueError("parent_not_found")
        if resolution.status == "waiting_for_user":
            if context is None:
                existing = self.store.get_coordinator_checkpoint(parent_run_id)
                if existing is None:
                    raise CoordinatorError("coordinator_checkpoint_not_found")
                context = RunContext.from_checkpoint(dict(existing.payload))
            ref = f"coordinator-checkpoint:{parent_run_id}:{parent.version + 1}"
            context.suspension_checkpoint_ref = ref
            checkpoint = CoordinatorCheckpoint(parent_run_id=parent_run_id, parent_version=parent.version, payload=context.to_checkpoint(), created_at=self._now())
            updated = self.store.checkpoint_and_transition_parent(checkpoint, target_status="waiting_for_user", phase="waiting_for_user", expected_version=parent.version)
            return RunOutcome(disposition="suspended", run_id=parent_run_id, status="waiting_for_user", checkpoint_ref=ref)
        terminal = self.advance_phase(parent_run_id, CoordinatorPhase.TERMINAL, terminal_status=resolution.status)
        return RunOutcome(disposition="failed" if terminal.status == "failed" else "completed", run_id=parent_run_id, status=terminal.status)
