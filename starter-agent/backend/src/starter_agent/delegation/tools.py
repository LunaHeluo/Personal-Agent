from __future__ import annotations

from pydantic import ValidationError

from starter_agent.capabilities.gate import ToolExecutionDenied
from starter_agent.delegation.service import CoordinatorTaskContract, DelegationService
from starter_agent.domain.models import ToolResult
from starter_agent.tools.base import Tool, ToolContext


class DelegateTaskTool(Tool):
    name = "delegate_task"
    description = "Create a persistent bounded Child Run for a registered specialist."
    risk_level = "write"
    metadata = {"action": "read"}
    input_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["specialist_id", "task_contract"],
        "properties": {
            "specialist_id": {"type": "string", "minLength": 1, "maxLength": 160},
            "task_contract": {
                "type": "object", "additionalProperties": False,
                "required": ["goal", "inputs", "requested_deadline", "requested_budget", "failure_behavior", "idempotency_key"],
                "properties": {
                    "goal": {"type": "string", "minLength": 1, "maxLength": 4000}, "inputs": {"type": "object"},
                    "constraints": {"type": "object"}, "requested_allowed_tools": {"type": "array", "items": {"type": "string"}, "uniqueItems": True, "maxItems": 64},
                    "requested_deadline": {"type": "string", "format": "date-time"}, "requested_budget": {"type": "object"},
                    "failure_behavior": {"enum": ["fail_parent", "allow_partial", "wait_for_user"]},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 160}, "contract_version": {"type": "string", "minLength": 1, "maxLength": 32},
                },
            },
        },
    }

    def __init__(self, service: DelegationService) -> None:
        self.service = service

    async def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
        if context.run_role != "coordinator" or context.parent_run_id is None or context.child_task_id is not None or context.child_run_id is not None:
            raise ToolExecutionDenied("delegate_task_coordinator_only")
        try:
            if set(arguments) != {"specialist_id", "task_contract"}:
                raise ValueError("unexpected field")
            specialist_id = arguments["specialist_id"]
            contract = CoordinatorTaskContract.model_validate(arguments["task_contract"])
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ToolExecutionDenied("delegate_task_invalid_arguments") from exc
        receipt = self.service.delegate_task(parent_run_id=context.parent_run_id, specialist_id=specialist_id, task_contract=contract)
        return ToolResult(ok=True, data=receipt.model_dump(mode="json"), display="Child Run queued")
