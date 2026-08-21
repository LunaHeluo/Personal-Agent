from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from starter_agent.trust.fixtures import FixtureManifest, JobResearchFixtureLoader
from starter_agent.trust.models import EvalCase, EvalCaseResult, EvalRun, EvalSuite
from starter_agent.trust.store import RecordAlreadyExistsError, TrustStore


@dataclass(frozen=True, slots=True)
class EvalRunnerConfig:
    run_id: str
    seed: int = 0
    max_concurrency: int = 1
    run_timeout_seconds: float | None = None
    case_timeout_seconds: float = 30
    tool_timeout_seconds: float = 20
    model_timeout_seconds: float = 60
    judge_timeout_seconds: float = 60
    initialization_retries: int = 1
    work_root: Path = Path(".session-only-trust-runner")
    code_version: str = "unknown"
    code_dirty: bool = True
    prompt_version: str = "unknown"
    skill_version: str = "unknown"
    tool_schema_version: str = "unknown"
    policy_version: str = "unknown"
    provider: str = "fixture"
    model: str = "fixture"
    judge: str = "disabled"


@dataclass(frozen=True, slots=True)
class EvalCaseExecution:
    run_id: str
    case: EvalCase
    attempt: int
    seed: int
    work_dir: Path
    database_url: str
    session_id: str
    turn_id: str
    fixture_manifest: FixtureManifest
    fixtures: tuple[str, ...]
    tool_timeout_seconds: float
    model_timeout_seconds: float
    judge_timeout_seconds: float


EvalCaseHandler = Callable[[EvalCaseExecution], Awaitable[dict[str, Any]]]


