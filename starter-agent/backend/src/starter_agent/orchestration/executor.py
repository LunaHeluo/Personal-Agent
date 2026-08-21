from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from pydantic import Field

from starter_agent.delegation.context import RunContext
from starter_agent.delegation.models import RunOutcome, RunSpec
from starter_agent.orchestration.models import (
    ExecutionState,
    OrchestrationModel,
    PlanStep,
)


class ExecutionResult(OrchestrationModel):
    status: str = Field(
        pattern=r"^(succeeded|partial|failed|waiting|scheduled|cancelled)$"
    )
    output_ref: str | None = Field(default=None, min_length=1, max_length=500)
    artifact_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    error_code: str | None = Field(default=None, min_length=1, max_length=200)
    requires_verification: bool = False


WorkflowHandler = Callable[
    [ExecutionState, RunContext], Awaitable[ExecutionResult]
]
DelegationSchedule = Callable[
    [ExecutionState, PlanStep, RunContext], Awaitable[ExecutionResult]
]


class UnifiedExecutor:
    """Adapters into existing execution capabilities; not another runtime."""

    def __init__(
        self,
        *,
        runtime,
        workflows: Mapping[str, WorkflowHandler] | None = None,
        delegation_schedule: DelegationSchedule | None = None,
    ) -> None:
        self.runtime = runtime
        self.workflows = dict(workflows or {})
        self.delegation_schedule = delegation_schedule

    async def execute(
        self,
        state: ExecutionState,
        *,
        context: RunContext,
        spec: RunSpec | None = None,
        workflow_id: str | None = None,
        budget_available: bool = True,
    ) -> ExecutionResult:
        if not budget_available:
            raise ValueError("execution_budget_unavailable")
        if state.current_node != "executor":
            raise ValueError("execution_node_not_active")
        if state.run_id != context.run_id:
            raise ValueError("execution_context_run_mismatch")
        route = None if state.route is None else state.route.route
        if route == "human_review":
            raise ValueError("human_review_is_not_executable")

        step = self._current_step(state)
        execution_type = None if step is None else step.execution
        if step is not None:
            if state.plan is None or state.plan.status not in {"valid", "executing"}:
                raise ValueError("plan_not_validated")
            if state.plan.validation_result_id is None:
                raise ValueError("plan_validation_result_missing")

        if execution_type == "child":
            if self.delegation_schedule is None:
                raise ValueError("delegation_adapter_unavailable")
            return await self.delegation_schedule(state, step, context)

        if execution_type == "workflow" or (step is None and route == "workflow"):
            selected_workflow = (
                step.workflow_id if step is not None else workflow_id
            )
            if selected_workflow is None:
                raise ValueError("workflow_id_required")
            handler = self.workflows.get(selected_workflow)
            if handler is None:
                raise ValueError("workflow_unavailable")
            return await handler(state, context)

        if route == "direct" or execution_type == "local":
            if spec is None:
                raise ValueError("runtime_spec_required")
            if spec.allowed_tools:
                raise ValueError("direct_tools_forbidden")
            return _from_outcome(await self.runtime.run(spec=spec, context=context))

        if route == "tool_loop" or execution_type == "tool_loop":
            if spec is None:
                raise ValueError("runtime_spec_required")
            if not spec.allowed_tools:
                raise ValueError("tool_loop_requires_allowed_tools")
            return _from_outcome(await self.runtime.run(spec=spec, context=context))

        if route == "plan_delegation" and step is None:
            raise ValueError("plan_current_step_required")
        raise ValueError("execution_route_unsupported")

    @staticmethod
    def _current_step(state: ExecutionState) -> PlanStep | None:
        if state.current_step is None:
            return None
        if state.plan is None:
            raise ValueError("current_step_plan_missing")
        for step in state.plan.steps:
            if step.step_id == state.current_step:
                return step
        raise ValueError("current_step_not_found")


def _from_outcome(outcome: RunOutcome) -> ExecutionResult:
    status = {
        "completed": outcome.status,
        "suspended": "waiting",
        "failed": "failed",
        "cancelled": "cancelled",
    }[outcome.disposition]
    if status not in {"succeeded", "partial", "failed", "waiting", "cancelled"}:
        status = "failed"
    return ExecutionResult(
        status=status,
        output_ref=outcome.output_ref or outcome.result_envelope_ref,
        error_code=outcome.error_code,
        requires_verification=(status in {"partial"}),
    )

