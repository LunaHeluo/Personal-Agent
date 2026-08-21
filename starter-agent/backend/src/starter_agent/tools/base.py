from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from starter_agent.domain.models import RiskLevel, ToolResult


@dataclass(frozen=True)
class ToolContext:
    session_id: UUID
    turn_id: UUID
    tool_call_id: str | None = None
    user_id: str | None = None
    project_id: str | None = None
    knowledge_base_id: UUID | None = None
    on_tool_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    parent_run_id: str | None = None
    child_task_id: str | None = None
    child_run_id: str | None = None
    eval_run_id: str | None = None
    case_id: str | None = None
    model_request_id: str | None = None
    policy_decision_id: str | None = None
    approval_id: str | None = None
    knowledge_scope: str | None = None
    run_role: str | None = None


class Tool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]
    risk_level: RiskLevel

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    @abstractmethod
    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        raise NotImplementedError
