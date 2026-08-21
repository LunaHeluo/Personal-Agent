"""Frozen single-agent job-research baseline; never a normal route dependency."""

from __future__ import annotations


class SingleAgentBaselineRunner:
    def __init__(self, orchestrator) -> None:
        self._orchestrator = orchestrator

    async def search_from_request(self, **kwargs):
        return await self._orchestrator.search_from_request(**kwargs)
