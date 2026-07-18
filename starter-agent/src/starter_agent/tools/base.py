from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from starter_agent.domain.models import RiskLevel, ToolResult


@dataclass(frozen=True)
class ToolContext:
    session_id: UUID
    turn_id: UUID


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

