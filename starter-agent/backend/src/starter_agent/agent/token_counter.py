from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from starter_agent.domain.models import Message


_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_JSON_PUNCTUATION = set("{}[],:\"")


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    estimated: bool = True


class TokenCounter:
    """Conservative preflight counter; provider usage remains authoritative."""

    def __init__(self, safety_ratio: float = 1.15):
        self.safety_ratio = safety_ratio

    def text(self, value: str) -> TokenEstimate:
        cjk = len(_CJK_RE.findall(value))
        punctuation = sum(char in _JSON_PUNCTUATION for char in value)
        ascii_like = max(len(value) - cjk - punctuation, 0)
        base = cjk * 1.5 + ascii_like / 3 + punctuation * 0.5
        return TokenEstimate(tokens=max(1, math.ceil(base * self.safety_ratio)))

    def messages(
        self,
        messages: list[Message],
        tool_schemas: list[dict[str, Any]] | None = None,
    ) -> TokenEstimate:
        payload = [message.model_dump(mode="json") for message in messages]
        serialized = json.dumps(
            {"messages": payload, "tools": tool_schemas or []},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return self.text(serialized)

    def tool_message(
        self,
        content: str,
        tool_name: str,
        tool_call_id: str,
    ) -> TokenEstimate:
        serialized = json.dumps(
            {
                "role": "tool",
                "name": tool_name,
                "tool_call_id": tool_call_id,
                "content": content,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return self.text(serialized)
