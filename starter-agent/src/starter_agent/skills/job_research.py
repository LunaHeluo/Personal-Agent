from __future__ import annotations

from collections.abc import Callable, Sequence
import asyncio
from datetime import UTC, datetime
import hashlib
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from starter_agent.capabilities.gate import (
    ToolExecutionDenied,
    UnifiedToolExecutor,
)
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.capabilities.models import AuditEvent
from starter_agent.domain.models import ToolResult
from starter_agent.job_research.candidates import JobCandidate
from starter_agent.job_research.fallback import JobPageFallback
from starter_agent.job_research.page_reader import PlaywrightJobPageReader
from starter_agent.skills.models import SkillRunResult, SkillToolTrace
from starter_agent.job_research.search_profile import (
    JobSearchProfileBuilder,
    ProfileAttemptSummary,
    SearchProfileUnavailable,
)
from starter_agent.knowledge.models import Evidence
from starter_agent.providers.base import Provider
from starter_agent.tools.base import ToolContext


class JobValidation(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: Literal["verified", "partial_verified", "rejected"]
    reason_codes: tuple[str, ...] = ()


class JobResearchOrchestrator:
    """A staged job-research workflow whose only Tool boundary is the Executor."""

    search_tool_name = "search_jobs_serpapi"
    browser_tool_name = "mcp__playwright__browser_navigate"
    browser_snapshot_tool_name = "mcp__playwright__browser_snapshot"
    browser_wait_tool_name = "mcp__playwright__browser_wait_for"
    evidence_tool_name = "retrieve_resume_evidence"

    def __init__(
        self,
        registry: UnifiedToolRegistry,
        executor: UnifiedToolExecutor,
        *,
        ingestion_available: bool | Callable[[], bool] = True,
        profile_builder: JobSearchProfileBuilder | None = None,
        page_fallback: JobPageFallback | None = None,
        browser_sleeper: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.registry = registry
        self.executor = executor
        self.ingestion_available = ingestion_available
        self.profile_builder = profile_builder or JobSearchProfileBuilder()
        self.page_fallback = page_fallback
        self.browser_sleeper = browser_sleeper

    async def prepare_request(
        self,
        *,
        user_request: str,
        context: ToolContext,
        provider: Provider,
        model: str,
    ) -> SkillRunResult:
        missing = self._missing((("tool", self.evidence_tool_name),))
        if missing:
            return self._dependency_unavailable(missing)
        evidence_result, evidence_trace = await self._call(
            self.evidence_tool_name,
            {"query": user_request[:10_000], "top_k": 6},
            context,
        )
        if not evidence_result.ok:
            return SkillRunResult(
                status="search_profile_required",
                error_code=evidence_result.error_code or "no_resume_evidence",
                trace=(evidence_trace,),
                data={"resume_evidence": []},
            )
        evidence = self._profile_evidence(evidence_result.data)
        try:
            profile = await self.profile_builder.build(
                user_request=user_request,
                evidence=evidence,
                provider=provider,
                model=model,
            )
        except SearchProfileUnavailable as exc:
            self._audit_profile_attempts(
                exc.attempts,
                context=context,
                provider=provider,
                model=model,
            )
            return SkillRunResult(
                status="search_profile_required",
                error_code=exc.code,
                trace=(evidence_trace,),
                data={"resume_evidence_count": len(evidence)},
            )
        self._audit_profile_attempts(
            profile.attempts,
            context=context,
            provider=provider,
            model=model,
        )
        return SkillRunResult(
            status="search_profile_ready",
            data={
                "search_profile": {
                    "query": profile.query,
                    "location": profile.location,
                    "evidence_refs": list(profile.evidence_refs),
                    "explicit_freshness": profile.explicit_freshness,
                    "role_terms": list(profile.role_terms),
                },
                "resume_evidence": (
                    evidence_result.data.get("evidence", [])
                    if isinstance(evidence_result.data, dict)
                    else []
                ),
            },
            trace=(evidence_trace,),
        )

    async def search_from_request(
        self,
        *,
        user_request: str,
        context: ToolContext,
        provider: Provider,
        model: str,
        limit: int = 3,
    ) -> SkillRunResult:
        prepared = await self.prepare_request(
            user_request=user_request,
            context=context,
            provider=provider,
            model=model,
        )
        if prepared.status != "search_profile_ready":
            return prepared
        return await self.search_prepared(
            prepared=prepared,
            context=context,
            limit=limit,
        )

    async def search_prepared(
        self,
        *,
        prepared: SkillRunResult,
        context: ToolContext,
        limit: int = 3,
    ) -> SkillRunResult:
        missing = self._missing((("tool", self.search_tool_name),))
        if missing:
            return self._dependency_unavailable(missing)
        profile = prepared.data.get("search_profile")
        if prepared.status != "search_profile_ready" or not isinstance(profile, dict):
            return SkillRunResult(
                status="search_profile_required",
                error_code="invalid_search_profile",
                trace=prepared.trace,
            )
        arguments: dict[str, Any] = {
            "query": str(profile.get("query") or ""),
            "limit": limit,
        }
        location = profile.get("location")
        if isinstance(location, str) and location:
            arguments["location"] = location
        search_result, search_trace = await self._call(
            self.search_tool_name,
            arguments,
            context,
        )
        trace = (*prepared.trace, search_trace)
        if not search_result.ok:
            return SkillRunResult(
                status="search_failed",
                error_code=search_result.error_code or "search_failed",
                trace=trace,
                data={"search": search_result.model_dump(mode="json")},
            )
        data = search_result.data if isinstance(search_result.data, dict) else {}
        results = data.get("results")
        if not isinstance(results, list) or not results:
            return SkillRunResult(
                status="search_failed",
                error_code="no_results",
                trace=trace,
                data={"search": search_result.model_dump(mode="json")},
            )
        return SkillRunResult(
            status="waiting_for_url_selection",
            data={
                "results": results,
                "search_profile": profile,
                "resume_evidence": prepared.data.get("resume_evidence", []),
            },
            trace=trace,
        )

    async def search(
        self,
        *,
        query: str,
        context: ToolContext,
        location: str | None = None,
        limit: int = 5,
    ) -> SkillRunResult:
        missing = self._missing((("tool", self.search_tool_name),))
        if missing:
            return self._dependency_unavailable(missing)
        arguments: dict[str, Any] = {"query": query, "limit": limit}
        if location:
            arguments["location"] = location
        result, trace = await self._call(
            self.search_tool_name,
            arguments,
            context,
        )
        if not result.ok:
            return SkillRunResult(
                status="search_failed",
                error_code=result.error_code or "search_failed",
                trace=(trace,),
                data={"search": result.model_dump(mode="json")},
            )
        data = result.data if isinstance(result.data, dict) else {}
        results = data.get("results")
        if not isinstance(results, list) or not results:
            return SkillRunResult(
                status="search_failed",
                error_code="no_results",
                trace=(trace,),
                data={"search": result.model_dump(mode="json")},
            )
        return SkillRunResult(
            status="waiting_for_url_selection",
            data={"results": results},
            trace=(trace,),
        )

    async def analyze(
        self,
        *,
        query: str,
        selected_url: str,
        context: ToolContext,
        top_k: int = 6,
    ) -> SkillRunResult:
        missing = self._missing(
            (
                ("mcp", self.browser_tool_name),
                ("mcp", self.browser_snapshot_tool_name),
                ("tool", self.evidence_tool_name),
            )
        )
        if missing:
            return self._dependency_unavailable(missing)
        browser_result, browser_trace = await self._call(
            self.browser_tool_name,
            {"url": selected_url},
            context,
        )
        if not browser_result.ok:
            return SkillRunResult(
                status="browser_failed",
                error_code=browser_result.error_code or "browser_failed",
                trace=(browser_trace,),
                data={"browser": browser_result.model_dump(mode="json")},
            )
        snapshot_result, snapshot_trace = await self._call(
            self.browser_snapshot_tool_name,
            {},
            context,
        )
        if not snapshot_result.ok:
            return SkillRunResult(
                status="browser_failed",
                error_code=snapshot_result.error_code or "browser_failed",
                trace=(browser_trace, snapshot_trace),
                data={"browser": snapshot_result.model_dump(mode="json")},
            )
        job = self._job_payload(snapshot_result.data)
        validation = self._validate_job(job, selected_url)
        if validation.state != "verified":
            return SkillRunResult(
                status="incomplete_job_description",
                error_code="incomplete_job_description",
                trace=(browser_trace, snapshot_trace),
                data={
                    "job": job,
                    "validation_state": validation.state,
                    "validation_errors": list(validation.reason_codes),
                },
            )
        evidence_result, evidence_traces = await self._retrieve_resume_evidence(
            query=query,
            jobs=(job,),
            top_k=top_k,
            context=context,
        )
        trace = (browser_trace, snapshot_trace, *evidence_traces)
        if not evidence_result.ok:
            return SkillRunResult(
                status="resume_evidence_unavailable",
                error_code=evidence_result.error_code or "no_evidence",
                trace=trace,
                data={
                    "job": job,
                    "resume_evidence": [],
                    "analysis": [],
                    "ingestion": {"status": "not_requested"},
                },
            )
        evidence_data = (
            evidence_result.data
            if isinstance(evidence_result.data, dict)
            else {}
        )
        evidence = evidence_data.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            return SkillRunResult(
                status="resume_evidence_unavailable",
                error_code="no_evidence",
                trace=trace,
                data={
                    "job": job,
                    "resume_evidence": [],
                    "analysis": [],
                    "ingestion": {"status": "not_requested"},
                },
            )
        if not self._ingestion_is_available():
            return SkillRunResult(
                status="dependency_unavailable",
                error_code="dependency_unavailable",
                missing_dependencies=("service:job_description_ingestion",),
                trace=trace,
                data={
                    "job": job,
                    "resume_evidence": evidence,
                    "analysis": self._analysis(job, evidence),
                },
            )
        return SkillRunResult(
            status="waiting_for_jd_ingestion_confirmation",
            trace=trace,
            data={
                "job": job,
                "resume_evidence": evidence,
                "analysis": self._analysis(job, evidence),
                "ingestion": {
                    "status": "confirmation_required",
                    "source_url": job["source_url"],
                },
            },
        )

    async def analyze_candidates(
        self,
        *,
        query: str,
        candidates: Sequence[JobCandidate],
        context: ToolContext,
        target_count: int = 3,
        top_k: int = 6,
        resume_evidence: list[dict[str, Any]] | None = None,
    ) -> SkillRunResult:
        dependencies = [
            ("mcp", self.browser_tool_name),
            ("mcp", self.browser_snapshot_tool_name),
        ]
        if resume_evidence is None:
            dependencies.append(("tool", self.evidence_tool_name))
        missing = self._missing(tuple(dependencies))
        if missing:
            return self._dependency_unavailable(missing)

        traces: list[SkillToolTrace] = []
        attempts: list[dict[str, Any]] = []
        jobs: list[dict[str, Any]] = []
        partial_jobs: list[dict[str, Any]] = []

        def record_attempt(
            attempt: dict[str, Any], *, call_id: str
        ) -> None:
            attempts.append(attempt)
            self._audit_candidate_attempt(
                attempt,
                context=context,
                call_id=call_id,
            )

        for index, candidate in enumerate(candidates):
            started_at = perf_counter()
            reader = PlaywrightJobPageReader(
                self._call,
                wait_tool_available=not self._missing(
                    (("mcp", self.browser_wait_tool_name),)
                ),
                sleeper=self.browser_sleeper,
            )
            page_read = await reader.read(candidate.url, context)
            traces.extend(page_read.traces)
            browser_attempts = [
                {
                    "attempt_number": item.attempt_number,
                    "wait_seconds": item.wait_seconds,
                    "wait_method": item.wait_method,
                    "status": item.status,
                    "error_code": item.error_code,
                    "snapshot_chars": item.snapshot_chars,
                }
                for item in page_read.attempts
            ]
            if not page_read.ok:
                fallback = (
                    await self.page_fallback.retrieve(candidate)
                    if self.page_fallback is not None
                    else None
                )
                if fallback is not None:
                    jobs.extend(fallback.jobs)
                    partial_jobs.extend(fallback.partial_jobs)
                usable = bool(fallback and (fallback.jobs or fallback.partial_jobs))
                fallback_failures = [
                    {"error_code": item.error_code, "safe_reason": item.safe_reason}
                    for item in (fallback.failures if fallback else ())
                ]
                final_error = None if usable else (
                    fallback_failures[-1]["error_code"]
                    if fallback_failures
                    else page_read.error_code
                )
                call_id = page_read.traces[-1].call_id
                attempt = self._candidate_attempt(
                    index=index,
                    source_url=candidate.url,
                    status=("fallback_succeeded" if fallback and fallback.jobs else "partial_verified" if usable else "browser_failed"),
                    error_code=final_error,
                    started_at=started_at,
                    validation_state=("verified" if fallback and fallback.jobs else "partial_verified" if usable else "rejected"),
                )
                attempt.update(
                    browser_attempts=browser_attempts,
                    browser_error_code=page_read.error_code,
                    fallback_method=fallback.method if fallback else "none",
                    fallback_failures=fallback_failures,
                    final_error_code=final_error,
                )
                record_attempt(
                    attempt,
                    call_id=call_id,
                )
                if len(jobs) >= target_count:
                    break
                continue

            snapshot_result = page_read.result
            assert snapshot_result is not None
            snapshot_trace = page_read.traces[-1]
            expected_url = self._result_source_url(snapshot_result) or candidate.url
            job = self._job_payload(snapshot_result.data)
            validation = self._validate_job(job, expected_url)
            if validation.state == "rejected":
                fallback = (
                    await self.page_fallback.retrieve(candidate)
                    if self.page_fallback is not None
                    else None
                )
                if fallback is not None and (fallback.jobs or fallback.partial_jobs):
                    jobs.extend(fallback.jobs)
                    partial_jobs.extend(fallback.partial_jobs)
                    attempt = self._candidate_attempt(
                        index=index,
                        source_url=candidate.url,
                        status="fallback_succeeded" if fallback.jobs else "partial_verified",
                        error_code=None,
                        started_at=started_at,
                        validation_state="verified" if fallback.jobs else "partial_verified",
                    )
                    attempt.update(
                        browser_attempts=browser_attempts,
                        browser_error_code="incomplete_job_description",
                        fallback_method=fallback.method,
                        fallback_failures=[
                            {"error_code": item.error_code, "safe_reason": item.safe_reason}
                            for item in fallback.failures
                        ],
                        final_error_code=None,
                    )
                    record_attempt(attempt, call_id=snapshot_trace.call_id)
                    if len(jobs) >= target_count:
                        break
                    continue
                attempt = self._candidate_attempt(
                    index=index,
                    source_url=candidate.url,
                    status="invalid_jd",
                    error_code="incomplete_job_description",
                    started_at=started_at,
                    page_type=str(job.get("page_type") or "unknown"),
                    truncated=bool(
                        job.get("truncated")
                        or snapshot_result.metadata.get("truncated")
                    ),
                    validation_state=validation.state,
                    reason_codes=validation.reason_codes,
                    snapshot_diagnostics={
                        key: snapshot_result.metadata.get(key)
                        for key in (
                            "upstream_structured_keys",
                            "snapshot_chars",
                            "snapshot_line_shapes",
                            "snapshot_headings",
                            "snapshot_signal_samples",
                        )
                    },
                )
                attempt["validation_errors"] = list(
                    validation.reason_codes
                )
                attempt["browser_attempts"] = browser_attempts
                record_attempt(attempt, call_id=snapshot_trace.call_id)
                continue
            if validation.state == "partial_verified":
                partial_jobs.append(
                    {
                        **job,
                        "validation_reason_codes": list(
                            validation.reason_codes
                        ),
                    }
                )
                record_attempt(
                    {**self._candidate_attempt(
                        index=index,
                        source_url=candidate.url,
                        status="partial_verified",
                        error_code="incomplete_job_description",
                        started_at=started_at,
                        page_type=str(job.get("page_type") or "job_description"),
                        truncated=bool(
                            job.get("truncated")
                            or snapshot_result.metadata.get("truncated")
                        ),
                        validation_state=validation.state,
                        reason_codes=validation.reason_codes,
                    ), "browser_attempts": browser_attempts},
                    call_id=snapshot_trace.call_id,
                )
                continue
            jobs.append(job)
            attempt = self._candidate_attempt(
                index=index,
                source_url=candidate.url,
                status="succeeded",
                error_code=None,
                started_at=started_at,
                page_type=str(job.get("page_type") or "job_description"),
                truncated=bool(
                    job.get("truncated")
                    or snapshot_result.metadata.get("truncated")
                ),
                validation_state=validation.state,
                reason_codes=validation.reason_codes,
            )
            attempt["final_url"] = job["source_url"]
            attempt["browser_attempts"] = browser_attempts
            attempt["retrieval_method"] = "playwright"
            attempt["final_error_code"] = None
            record_attempt(attempt, call_id=snapshot_trace.call_id)
            if len(jobs) >= target_count:
                break

        common_data: dict[str, Any] = {
            "jobs": jobs,
            "partial_jobs": partial_jobs,
            "candidate_attempts": attempts,
        }
        if not jobs:
            return SkillRunResult(
                status="incomplete_job_description",
                error_code=(
                    attempts[-1]["error_code"]
                    if attempts
                    else "job_description_unverified"
                ),
                trace=tuple(traces),
                data=common_data,
            )

        evidence_error: str | None = None
        if resume_evidence is None:
            evidence_result, evidence_traces = await self._retrieve_resume_evidence(
                query=query,
                jobs=jobs,
                top_k=top_k,
                context=context,
            )
            traces.extend(evidence_traces)
            evidence_data = (
                evidence_result.data
                if evidence_result.ok and isinstance(evidence_result.data, dict)
                else {}
            )
            evidence = evidence_data.get("evidence")
            if not isinstance(evidence, list):
                evidence = []
            if not evidence_result.ok:
                evidence_error = evidence_result.error_code or "no_evidence"
        else:
            evidence = list(resume_evidence)
        analyses = [self._analysis(job, evidence) for job in jobs]
        data = {
            **common_data,
            "job": jobs[0],
            "resume_evidence": evidence,
            "analysis": analyses[0],
            "job_results": [
                {"job": job, "analysis": analysis}
                for job, analysis in zip(jobs, analyses, strict=True)
            ],
            "ingestion": {"status": "not_requested"},
        }
        if evidence_error or not evidence:
            return SkillRunResult(
                status="resume_evidence_unavailable",
                error_code=evidence_error or "no_evidence",
                trace=tuple(traces),
                data=data,
            )
        if not self._ingestion_is_available():
            return SkillRunResult(
                status="dependency_unavailable",
                error_code="dependency_unavailable",
                missing_dependencies=("service:job_description_ingestion",),
                trace=tuple(traces),
                data=data,
            )
        data["ingestion"] = {
            "status": "confirmation_required",
            "source_url": jobs[0]["source_url"],
        }
        return SkillRunResult(
            status="waiting_for_jd_ingestion_confirmation",
            trace=tuple(traces),
            data=data,
        )

    @staticmethod
    def _result_source_url(result: ToolResult) -> str:
        data = result.data if isinstance(result.data, dict) else {}
        for key in ("final_url", "source_url"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        for key in ("final_url", "source_url"):
            value = result.metadata.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    @staticmethod
    def _resume_evidence_query(
        user_query: str,
        jobs: Sequence[dict[str, Any]],
    ) -> str:
        """Build a bounded local-only RAG query from verified JD evidence."""

        parts: list[str] = [user_query.strip()]
        for job in jobs:
            title = job.get("title")
            if isinstance(title, str) and title.strip():
                parts.append(title.strip())
            for field in (
                "responsibilities",
                "requirements",
                "preferred_qualifications",
            ):
                values = job.get(field)
                if not isinstance(values, list):
                    continue
                parts.extend(
                    value.strip()
                    for value in values
                    if isinstance(value, str) and value.strip()
                )
        return "\n".join(parts)[:10_000]

    async def _retrieve_resume_evidence(
        self,
        *,
        query: str,
        jobs: Sequence[dict[str, Any]],
        top_k: int,
        context: ToolContext,
    ) -> tuple[ToolResult, tuple[SkillToolTrace, ...]]:
        primary_result, primary_trace = await self._call(
            self.evidence_tool_name,
            {
                "query": self._resume_evidence_query(query, jobs),
                "top_k": top_k,
            },
            context,
        )
        primary_data = (
            primary_result.data
            if isinstance(primary_result.data, dict)
            else {}
        )
        if primary_result.ok and primary_data.get("evidence"):
            return primary_result, (primary_trace,)

        fallback_result, fallback_trace = await self._call(
            self.evidence_tool_name,
            {"query": "我的简历匹配这个岗位", "top_k": top_k},
            context,
        )
        return fallback_result, (primary_trace, fallback_trace)

    @staticmethod
    def _candidate_attempt(
        *,
        index: int,
        source_url: str,
        status: str,
        error_code: str | None,
        started_at: float,
        page_type: str = "unknown",
        truncated: bool = False,
        validation_state: str = "rejected",
        reason_codes: tuple[str, ...] = (),
        snapshot_diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "candidate_index": index,
            "source_url": source_url,
            "status": status,
            "error_code": error_code,
            "page_type": page_type,
            "truncated": truncated,
            "duration_ms": max(0, round((perf_counter() - started_at) * 1000)),
            "validation_state": validation_state,
            "reason_codes": list(reason_codes),
            "snapshot_diagnostics": dict(snapshot_diagnostics or {}),
        }

    def _audit_candidate_attempt(
        self,
        attempt: dict[str, Any],
        *,
        context: ToolContext,
        call_id: str,
    ) -> None:
        executor = self.executor
        gate = getattr(executor, "gate", None)
        store = getattr(gate, "store", None)
        if store is None:
            return
        source_url = str(attempt.get("source_url") or "")
        store.append_audit_event(
            AuditEvent(
                event_id=f"audit-{uuid4().hex}",
                actor="skill:job-research",
                action="job_research.candidate.completed",
                target="job-research:candidate",
                decision=(
                    "allow"
                    if attempt.get("status") == "succeeded"
                    else "error"
                ),
                reason_code=str(
                    attempt.get("error_code")
                    or attempt.get("status")
                    or "candidate_completed"
                ),
                session_id=str(context.session_id),
                turn_id=str(context.turn_id),
                call_id=call_id,
                payload={
                    "candidate_index": int(
                        attempt.get("candidate_index", 0)
                    ),
                    "source_url_hash": hashlib.sha256(
                        source_url.encode("utf-8")
                    ).hexdigest(),
                    "status": str(attempt.get("status") or "unknown"),
                    "error_code": attempt.get("error_code"),
                    "page_type": str(
                        attempt.get("page_type") or "unknown"
                    ),
                    "validation_state": str(
                        attempt.get("validation_state") or "rejected"
                    ),
                    "reason_codes": [
                        str(item)[:160]
                        for item in attempt.get("reason_codes", [])[:20]
                    ],
                    "truncated": bool(attempt.get("truncated")),
                    "duration_ms": max(
                        0, int(attempt.get("duration_ms", 0))
                    ),
                },
                created_at=datetime.now(UTC),
            )
        )

    async def _call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> tuple[ToolResult, SkillToolTrace]:
        call_id = f"skill-job-research-{uuid4().hex}"
        try:
            request = self.executor.gate.request_for_tool(
                caller="skill:job-research",
                principal=context.user_id or "local-user",
                session_id=str(context.session_id),
                turn_id=str(context.turn_id),
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
            )
            decision = await self.executor.gate.evaluate(request)
            if decision.outcome != "allow" or decision.permit is None:
                error_code = (
                    "tool_confirmation_required"
                    if decision.outcome == "require_confirmation"
                    else decision.reason_code
                )
                result = ToolResult(
                    ok=False,
                    display="Skill Tool 请求未获准执行。",
                    error_code=error_code,
                )
                return result, SkillToolTrace(
                    tool_name=tool_name,
                    call_id=call_id,
                    arguments=dict(arguments),
                    result=result.model_dump(mode="json"),
                    gate_outcome=decision.outcome,
                    error_code=error_code,
                )
            value = await self.executor.execute(
                request,
                permit_id=decision.permit.id,
            )
            result = (
                value
                if isinstance(value, ToolResult)
                else ToolResult(ok=True, data=value)
            )
            return result, SkillToolTrace(
                tool_name=tool_name,
                call_id=call_id,
                arguments=dict(arguments),
                result=result.model_dump(mode="json"),
                gate_outcome="allow",
                error_code=result.error_code,
            )
        except (ToolExecutionDenied, ValueError) as exc:
            error_code = getattr(exc, "code", str(exc)) or "tool_execution_failed"
            result = ToolResult(
                ok=False,
                display="Skill Tool 执行失败。",
                error_code=error_code,
            )
            return result, SkillToolTrace(
                tool_name=tool_name,
                call_id=call_id,
                arguments=dict(arguments),
                result=result.model_dump(mode="json"),
                gate_outcome="deny",
                error_code=error_code,
            )

    def _missing(
        self,
        dependencies: tuple[tuple[str, str], ...],
    ) -> tuple[str, ...]:
        missing: list[str] = []
        for kind, name in dependencies:
            capability = self.registry.resolve_execution(name)
            if (
                capability is None
                or not capability.enabled
                or not capability.connected
                or capability.review_state != "approved"
                or not self.executor.has_invoker(
                    capability.server_id,
                    capability.canonical_name,
                )
            ):
                missing.append(f"{kind}:{name}")
        return tuple(missing)

    def _audit_profile_attempts(
        self,
        attempts: tuple[ProfileAttemptSummary, ...],
        *,
        context: ToolContext,
        provider: Provider,
        model: str,
    ) -> None:
        for item in attempts:
            error_code = item.error_code
            self.executor.gate.store.append_audit_event(
                AuditEvent(
                    event_id=f"audit-{uuid4().hex}",
                    actor="skill:job-research",
                    action="model.job_search_profile.completed",
                    target=f"provider:{provider.name}:{model}",
                    decision="error" if error_code else "allow",
                    reason_code=error_code or "profile_valid",
                    session_id=str(context.session_id),
                    turn_id=str(context.turn_id),
                    call_id=item.model_request_id,
                    payload={
                        "attempt": item.attempt,
                        "model_request_id": item.model_request_id,
                        "output_length": item.output_length,
                        "fields": list(item.fields),
                        "error_code": error_code,
                        "provider": provider.name,
                        "model": model,
                    },
                    created_at=datetime.now(UTC),
                )
            )

    @staticmethod
    def _dependency_unavailable(
        missing: tuple[str, ...],
    ) -> SkillRunResult:
        return SkillRunResult(
            status="dependency_unavailable",
            error_code="dependency_unavailable",
            missing_dependencies=missing,
        )

    @staticmethod
    def _job_payload(value: Any) -> dict[str, Any]:
        data = value if isinstance(value, dict) else {}
        structured = data.get("structured_content")
        if isinstance(structured, dict):
            data = structured
        return {
            "title": data.get("title", ""),
            "company": data.get("company", ""),
            "location": data.get("location", ""),
            "responsibilities": data.get("responsibilities", []),
            "requirements": data.get("requirements", []),
            "source_url": data.get("source_url", ""),
            "retrieved_at": data.get("retrieved_at", ""),
            "page_type": data.get("page_type", ""),
            "validation_state": data.get("validation_state", ""),
            "source_spans": data.get("source_spans", []),
            "raw_text": data.get("raw_text", ""),
            "truncated": bool(data.get("truncated")),
        }

    @staticmethod
    def _validate_job(
        job: dict[str, Any],
        selected_url: str,
    ) -> JobValidation:
        hard_reasons: list[str] = []
        partial_reasons: list[str] = []
        informational_reasons: list[str] = []
        page_type = str(job.get("page_type") or "")
        if page_type not in {"", "job_detail", "job_description"}:
            hard_reasons.append("not_job_detail_page")
        if job.get("validation_state") == "rejected":
            hard_reasons.append("extraction_rejected")
        for field in ("title", "source_url"):
            if not isinstance(job.get(field), str) or not job[field].strip():
                hard_reasons.append(f"missing_{field}")
        if not isinstance(job.get("location"), str) or not job["location"].strip():
            partial_reasons.append("missing_location")
        missing_sections: list[str] = []
        for field in ("responsibilities", "requirements"):
            value = job.get(field)
            if (
                not isinstance(value, list)
                or not value
                or any(not isinstance(item, str) or not item.strip() for item in value)
            ):
                missing_sections.append(f"missing_{field}")
        if len(missing_sections) == 2:
            hard_reasons.extend(missing_sections)
        else:
            partial_reasons.extend(missing_sections)
        company = job.get("company")
        if not isinstance(company, str) or not company.strip():
            if missing_sections:
                partial_reasons.append("missing_company")
            else:
                informational_reasons.append("company_not_disclosed")
        if job.get("source_url") != selected_url:
            hard_reasons.append("source_url_mismatch")
        if hard_reasons:
            return JobValidation(
                state="rejected",
                reason_codes=tuple(dict.fromkeys(hard_reasons)),
            )
        if partial_reasons:
            return JobValidation(
                state="partial_verified",
                reason_codes=tuple(dict.fromkeys(partial_reasons)),
            )
        return JobValidation(
            state="verified",
            reason_codes=tuple(dict.fromkeys(informational_reasons)),
        )

    @staticmethod
    def _analysis(
        job: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for requirement in job["requirements"]:
            terms = {
                term.casefold()
                for term in str(requirement).replace("/", " ").split()
                if len(term) >= 2
            }
            cited = [
                item
                for item in evidence
                if isinstance(item, dict)
                and isinstance(item.get("quote"), str)
                and any(term in item["quote"].casefold() for term in terms)
            ]
            rows.append(
                {
                    "requirement": requirement,
                    "status": "matched" if cited else "gap",
                    "evidence": [
                        {
                            key: item.get(key)
                            for key in (
                                "chunk_id",
                                "document_id",
                                "version",
                                "section",
                                "start_line",
                                "end_line",
                                "quote",
                                "source_ref",
                            )
                        }
                        for item in cited
                    ],
                }
            )
        return rows

    def _ingestion_is_available(self) -> bool:
        return (
            self.ingestion_available()
            if callable(self.ingestion_available)
            else self.ingestion_available
        )

    @staticmethod
    def _profile_evidence(value: Any) -> list[Evidence]:
        data = value if isinstance(value, dict) else {}
        rows = data.get("evidence")
        if not isinstance(rows, list):
            return []
        evidence: list[Evidence] = []
        for index, row in enumerate(rows[:10], start=1):
            if not isinstance(row, dict) or not isinstance(row.get("quote"), str):
                continue
            try:
                evidence.append(
                    Evidence(
                        evidence_id=f"E{index}",
                        chunk_id=row.get("chunk_id"),
                        document_id=row.get("document_id"),
                        filename=str(row.get("filename") or "resume"),
                        version=int(row.get("version") or 1),
                        section_path=[str(row.get("section") or "")],
                        start_line=int(row.get("start_line") or 1),
                        end_line=int(row.get("end_line") or 1),
                        text=row["quote"],
                    )
                )
            except (TypeError, ValueError):
                continue
        return evidence
