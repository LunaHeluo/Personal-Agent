from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from starter_agent.domain.models import Message, ModelResponse, ToolCall
from starter_agent.providers.base import Provider


class MockProvider(Provider):
    name = "mock"

    async def complete(
        self,
        messages: list[Message],
        model: str,
        tools: list[dict[str, Any]],
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        tool_choice: str | None = None,
    ) -> ModelResponse:
        last = messages[-1]
        if last.role == "tool":
            content = f"工具返回：{last.content}"
            if on_delta:
                await on_delta(content)
            return ModelResponse(
                content=content,
                provider=self.name,
                model=model,
            )

        text = last.content.strip()
        if tool_choice == "get_current_time":
            return ModelResponse(
                provider=self.name,
                model=model,
                tool_calls=[
                    ToolCall(
                        id=f"mock-{int(datetime.now().timestamp())}",
                        name="get_current_time",
                        arguments={"timezone": "Asia/Shanghai"},
                    )
                ],
            )
        if any(word in text.lower() for word in ("time", "date", "几点", "时间", "日期")):
            if any(tool["function"]["name"] == "get_current_time" for tool in tools):
                timezone_match = re.search(r"(Asia/[A-Za-z_]+|UTC|[+-]\d{2}:\d{2})", text)
                timezone = timezone_match.group(1) if timezone_match else "Asia/Shanghai"
                return ModelResponse(
                    provider=self.name,
                    model=model,
                    tool_calls=[
                        ToolCall(
                            id=f"mock-{int(datetime.now().timestamp())}",
                            name="get_current_time",
                            arguments={"timezone": timezone},
                        )
                    ],
                )

        content = (
                "我是 Starter Agent 的 Mock 模式。当前可以进行基础对话、保存会话，"
                "并在需要时调用只读时间工具。你可以修改 docs/agent.md 来定义我的身份与边界。"
            )
        if on_delta:
            await on_delta(content)
        return ModelResponse(
            content=content,
            provider=self.name,
            model=model,
        )

    async def health(self, model: str) -> tuple[bool, str]:
        return True, f"Mock provider ready ({model})"
