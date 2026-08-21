from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from starter_agent.trust.models import JudgeResult, JudgeRubric
from starter_agent.trust.store import TrustStore


@dataclass(frozen=True, slots=True)
class JudgeClientResult:
    provider: str
    model: str
    raw_score: float
    normalized_score: float
    reason: str
    usage: dict[str, Any]


JudgeClient = Callable[[JudgeRubric, dict[str, Any]], Awaitable[JudgeClientResult]]


class LlmJudgeService:
    def __init__(
        self,
        *,
        store: TrustStore,
        enabled: bool,
        client: JudgeClient | None,
    ) -> None:
        self.store = store
        self.enabled = enabled
        self.client = client

    async def judge(
        self,
        *,
        id: str,
        run_id: str,
        case_result_id: str,
        rubric: JudgeRubric,
        payload: dict[str, Any],
    ) -> JudgeResult | None:
        if not self.enabled:
            return None
        if self.client is None:
            raise RuntimeError("judge_client_unavailable")
        client_result = await self.client(rubric, payload)
        result = JudgeResult(
            id=id,
            run_id=run_id,
            case_result_id=case_result_id,
            rubric_id=rubric.id,
            rubric_version=rubric.version,
            provider=client_result.provider,
            model=client_result.model,
            raw_score=client_result.raw_score,
            normalized_score=client_result.normalized_score,
            reason=client_result.reason,
            usage_summary=client_result.usage,
            created_at=datetime.now(UTC),
        )
        return self.store.create_judge_result(result)

    def judge_sync(
        self,
        *,
        id: str,
        run_id: str,
        case_result_id: str,
        rubric: JudgeRubric,
        payload: dict[str, Any],
    ) -> JudgeResult | None:
        return asyncio.run(
            self.judge(
                id=id,
                run_id=run_id,
                case_result_id=case_result_id,
                rubric=rubric,
                payload=payload,
            )
        )
