from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from starter_agent.domain.models import ToolResult
from starter_agent.tools.base import Tool, ToolContext


class GetCurrentTimeTool(Tool):
    name = "get_current_time"
    description = "Get the current date and time in an IANA timezone or UTC offset."
    risk_level = "read"
    input_schema = {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA timezone such as Asia/Shanghai, UTC, or +08:00.",
                "default": "Asia/Shanghai",
            }
        },
        "additionalProperties": False,
    }

    async def execute(
        self, arguments: dict[str, object], context: ToolContext
    ) -> ToolResult:
        name = str(arguments.get("timezone", "Asia/Shanghai"))
        try:
            if name == "UTC":
                zone = UTC
            elif len(name) == 6 and name[0] in "+-" and name[3] == ":":
                sign = 1 if name[0] == "+" else -1
                delta = timedelta(
                    hours=int(name[1:3]) * sign,
                    minutes=int(name[4:6]) * sign,
                )
                zone = timezone(delta)
            else:
                zone = ZoneInfo(name)
        except (ValueError, ZoneInfoNotFoundError):
            return ToolResult(
                ok=False,
                error_code="invalid_timezone",
                display=f"Unknown timezone: {name}",
            )
        current = datetime.now(zone)
        payload = {
            "timezone": name,
            "iso": current.isoformat(),
            "date": current.date().isoformat(),
            "time": current.strftime("%H:%M:%S"),
        }
        return ToolResult(ok=True, data=payload, display=current.isoformat())

