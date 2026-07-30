from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from starter_agent.domain.models import Message
from starter_agent.providers.base import Provider


_FENCE = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*)\r?\n```[ \t]*\Z",
    re.IGNORECASE | re.DOTALL,
)
_LATIN_LOCATION = re.compile(r"[A-Za-z][A-Za-z0-9 .,'()&/-]*\Z")


class _AliasResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_alias: str = Field(min_length=1, max_length=100)


class LocationAliasBuilder:
    """Generate a minimal alias; the search provider validates authority later."""

    async def build(
        self,
        *,
        location: str,
        provider: Provider,
        model: str,
    ) -> str | None:
        requested = " ".join(location.split())[:100]
        if not requested:
            return None
        for attempt in range(2):
            messages = self._messages(requested, retry=attempt > 0)
            try:
                response = await provider.complete(messages, model, tools=[])
                alias = self._parse(response.content or "")
            except Exception:
                # Alias recovery is optional and must never fail job research.
                continue
            if (
                alias.casefold() != requested.casefold()
                and _LATIN_LOCATION.fullmatch(alias) is not None
            ):
                return alias
        return None

    @staticmethod
    def _parse(content: str) -> str:
        stripped = content.strip()
        match = _FENCE.fullmatch(stripped)
        if match:
            stripped = match.group("body").strip()
        parsed = _AliasResponse.model_validate_json(stripped)
        return " ".join(parsed.location_alias.split())

    @staticmethod
    def _messages(location: str, *, retry: bool) -> list[Message]:
        retry_text = (
            ' Previous output was invalid. Return exactly: '
            '{"location_alias":"Shanghai"}.'
            if retry
            else ""
        )
        return [
            Message(
                role="system",
                content=(
                    "Return one JSON object with exactly one field, "
                    'location_alias. Translate or transliterate the supplied '
                    "place into its commonly searched Latin-script name. "
                    "Do not add explanation or any other field."
                    f"{retry_text}"
                ),
            ),
            Message(role="user", content=location),
        ]
