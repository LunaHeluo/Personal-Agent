from __future__ import annotations

from typing import Any

from pydantic import Field

from starter_agent.delegation.context import RunContext
from starter_agent.delegation.models import ResultEnvelope
from starter_agent.orchestration.models import (
    BackgroundTask,
    BudgetSnapshot,
    ExecutionState,
    OrchestrationModel,
    PendingAction,
    Plan,
    RouteDecision,
)


CONTEXT_OWNERSHIP = {
    "goal": "execution_state",
    "safety_policy_refs": "policy_authority",
    "confirmed_facts": "parent_run",
    "plan": "execution_state",
    "todo": "run_context",
    "budget": "budget_ledger",
    "pending_action": "approval_gate",
    "chat_summary": "session_context",
    "memory_refs": "long_term_memory",
    "task_snapshot": "sqlite_run_store",
    "child_results": "result_envelope_store",
}


class ChildResultProjection(OrchestrationModel):
    child_run_id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=160)
    status: str = Field(min_length=1, max_length=40)
    result_envelope_ref: str = Field(min_length=1, max_length=500)
    artifact_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=256)
    source_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=256)
    output_fields: tuple[str, ...] = Field(default_factory=tuple, max_length=256)
    missing: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    conflict_count: int = Field(default=0, ge=0)
    error_codes: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    trace_ref: str = Field(min_length=1, max_length=500)


class ParentContextProjection(OrchestrationModel):
    run_id: str = Field(min_length=1, max_length=160)
    goal: str = Field(min_length=1, max_length=4_000)
    safety_policy_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    confirmed_facts: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    route: RouteDecision | None = None
    plan: Plan | None = None
    current_step: str | None = Field(default=None, min_length=1, max_length=160)
    todo: tuple[dict[str, Any], ...] = Field(default_factory=tuple, max_length=1_000)
    budget: BudgetSnapshot | None = None
    pending_action: PendingAction | None = None
    task_snapshot: BackgroundTask | None = None
    chat_summary: str | None = Field(default=None, max_length=20_000)
    memory_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=256)
    child_results: tuple[ChildResultProjection, ...] = Field(
        default_factory=tuple,
        max_length=1_000,
    )


class OrchestrationContextManager:
    """Own the orchestration partition inside the existing RunContext."""

    @staticmethod
    def attach_state(context: RunContext, state: ExecutionState) -> RunContext:
        if state.run_id != context.run_id:
            raise ValueError("orchestration_state_run_mismatch")
        if state.session_id != str(context.session_id):
            raise ValueError("orchestration_state_session_mismatch")
        if state.turn_id != str(context.turn_id):
            raise ValueError("orchestration_state_turn_mismatch")
        context.orchestration_state = state.model_copy(deep=True)
        return context

    @staticmethod
    def parent_projection(
        context: RunContext,
        *,
        safety_policy_refs: tuple[str, ...],
        confirmed_facts: tuple[str, ...] = (),
        chat_summary: str | None = None,
        memory_refs: tuple[str, ...] = (),
        child_results: tuple[ChildResultProjection, ...] = (),
    ) -> ParentContextProjection:
        state = context.orchestration_state
        if state is None:
            raise ValueError("orchestration_state_missing")
        return ParentContextProjection(
            run_id=context.run_id,
            goal=state.goal,
            safety_policy_refs=safety_policy_refs,
            confirmed_facts=confirmed_facts,
            route=state.route,
            plan=state.plan,
            current_step=state.current_step,
            todo=tuple(context.todo_plan),
            budget=state.budget,
            pending_action=state.pending_action,
            task_snapshot=state.background_task,
            chat_summary=chat_summary,
            memory_refs=memory_refs,
            child_results=child_results,
        )

    @staticmethod
    def project_child_result(
        envelope: ResultEnvelope,
        *,
        result_envelope_ref: str,
        artifact_refs: tuple[str, ...],
    ) -> ChildResultProjection:
        source_refs: list[str] = []
        for evidence in envelope.evidence:
            for key in ("artifact_ref", "source_ref", "source_url"):
                value = evidence.get(key)
                if isinstance(value, str) and value and value not in source_refs:
                    source_refs.append(value[:500])
        error_codes = tuple(
            str(item.get("code") or item.get("error_code") or "child_error")[:200]
            for item in envelope.errors
        )
        return ChildResultProjection(
            child_run_id=envelope.child_run_id,
            task_id=envelope.task_id,
            status=envelope.status,
            result_envelope_ref=result_envelope_ref,
            artifact_refs=artifact_refs,
            source_refs=tuple(source_refs),
            output_fields=tuple(
                sorted(
                    str(key)[:200]
                    for key in envelope.output
                    if str(key).casefold()
                    not in {
                        "messages",
                        "scratchpad",
                        "working_memory",
                        "context",
                        "conversation",
                        "history",
                    }
                )
            ),
            missing=envelope.missing,
            conflict_count=len(envelope.conflicts),
            error_codes=error_codes,
            trace_ref=envelope.trace_ref,
        )
