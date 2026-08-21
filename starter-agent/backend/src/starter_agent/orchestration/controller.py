from __future__ import annotations

from starter_agent.orchestration.executor import ExecutionResult
from starter_agent.orchestration.graph import EdgeCondition, StateNode, transition
from starter_agent.orchestration.models import ExecutionState


class OrchestrationController:
    """Advance one explicit node/edge at a time.

    Model, Tool and network work is delegated to existing services outside this
    reducer.  The controller never runs a hidden reflection loop.
    """

    def enter_route(self, state: ExecutionState) -> ExecutionState:
        if state.current_node != "router" or state.route is None:
            raise ValueError("route_decision_not_ready")
        route_targets = {
            "direct": (StateNode.EXECUTOR, EdgeCondition.ROUTE_DIRECT),
            "workflow": (StateNode.EXECUTOR, EdgeCondition.ROUTE_WORKFLOW),
            "tool_loop": (StateNode.EXECUTOR, EdgeCondition.ROUTE_TOOL_LOOP),
            "plan_delegation": (
                StateNode.PLANNER,
                EdgeCondition.ROUTE_PLAN_DELEGATION,
            ),
            "human_review": (
                StateNode.HUMAN_REVIEW,
                EdgeCondition.ROUTE_HUMAN_REVIEW,
            ),
        }
        target, condition = route_targets[state.route.route]
        if target == StateNode.HUMAN_REVIEW and state.pending_action is None:
            raise ValueError("human_review_pending_action_required")
        return transition(state, target=target, condition=condition)

    def after_execution(
        self,
        state: ExecutionState,
        result: ExecutionResult,
    ) -> ExecutionState:
        if state.current_node != "executor":
            raise ValueError("executor_node_not_active")
        outputs = dict(state.outputs)
        if result.output_ref is not None:
            outputs[state.current_step or "final"] = result.output_ref
        state = state.model_copy(
            update={
                "outputs": outputs,
                "artifact_refs": tuple(
                    dict.fromkeys((*state.artifact_refs, *result.artifact_refs))
                ),
            }
        )
        if result.status == "scheduled":
            return transition(
                state,
                target=StateNode.TASK_MANAGER,
                condition=EdgeCondition.BACKGROUND_OR_FANOUT,
            )
        if result.status == "waiting":
            if state.pending_action is None:
                raise ValueError("waiting_execution_pending_action_required")
            return transition(
                state,
                target=StateNode.HUMAN_REVIEW,
                condition=EdgeCondition.GATE_REQUIRES_APPROVAL,
            )
        if result.status == "cancelled":
            return transition(
                state,
                target=StateNode.CANCELLED,
                condition=EdgeCondition.CANCEL_REQUESTED,
            )
        if result.status == "failed":
            return transition(
                state,
                target=StateNode.STOP,
                condition=EdgeCondition.ALWAYS,
                stop_reason=result.error_code or "execution_failed",
            )
        if result.requires_verification:
            return transition(
                state,
                target=StateNode.VERIFIER,
                condition=EdgeCondition.ALWAYS,
            )
        return transition(
            state,
            target=StateNode.END,
            condition=EdgeCondition.ALWAYS,
            execution_status=(
                "partial" if result.status == "partial" else "completed"
            ),
        )

