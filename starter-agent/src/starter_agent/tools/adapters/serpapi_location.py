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
    city_alias: str | None = None
    country_code: str | None = None


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
        canonical_name, city_alias, country_code = self._location_fields(payload)
        return LocationResolution(
            requested,
            canonical_name,
            "resolved" if canonical_name else "not_found",
            city_alias,
            country_code,
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
    def _location_fields(
        payload: Any,
    ) -> tuple[str | None, str | None, str | None]:
        if not isinstance(payload, list):
            raise ValueError("locations_response_invalid")
        for item in payload[:5]:
            if not isinstance(item, dict):
                continue
            value = item.get("canonical_name")
            if isinstance(value, str) and 1 <= len(value.strip()) <= 100:
                canonical_name = value.strip()
                raw_alias = item.get("name")
                city_alias = (
                    raw_alias.strip()
                    if isinstance(raw_alias, str) and raw_alias.strip()
                    else canonical_name.split(",", 1)[0].strip()
                )
                raw_country = item.get("country_code")
                country_code = (
                    raw_country.strip().casefold()
                    if isinstance(raw_country, str)
                    and len(raw_country.strip()) == 2
                    and raw_country.strip().isalpha()
                    else None
                )
                return canonical_name, city_alias or None, country_code
        return None, None, None

    @staticmethod
    def _canonical_name(payload: Any) -> str | None:
        """Backward-compatible helper used by existing callers and tests."""

        return SerpApiLocationResolver._location_fields(payload)[0]
