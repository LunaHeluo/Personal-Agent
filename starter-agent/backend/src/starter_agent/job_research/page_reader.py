from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from starter_agent.domain.models import ToolResult
from starter_agent.skills.models import SkillToolTrace
from starter_agent.tools.base import ToolContext


ToolCaller = Callable[
    [str, dict[str, Any], ToolContext],
    Awaitable[tuple[ToolResult, SkillToolTrace]],
]
Sleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class PageReadAttempt:
    attempt_number: int
    wait_seconds: int
    wait_method: str
    status: str
    error_code: str | None = None
    final_url: str = ""
    snapshot_chars: int = 0


@dataclass(frozen=True)
class PageReadResult:
    result: ToolResult | None
    traces: tuple[SkillToolTrace, ...]
    attempts: tuple[PageReadAttempt, ...]
    error_code: str | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None and self.result.ok


class PlaywrightJobPageReader:
    navigate_tool_name = "mcp__playwright__browser_navigate"
    wait_tool_name = "mcp__playwright__browser_wait_for"
    snapshot_tool_name = "mcp__playwright__browser_snapshot"

    def __init__(
        self,
        call_tool: ToolCaller,
        *,
        wait_tool_available: bool = True,
        sleeper: Sleeper = asyncio.sleep,
        stability_interval_seconds: int = 1,
        minimum_snapshot_chars: int = 200,
    ) -> None:
        self._call_tool = call_tool
        self._wait_tool_available = wait_tool_available
        self._sleeper = sleeper
        self._stability_interval_seconds = stability_interval_seconds
        self._minimum_snapshot_chars = minimum_snapshot_chars

    async def read(self, url: str, context: ToolContext) -> PageReadResult:
        traces: list[SkillToolTrace] = []
        attempts: list[PageReadAttempt] = []

        for attempt_number, wait_seconds in enumerate((15, 30), start=1):
            navigate, trace = await self._call_tool(
                self.navigate_tool_name,
                {"url": url},
                context,
            )
            traces.append(trace)
            final_url = self._source_url(navigate) or url
            if not navigate.ok:
                error_code = navigate.error_code or "mcp_unknown_error"
                attempts.append(
                    PageReadAttempt(
                        attempt_number=attempt_number,
                        wait_seconds=wait_seconds,
                        wait_method="not_started",
                        status="navigate_failed",
                        error_code=error_code,
                        final_url=final_url,
                    )
                )
                continue

            wait_method = await self._wait(wait_seconds, context, traces)
            first, error_code = await self._take_snapshot(context, traces)
            if error_code is not None:
                attempts.append(
                    PageReadAttempt(
                        attempt_number=attempt_number,
                        wait_seconds=wait_seconds,
                        wait_method=wait_method,
                        status="snapshot_failed",
                        error_code=error_code,
                        final_url=final_url,
                    )
                )
                continue

            await self._sleeper(self._stability_interval_seconds)
            second, error_code = await self._take_snapshot(context, traces)
            if error_code is not None:
                attempts.append(
                    PageReadAttempt(
                        attempt_number=attempt_number,
                        wait_seconds=wait_seconds,
                        wait_method=wait_method,
                        status="snapshot_failed",
                        error_code=error_code,
                        final_url=final_url,
                    )
                )
                continue

            assert first is not None and second is not None
            snapshot_chars = min(self._snapshot_chars(first), self._snapshot_chars(second))
            error_code = self._validate_snapshots(first, second, final_url)
            if error_code is not None:
                attempts.append(
                    PageReadAttempt(
                        attempt_number=attempt_number,
                        wait_seconds=wait_seconds,
                        wait_method=wait_method,
                        status="validation_failed",
                        error_code=error_code,
                        final_url=final_url,
                        snapshot_chars=snapshot_chars,
                    )
                )
                continue

            attempts.append(
                PageReadAttempt(
                    attempt_number=attempt_number,
                    wait_seconds=wait_seconds,
                    wait_method=wait_method,
                    status="success",
                    final_url=final_url,
                    snapshot_chars=snapshot_chars,
                )
            )
            return PageReadResult(
                result=second,
                traces=tuple(traces),
                attempts=tuple(attempts),
            )

        error_code = attempts[-1].error_code if attempts else "mcp_unknown_error"
        return PageReadResult(
            result=None,
            traces=tuple(traces),
            attempts=tuple(attempts),
            error_code=error_code,
        )

    async def _wait(
        self,
        wait_seconds: int,
        context: ToolContext,
        traces: list[SkillToolTrace],
    ) -> str:
        if self._wait_tool_available:
            result, trace = await self._call_tool(
                self.wait_tool_name,
                {"time": wait_seconds},
                context,
            )
            traces.append(trace)
            if result.ok:
                return "playwright"

        await self._sleeper(wait_seconds)
        return "client_timer"

    async def _take_snapshot(
        self,
        context: ToolContext,
        traces: list[SkillToolTrace],
    ) -> tuple[ToolResult | None, str | None]:
        result, trace = await self._call_tool(self.snapshot_tool_name, {}, context)
        traces.append(trace)
        if result.ok:
            return result, None
        return None, result.error_code or "mcp_unknown_error"

    def _validate_snapshots(
        self,
        first: ToolResult,
        second: ToolResult,
        expected_url: str,
    ) -> str | None:
        first_url = self._source_url(first)
        second_url = self._source_url(second)
        if (first_url and first_url != expected_url) or (second_url and second_url != expected_url):
            return "snapshot_mismatch"
        if (
            self._snapshot_chars(first) <= self._minimum_snapshot_chars
            or self._snapshot_chars(second) <= self._minimum_snapshot_chars
        ):
            return "selector_unmatched"
        first_signature = self._job_evidence_signature(first)
        second_signature = self._job_evidence_signature(second)
        if first_signature and second_signature:
            if first_signature != second_signature:
                return "page_not_stable"
            first_chars = self._snapshot_chars(first)
            second_chars = self._snapshot_chars(second)
            larger = max(first_chars, second_chars)
            if larger and abs(first_chars - second_chars) / larger > 0.2:
                return "page_not_stable"
            return None
        if self._snapshot_hash(first) != self._snapshot_hash(second):
            return "page_not_stable"
        return None

    @classmethod
    def _job_evidence_signature(
        cls,
        result: ToolResult,
    ) -> tuple[str, ...]:
        data = result.data if isinstance(result.data, dict) else {}
        structured = data.get("structured_content")
        values: list[str] = []
        if isinstance(structured, dict):
            responsibilities = cls._string_items(
                structured.get("responsibilities")
            )
            requirements = cls._string_items(structured.get("requirements"))
            if responsibilities or requirements:
                values.extend(
                    cls._string_items(
                        [structured.get("title"), structured.get("location")]
                    )
                )
                values.extend(responsibilities)
                values.extend(requirements)
        else:
            headings = (
                result.metadata.get("snapshot_headings")
                or data.get("snapshot_headings")
                or []
            )
            samples = (
                result.metadata.get("snapshot_signal_samples")
                or data.get("snapshot_signal_samples")
                or []
            )
            sample_values = cls._string_items(samples)
            if sample_values:
                values.extend(cls._string_items(headings))
                values.extend(sample_values)
        return tuple(
            " ".join(value.split()).casefold()
            for value in values
            if value.strip()
        )

    @staticmethod
    def _string_items(value: object) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []
        return [
            item
            for item in value
            if isinstance(item, str) and item.strip()
        ]

    @staticmethod
    def _source_url(result: ToolResult) -> str:
        data = result.data if isinstance(result.data, dict) else {}
        value = (
            result.metadata.get("final_url")
            or result.metadata.get("source_url")
            or data.get("final_url")
            or data.get("source_url")
            or data.get("url")
        )
        return str(value or "")

    @staticmethod
    def _snapshot_chars(result: ToolResult) -> int:
        value = result.metadata.get("snapshot_chars")
        if isinstance(value, int):
            return value
        return len(json.dumps(result.data, ensure_ascii=False, sort_keys=True, default=str))

    @staticmethod
    def _snapshot_hash(result: ToolResult) -> str:
        value = result.metadata.get("source_content_sha256") or result.metadata.get("content_sha256")
        if value:
            return str(value)
        payload = json.dumps(result.data, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
