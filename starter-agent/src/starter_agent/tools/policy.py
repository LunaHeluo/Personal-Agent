from starter_agent.domain.errors import ToolPolicyError
from starter_agent.tools.base import Tool


class ToolPolicy:
    def __init__(self, allowed_risk_levels: list[str]):
        self.allowed_risk_levels = set(allowed_risk_levels)

    def check(self, tool: Tool) -> None:
        if tool.risk_level not in self.allowed_risk_levels:
            raise ToolPolicyError(
                f"Tool '{tool.name}' risk level '{tool.risk_level}' is not allowed"
            )