class EvalRunner:
    def __init__(
        self,
        *,
        store: TrustStore,
        fixture_loader: JobResearchFixtureLoader,
        config: EvalRunnerConfig,
    ) -> None:
        if config.max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if config.initialization_retries < 0:
            raise ValueError("initialization_retries must be non-negative")
        self.store = store
        self.fixture_loader = fixture_loader
        self.config = config
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    async def run_suite(
        self,
        suite: EvalSuite,
        cases: list[EvalCase],
        handler: EvalCaseHandler,
    ) -> EvalRun:
        coroutine = self._run_suite(suite, cases, handler)
        if self.config.run_timeout_seconds is None:
            return await coroutine
        try:
            return await asyncio.wait_for(
                coroutine,
                timeout=self.config.run_timeout_seconds,
            )
        except TimeoutError:
            return self.store.update_run_status(
                self.config.run_id,
                status="failed",
                completed_at=datetime.now(UTC),
            )

    async def _run_suite(
        self,
        suite: EvalSuite,
        cases: list[EvalCase],
        handler: EvalCaseHandler,
    ) -> EvalRun:
        manifest = self.fixture_loader.load_manifest()
        self._create_once(lambda: self.store.create_suite(suite))
        started_at = datetime.now(UTC)
        run = EvalRun(
            id=self.config.run_id,
            suite_id=suite.id,
            run_type="fixture",
            status="running",
            started_at=started_at,
            code_version=self.config.code_version,
            code_dirty=self.config.code_dirty,
            prompt_version=self.config.prompt_version,
            skill_version=self.config.skill_version,
            tool_schema_version=self.config.tool_schema_version,
            policy_version=self.config.policy_version,
            fixture_manifest_hash=manifest.manifest_hash,
            config_summary={
                "seed": self.config.seed,
                "provider": self.config.provider,
                "model": self.config.model,
                "judge": self.config.judge,
                "max_concurrency": self.config.max_concurrency,
            },
        )
        self._create_once(lambda: self.store.create_run(run))
        for case in cases:
            self._create_once(lambda case=case: self.store.create_case(case))

        semaphore = asyncio.Semaphore(self.config.max_concurrency)
        results: list[EvalCaseResult] = []
        pending_cases = list(cases)

        async def run_case(case: EvalCase) -> EvalCaseResult:
            async with semaphore:
                if self._cancel_requested:
                    return self._record_skipped(case, "run_cancelled")
                result = await self._execute_case(case, manifest, handler)
                if self._cancel_requested:
                    return result
                return result

        if self.config.max_concurrency == 1:
            for case in pending_cases:
                if self._cancel_requested:
                    results.append(self._record_skipped(case, "run_cancelled"))
                    continue
                results.append(await run_case(case))
        else:
            results = list(await asyncio.gather(*(run_case(case) for case in pending_cases)))

        completed_at = datetime.now(UTC)
        final_status = self._final_status(results)
        return self.store.update_run_status(
            self.config.run_id,
            status=final_status,
            completed_at=completed_at,
        )

    async def _execute_case(
        self,
        case: EvalCase,
        manifest: FixtureManifest,
        handler: EvalCaseHandler,
    ) -> EvalCaseResult:
        context = self._case_context(case, manifest)
        attempts = self.config.initialization_retries + 1
        last_error: str | None = None
        for attempt in range(1, attempts + 1):
            try:
                outcome = await asyncio.wait_for(
                    handler(context),
                    timeout=self.config.case_timeout_seconds,
                )
                return self._record_case(
                    case,
                    status="passed",
                    outcome_summary={
                        **outcome,
                        "attempt": attempt,
                        "seed": context.seed,
                    },
                )
            except TimeoutError:
                return self._record_case(
                    case,
                    status="error",
                    outcome_summary={
                        "error_code": "case_timeout",
                        "attempt": attempt,
                    },
                )
            except RuntimeError as exc:
                last_error = str(exc) or exc.__class__.__name__
                if attempt < attempts and last_error == "runner_initialization_failed":
                    continue
                return self._record_case(
                    case,
                    status="error",
                    outcome_summary={
                        "error_code": "case_error",
                        "message": last_error,
                        "attempt": attempt,
                    },
                )
            except Exception as exc:
                return self._record_case(
                    case,
                    status="error",
                    outcome_summary={
                        "error_code": "case_error",
                        "message": exc.__class__.__name__,
                        "attempt": attempt,
                    },
                )
        return self._record_case(
            case,
            status="error",
            outcome_summary={
                "error_code": "case_error",
                "message": last_error or "unknown",
                "attempt": attempts,
            },
        )

    def _case_context(
        self,
        case: EvalCase,
        manifest: FixtureManifest,
    ) -> EvalCaseExecution:
        work_dir = self.config.work_root / self.config.run_id / case.id
        return EvalCaseExecution(
            run_id=self.config.run_id,
            case=case,
            attempt=1,
            seed=self.config.seed,
            work_dir=work_dir,
            database_url=f"sqlite:///{work_dir / 'agent.db'}",
            session_id=f"{self.config.run_id}:{case.id}:session",
            turn_id=f"{self.config.run_id}:{case.id}:turn-1",
            fixture_manifest=manifest,
            fixtures=case.fixture_ids,
            tool_timeout_seconds=self.config.tool_timeout_seconds,
            model_timeout_seconds=self.config.model_timeout_seconds,
            judge_timeout_seconds=self.config.judge_timeout_seconds,
        )

    def _record_case(
        self,
        case: EvalCase,
        *,
        status: str,
        outcome_summary: dict[str, Any],
    ) -> EvalCaseResult:
        result = EvalCaseResult(
            id=f"{self.config.run_id}:{case.id}",
            run_id=self.config.run_id,
            case_id=case.id,
            status=status,
            outcome_summary=outcome_summary,
            session_id=f"{self.config.run_id}:{case.id}:session",
            turn_id=f"{self.config.run_id}:{case.id}:turn-1",
        )
        return self.store.create_case_result(result)

    def _record_skipped(self, case: EvalCase, reason: str) -> EvalCaseResult:
        return self._record_case(
            case,
            status="skipped",
            outcome_summary={"reason": reason},
        )

    def _final_status(self, results: list[EvalCaseResult]) -> str:
        if self._cancel_requested:
            return "cancelled"
        if any(result.status == "blocked" for result in results):
            return "blocked"
        if any(result.status in {"failed", "error"} for result in results):
            return "failed"
        return "completed"

    def _create_once(self, operation: Callable[[], Any]) -> None:
        try:
            operation()
        except RecordAlreadyExistsError:
            return
