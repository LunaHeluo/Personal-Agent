from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx


LocationResolutionStatus = Literal["resolved", "not_found", "unavailable"]


@dataclass(frozen=True, slots=True)
class LocationResolution:
    requested: str
    canonical_name: str | None
    status: LocationResolutionStatus


class SerpApiLocationResolver:
    endpoint = "https://serpapi.com/locations.json"

    def __init__(
        self,
        *,
        client: Any | None = None,
        timeout: float = 8.0,
    ) -> None:
        self.client = client
        self.timeout = timeout

    async def resolve(self, location: str) -> LocationResolution:
        requested = " ".join(location.split())[:100]
        if not requested:
            return LocationResolution(requested, None, "not_found")
        try:
            if self.client is not None:
                payload = await self._request(self.client, requested)
            else:
                async with httpx.AsyncClient() as client:
                    payload = await self._request(client, requested)
        except (httpx.HTTPError, ValueError, TypeError):
            return LocationResolution(requested, None, "unavailable")
        canonical_name = self._canonical_name(payload)
        return LocationResolution(
            requested,
            canonical_name,
            "resolved" if canonical_name else "not_found",
        )

    async def _request(self, client: Any, requested: str) -> Any:
        response = await client.get(
            self.endpoint,
            params={"q": requested, "limit": 5},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _canonical_name(payload: Any) -> str | None:
        if not isinstance(payload, list):
            raise ValueError("locations_response_invalid")
        for item in payload[:5]:
            if not isinstance(item, dict):
                continue
            value = item.get("canonical_name")
            if isinstance(value, str) and 1 <= len(value.strip()) <= 100:
                return value.strip()
        return None
