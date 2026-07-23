from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from starter_agent.capabilities.gate import (
    ToolExecutionDenied,
    UnifiedToolExecutor,
)
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.domain.models import ToolResult
from starter_agent.skills.models import SkillRunResult, SkillToolTrace
from starter_agent.tools.base import ToolContext


class JobResearchOrchestrator:
    """A staged job-research workflow whose only Tool boundary is the Executor."""

    search_tool_name = "search_jobs_serpapi"
    browser_tool_name = "mcp__playwright__browser_navigate"
    evidence_tool_name = "retrieve_resume_evidence"

    def __init__(
        self,
        registry: UnifiedToolRegistry,
        executor: UnifiedToolExecutor,
        *,
        ingestion_available: bool | Callable[[], bool] = True,
    ) -> None:
        self.registry = registry
        self.executor = executor
        self.ingestion_available = ingestion_available

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
        job = self._job_payload(browser_result.data)
        reasons = self._validate_job(job, selected_url)
        if reasons:
            return SkillRunResult(
                status="incomplete_job_description",
                error_code="incomplete_job_description",
                trace=(browser_trace,),
                data={"job": job, "validation_errors": list(reasons)},
            )
        evidence_query = " ".join(
            [
                query,
                *job["requirements"],
                *job["responsibilities"],
            ]
        )[:10_000]
        evidence_result, evidence_trace = await self._call(
            self.evidence_tool_name,
            {"query": evidence_query, "top_k": top_k},
            context,
        )
        trace = (browser_trace, evidence_trace)
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
            ):
                missing.append(f"{kind}:{name}")
        return tuple(missing)

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
        }

    @staticmethod
    def _validate_job(
        job: dict[str, Any],
        selected_url: str,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        for field in ("title", "company", "location", "source_url"):
            if not isinstance(job.get(field), str) or not job[field].strip():
                reasons.append(f"missing_{field}")
        for field in ("responsibilities", "requirements"):
            value = job.get(field)
            if (
                not isinstance(value, list)
                or not value
                or any(not isinstance(item, str) or not item.strip() for item in value)
            ):
                reasons.append(f"missing_{field}")
        if job.get("source_url") != selected_url:
            reasons.append("source_url_mismatch")
        return tuple(reasons)

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
