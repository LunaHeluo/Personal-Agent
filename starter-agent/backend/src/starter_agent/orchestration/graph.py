from __future__ import annotations

from enum import Enum

from starter_agent.orchestration.models import ExecutionState


class StateNode(str, Enum):
    START = "start"
    LOAD_STATE = "load_state"
    ROUTER = "router"
    PLANNER = "planner"
    PLAN_VALIDATOR = "plan_validator"
    TASK_MANAGER = "task_manager"
    EXECUTOR = "executor"
    JOIN = "join"
    MERGE = "merge"
    VERIFIER = "verifier"
    RECOVERY = "recovery"
    HUMAN_REVIEW = "human_review"
    END = "end"
    STOP = "stop"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class EdgeCondition(str, Enum):
    ALWAYS = "always"
    ROUTE_DIRECT = "route_direct"
    ROUTE_WORKFLOW = "route_workflow"
    ROUTE_TOOL_LOOP = "route_tool_loop"
    ROUTE_PLAN_DELEGATION = "route_plan_delegation"
    ROUTE_HUMAN_REVIEW = "route_human_review"
    PLAN_VALID = "plan_valid"
    PLAN_INVALID_REPAIRABLE = "plan_invalid_repairable"
    PLAN_INVALID_HUMAN = "plan_invalid_human"
    BACKGROUND_OR_FANOUT = "background_or_fanout"
    FOREGROUND_READY = "foreground_ready"
    GATE_REQUIRES_APPROVAL = "gate_requires_approval"
    BUDGET_AVAILABLE = "budget_available"
    BUDGET_EXHAUSTED = "budget_exhausted"
    JOIN_WAIT = "join_wait"
    JOIN_MERGE = "join_merge"
    JOIN_HUMAN = "join_human"
    JOIN_FAIL = "join_fail"
    VERIFY_PASSED = "verify_passed"
    VERIFY_RECOVERABLE = "verify_recoverable"
    VERIFY_HUMAN = "verify_human"
    VERIFY_STOP = "verify_stop"
    RECOVERY_SUCCEEDED = "recovery_succeeded"
    RECOVERY_EXHAUSTED = "recovery_exhausted"
    CANCEL_REQUESTED = "cancel_requested"
    PROCESS_INTERRUPTED = "process_interrupted"


class StateTransitionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


_ALLOWED_TARGETS: dict[StateNode, frozenset[StateNode]] = {
    StateNode.START: frozenset({StateNode.LOAD_STATE}),
    StateNode.LOAD_STATE: frozenset(
        {StateNode.ROUTER, StateNode.STOP, StateNode.CANCELLED, StateNode.INTERRUPTED}
    ),
    StateNode.ROUTER: frozenset(
        {StateNode.EXECUTOR, StateNode.PLANNER, StateNode.HUMAN_REVIEW, StateNode.STOP}
    ),
    StateNode.PLANNER: frozenset(
        {StateNode.PLAN_VALIDATOR, StateNode.HUMAN_REVIEW, StateNode.STOP}
    ),
    StateNode.PLAN_VALIDATOR: frozenset(
        {
            StateNode.PLANNER,
            StateNode.EXECUTOR,
            StateNode.TASK_MANAGER,
            StateNode.HUMAN_REVIEW,
            StateNode.STOP,
        }
    ),
    StateNode.TASK_MANAGER: frozenset(
        {StateNode.TASK_MANAGER, StateNode.JOIN, StateNode.HUMAN_REVIEW, StateNode.STOP, StateNode.CANCELLED}
    ),
    StateNode.EXECUTOR: frozenset(
        {
            StateNode.EXECUTOR,
            StateNode.TASK_MANAGER,
            StateNode.VERIFIER,
            StateNode.HUMAN_REVIEW,
            StateNode.END,
            StateNode.STOP,
            StateNode.CANCELLED,
        }
    ),
    StateNode.JOIN: frozenset(
        {StateNode.JOIN, StateNode.MERGE, StateNode.VERIFIER, StateNode.HUMAN_REVIEW, StateNode.STOP}
    ),
    StateNode.MERGE: frozenset({StateNode.VERIFIER, StateNode.STOP}),
    StateNode.VERIFIER: frozenset(
        {StateNode.RECOVERY, StateNode.HUMAN_REVIEW, StateNode.END, StateNode.STOP}
    ),
    StateNode.RECOVERY: frozenset(
        {StateNode.VERIFIER, StateNode.HUMAN_REVIEW, StateNode.STOP}
    ),
    StateNode.HUMAN_REVIEW: frozenset(
        {
            StateNode.EXECUTOR,
            StateNode.PLANNER,
            StateNode.PLAN_VALIDATOR,
            StateNode.VERIFIER,
            StateNode.END,
            StateNode.STOP,
            StateNode.CANCELLED,
        }
    ),
    StateNode.END: frozenset(),
    StateNode.STOP: frozenset(),
    StateNode.CANCELLED: frozenset(),
    StateNode.INTERRUPTED: frozenset(),
}


_ROUTE_CONDITIONS = {
    EdgeCondition.ROUTE_DIRECT: "direct",
    EdgeCondition.ROUTE_WORKFLOW: "workflow",
    EdgeCondition.ROUTE_TOOL_LOOP: "tool_loop",
    EdgeCondition.ROUTE_PLAN_DELEGATION: "plan_delegation",
    EdgeCondition.ROUTE_HUMAN_REVIEW: "human_review",
}


def allowed_targets(source: StateNode | str) -> frozenset[StateNode]:
    return _ALLOWED_TARGETS[StateNode(source)]


def transition(
    state: ExecutionState,
    *,
    target: StateNode | str,
    condition: EdgeCondition | str,
    execution_status: str | None = None,
    stop_reason: str | None = None,
) -> ExecutionState:
    """Apply one already-evaluated conditional edge.

    Component-specific evaluators own Gate/Budget/Join decisions.  This reducer
    only enforces graph legality and route conditions, so it cannot become a
    second execution runtime.
    """

    source_node = StateNode(state.current_node)
    target_node = StateNode(target)
    edge_condition = EdgeCondition(condition)
    if target_node not in allowed_targets(source_node):
        raise StateTransitionError(
            "illegal_target",
            f"illegal orchestration transition: {source_node.value}->{target_node.value}",
        )
    required_route = _ROUTE_CONDITIONS.get(edge_condition)
    actual_route = None if state.route is None else state.route.route
    if required_route is not None and actual_route != required_route:
        raise StateTransitionError(
            "route_condition_failed",
            f"edge requires route {required_route}, got {actual_route}",
        )
    if source_node == StateNode.ROUTER:
        expected_targets = {
            "direct": StateNode.EXECUTOR,
            "workflow": StateNode.EXECUTOR,
            "tool_loop": StateNode.EXECUTOR,
            "plan_delegation": StateNode.PLANNER,
            "human_review": StateNode.HUMAN_REVIEW,
        }
        expected = expected_targets.get(actual_route)
        if expected is not None and target_node != expected:
            raise StateTransitionError(
                "route_target_mismatch",
                f"route {actual_route} must enter {expected.value}",
            )

    terminal_status = {
        StateNode.END: "completed",
        StateNode.STOP: "failed",
        StateNode.CANCELLED: "cancelled",
        StateNode.INTERRUPTED: "interrupted",
    }.get(target_node)
    next_status = execution_status or terminal_status or (
        "waiting" if target_node == StateNode.HUMAN_REVIEW else "running"
    )
    return state.model_copy(
        update={
            "current_node": target_node.value,
            "execution_status": next_status,
            "stop_reason": stop_reason,
            "state_version": state.state_version + 1,
        }
    )
