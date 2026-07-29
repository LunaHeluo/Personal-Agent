from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from starter_agent.domain.models import Message
from starter_agent.mcp.config import McpConfigLoader
from starter_agent.mcp.manager import McpManager
from starter_agent.mcp.network_guard import PlaywrightNetworkGuard
from starter_agent.providers.registry import ProviderRegistry
from starter_agent.settings import AgentSettings
from starter_agent.trust.models import EvalRun, EvalSuite, SmokeRun, TrustTraceEvent
from starter_agent.trust.store import RecordAlreadyExistsError, TrustStore
from starter_agent.job_research.candidates import rank_job_candidates
from starter_agent.job_research.candidates import JobCandidate


def expected_smoke_report_fields() -> frozenset[str]:
    return frozenset(
        {
            "route_decision",
            "resume_evidence",
            "serpapi_search",
            "candidate_attempts",
            "verified_source_url",
            "separate_from_fixture_baseline",
        }
    )


DEFAULT_PUBLIC_JD_URL = (
    "https://jobs.lever.co/payugpo/"
    "49975338-7270-422e-a3c1-e2375394cef4"
)

SMOKE_MODEL_INSTRUCTION = (
    "这是一次 job-research 真实 Smoke。请只基于下面的公开 JD 摘要，"
    "判断是否成功读取到岗位页面，并用一句中文概括；不要输出完整正文。"
)


_ERROR_PAGE = re.compile(
    r"(?:ERR_[A-Z_]+|404\s+not\s+found|access\s+denied|forbidden|"
    r"page\s+does\s+not\s+exist|captcha|verify\s+you\s+are\s+human)",
    re.IGNORECASE,
)
_JD_SIGNALS = (
    re.compile(r"\b(?:responsibilit(?:y|ies)|what you(?:'|’)ll do)\b", re.I),
    re.compile(r"\b(?:requirements?|qualifications?|what we(?:'|’)re looking for)\b", re.I),
    re.compile(r"\b(?:apply(?: now)?|job description|position|role)\b", re.I),
    re.compile(r"(?:岗位职责|职位描述|任职要求|岗位要求|立即申请|申请职位)"),
)


def validate_jd_snapshot(snapshot_text: str) -> tuple[bool, dict[str, Any]]:
    """Reject empty/error/list pages before a live Smoke can pass."""

    normalized = " ".join(snapshot_text.split())
    if not normalized:
        return False, {"reason_code": "empty_snapshot", "jd_signal_count": 0}
    if _ERROR_PAGE.search(normalized):
        return False, {"reason_code": "error_page", "jd_signal_count": 0}
    signal_count = sum(bool(pattern.search(normalized)) for pattern in _JD_SIGNALS)
    if len(normalized) < 80 or signal_count < 2:
        return False, {
            "reason_code": "insufficient_jd_structure",
            "jd_signal_count": signal_count,
            "snapshot_chars": len(normalized),
        }
    return True, {
        "reason_code": "validated_jd",
        "jd_signal_count": signal_count,
        "snapshot_chars": len(normalized),
    }


def select_smoke_candidates(
    candidates: tuple[JobCandidate, ...],
    *,
    limit: int,
    source_url: str | None = None,
) -> tuple[JobCandidate, ...]:
    """Select every bounded public HTTPS candidate for the live read probe."""

    if limit <= 0:
        return ()
    selected = list(
        item
        for item in candidates
        if urlsplit(item.url).scheme.casefold() == "https"
    )
    if source_url and urlsplit(source_url).scheme.casefold() == "https":
        selected = [item for item in selected if item.url != source_url]
        selected.insert(
            0,
            JobCandidate(
                url=source_url,
                title="Public JD smoke probe",
                source="explicit_smoke_url",
                url_kind="organic",
                confidence=1.0,
                provider_position=-1,
                page_kind="job_detail_candidate",
                score=1.0,
                reason_codes=("explicit_public_smoke_url",),
            ),
        )
    return tuple(selected[:limit])


async def run_job_research_real_smoke(
    *,
    settings: AgentSettings,
    trust_store: TrustStore,
    project_root: Path,
    run_id: str,
    report_dir: Path,
    source_url: str = DEFAULT_PUBLIC_JD_URL,
    provider_name: str | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Run the live smoke path with a real provider and real Playwright MCP."""

    report_dir.mkdir(parents=True, exist_ok=True)
    provider_name = provider_name or settings.model.default_provider
    model_name = model_name or settings.model.default_model
    provider = ProviderRegistry(settings).get(provider_name)
    ok, health_detail = await provider.health(model_name)
    if not ok:
        raise RuntimeError(f"provider health failed: {health_detail}")

    from starter_agent.bootstrap import create_application

    application = create_application()
    capability_store = application.runtime.gate.store
    network_guard = PlaywrightNetworkGuard()
    manager = McpManager(
        McpConfigLoader(project_root).load(settings.mcp.config_path),
        store=capability_store,
        tool_executor=application.runtime.executor,
        initialize_timeout_seconds=settings.mcp.initialize_timeout_seconds,
        shutdown_timeout_seconds=settings.mcp.shutdown_timeout_seconds,
        browser_network_guard=network_guard,
    )
    trace_events: list[TrustTraceEvent] = []
    try:
        create_smoke_parent_run(
            trust_store,
            run_id=run_id,
            provider=provider_name,
            model=model_name,
            policy_version="live-smoke",
        )
        statuses = await manager.start()
        playwright_status = statuses["playwright"]
        initial = await manager.discover("playwright")
        current = manager.get_status("playwright")
        refreshed = await manager.refresh_server(
            "playwright",
            expected_revision=current.revision,
        )
        session_id = uuid4()
        turn_id = uuid4()
        user_request = (
            "\u6839\u636e\u6211\u7684\u7b80\u5386\u641c\u7d22"
            "\u4e0a\u6d77\u7684 AI Agent \u5c97\u4f4d"
        )
        route = await application.route_knowledge_request(
            content=user_request,
            provider_name=provider_name,
            model=model_name,
        )
        if route.route.value != "job_research":
            raise RuntimeError(f"job research route failed: {route.route.value}")
        search_query = "AI Agent Engineer job description"
        search_location = "Shanghai"
        search = await application.search_job_research(
            query=search_query,
            session_id=session_id,
            turn_id=turn_id,
            location=search_location,
            limit=settings.job_research.max_candidate_urls,
        )
        if search.status != "waiting_for_url_selection":
            raise RuntimeError(
                f"SerpAPI search failed: {search.error_code or search.status}"
            )
        raw_results = search.data.get("results", [])
        candidates = rank_job_candidates(raw_results, limit=settings.job_research.max_candidate_urls)
        if source_url:
            candidates = tuple(
                sorted(
                    candidates,
                    key=lambda item: item.url != source_url,
                )
            )
        if not candidates:
            raise RuntimeError("SerpAPI returned no usable JD candidates")
        smoke_candidates = select_smoke_candidates(
            candidates,
            limit=settings.job_research.max_candidate_urls,
            source_url=source_url,
        )
        async with asyncio.timeout(settings.runtime.max_seconds):
            analyzed = await application.analyze_job_research_candidates(
                query=search_query,
                candidates=smoke_candidates,
                session_id=session_id,
                turn_id=turn_id,
                target_count=1,
                resume_evidence=None,
            )
        jobs = analyzed.data.get("jobs", [])
        if not jobs:
            attempts = analyzed.data.get("candidate_attempts", [])
            diagnostics = [
                {
                    "tool_name": trace.tool_name,
                    "metadata": trace.result.get("metadata", {}),
                }
                for trace in analyzed.trace
                if trace.tool_name.endswith("browser_snapshot")
            ]
            blocked_report = {
                "run_id": run_id,
                "run_type": "smoke",
                "status": "blocked",
                "route_decision": {
                    "route": route.route.value,
                    "reason_code": route.reason_code,
                    "runtime_revision": route.runtime_revision,
                },
                "serpapi_search": {
                    "query_hash": _hash(search_query),
                    "location": search_location,
                    "candidate_count": len(candidates),
                    "candidates": [
                        {
                            "url_hash": _hash(item.url),
                            "url_kind": item.url_kind,
                            "page_kind": item.page_kind,
                            "score": item.score,
                            "reason_codes": list(item.reason_codes),
                        }
                        for item in candidates
                    ],
                },
                "candidate_attempts": attempts,
                "candidate_selection": [
                    {
                        "source": item.source,
                        "url_hash": _hash(item.url),
                    }
                    for item in smoke_candidates
                ],
                "playwright_diagnostics": diagnostics,
                "verified_source_url": None,
                "separate_from_fixture_baseline": True,
            }
            blocked_path = report_dir / f"{run_id}.json"
            blocked_path.write_text(
                json.dumps(
                    blocked_report,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            trust_store.update_run_status(
                run_id,
                status="blocked",
                completed_at=datetime.now(UTC),
            )
            raise RuntimeError(
                "Playwright did not verify any JD candidate: "
                f"attempts={json.dumps(attempts, ensure_ascii=True)[:1200]} "
                f"diagnostics={json.dumps(diagnostics, ensure_ascii=True)[:2400]} "
                f"report={blocked_path}"
            )
        resume_evidence = analyzed.data.get("resume_evidence", [])
        if not resume_evidence:
            raise RuntimeError("local RAG returned no resume evidence")
        job = jobs[0]
        verified_source_url = str(job["source_url"])
        snapshot_validation = {
            "reason_code": "validated_jd",
            "validation_state": str(job.get("validation_state") or "verified"),
            "responsibility_count": len(job.get("responsibilities", [])),
            "requirement_count": len(job.get("requirements", [])),
        }
        excerpt = json.dumps(
            {
                "title": job.get("title"),
                "company": job.get("company"),
                "location": job.get("location"),
                "responsibilities": job.get("responsibilities", [])[:3],
                "requirements": job.get("requirements", [])[:3],
            },
            ensure_ascii=False,
        )[:1500]
        model_response = await provider.complete(
            [
                Message(
                    role="user",
                    content=(
                        f"{SMOKE_MODEL_INSTRUCTION}\n\n"
                        f"source_url: {verified_source_url}\n"
                        f"JD 摘要:\n{excerpt}"
                    ),
                )
            ],
            model_name,
            tools=[],
        )
        for event_type, status, summary in (
            (
                "route",
                "passed",
                {
                    "route": route.route.value,
                    "reason_code": route.reason_code,
                    "runtime_revision": route.runtime_revision,
                },
            ),
            (
                "search",
                "passed",
                {
                    "tool_name": "search_jobs_serpapi",
                    "candidate_count": len(candidates),
                    "query_hash": _hash(search_query),
                },
            ),
            (
                "model",
                "passed",
                {
                    "provider": provider_name,
                    "model": model_name,
                    "health": "ok",
                    "response_chars": len(model_response.content or ""),
                },
            ),
            (
                "tool",
                "passed",
                {
                    "server": "playwright",
                    "runtime": playwright_status.runtime_name,
                    "runtime_version": playwright_status.runtime_version,
                    "snapshot_version": refreshed.version,
                    "tool_count": refreshed.tool_count,
                    "source_url_hash": _hash(verified_source_url),
                    "excerpt_chars": len(excerpt),
                    "candidate_attempt_count": len(
                        analyzed.data.get("candidate_attempts", [])
                    ),
                    **snapshot_validation,
                },
            ),
        ):
            trace = _trace(run_id, event_type, status, summary)
            trust_store.append_trace_event(trace)
            trace_events.append(trace)
        smoke = SmokeRun(
            id=f"{run_id}:smoke",
            run_id=run_id,
            source_url=verified_source_url,
            source_url_hash=_hash(verified_source_url),
            trace_event_ids=tuple(event.id for event in trace_events),
            report_summary={
                "provider": provider_name,
                "model": model_name,
                "mcp_server": "playwright",
                "mcp_tools": refreshed.tool_count,
                "source_url_hash": _hash(verified_source_url),
                "snapshot_excerpt_chars": len(excerpt),
                "snapshot_validation": snapshot_validation,
                "route_decision": route.route.value,
                "resume_evidence_count": len(resume_evidence),
                "serpapi_candidate_count": len(candidates),
                "candidate_attempt_count": len(
                    analyzed.data.get("candidate_attempts", [])
                ),
                "separate_from_fixture_baseline": True,
            },
        )
        trust_store.create_smoke_run(smoke)
        trust_store.update_run_status(
            run_id,
            status="completed",
            completed_at=datetime.now(UTC),
        )
        report = {
            "run_id": run_id,
            "run_type": "smoke",
            "status": "passed",
            "source_url": verified_source_url,
            "source_url_hash": _hash(verified_source_url),
            "provider": provider_name,
            "model": model_name,
            "model_summary": model_response.content,
            "route_decision": {
                "route": route.route.value,
                "reason_code": route.reason_code,
                "runtime_revision": route.runtime_revision,
            },
            "resume_evidence": {
                "count": len(resume_evidence),
                "source_refs": list(
                    str(item.get("source_ref") or "")
                    for item in resume_evidence
                    if isinstance(item, dict) and item.get("source_ref")
                ),
            },
            "serpapi_search": {
                "query_hash": _hash(search_query),
                "location": search_location,
                "candidate_count": len(candidates),
            },
            "candidate_attempts": analyzed.data.get(
                "candidate_attempts", []
            ),
            "candidate_selection": [
                {
                    "source": item.source,
                    "url_hash": _hash(item.url),
                }
                for item in smoke_candidates
            ],
            "verified_source_url": verified_source_url,
            "mcp": {
                "server": "playwright",
                "runtime": playwright_status.runtime_name,
                "runtime_version": playwright_status.runtime_version,
                "node_version": playwright_status.node_version,
                "npx_version": playwright_status.npx_version,
                "initial_snapshot_version": initial.version,
                "snapshot_version": refreshed.version,
                "tool_count": refreshed.tool_count,
                "snapshot_validation": snapshot_validation,
            },
            "trace_event_ids": [event.id for event in trace_events],
            "separate_from_fixture_baseline": True,
        }
        report_path = report_dir / f"{run_id}.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        report["report_path"] = str(report_path)
        return report
    finally:
        try:
            await asyncio.wait_for(
                manager.shutdown(),
                timeout=settings.mcp.shutdown_timeout_seconds + 5,
            )
        except (TimeoutError, OSError):
            pass
        finally:
            try:
                network_guard.dispose()
            except RuntimeError:
                pass


def create_smoke_parent_run(
    store: TrustStore,
    *,
    run_id: str,
    provider: str,
    model: str,
    policy_version: str,
) -> EvalRun:
    existing = store.get_run(run_id)
    if existing is not None:
        return existing
    suite = EvalSuite(
        id="job-research-real-smoke-suite",
        name="Job Research Real Smoke Suite",
        version="v1",
        created_at=datetime.now(UTC),
        case_ids=(),
        metadata_summary={"smoke": "real_playwright_mcp"},
    )
    try:
        store.create_suite(suite)
    except RecordAlreadyExistsError:
        pass
    run = EvalRun(
        id=run_id,
        suite_id=suite.id,
        run_type="smoke",
        status="running",
        started_at=datetime.now(UTC),
        code_version="workspace",
        code_dirty=True,
        prompt_version="live-smoke",
        skill_version="job-research@live-smoke",
        tool_schema_version="playwright-mcp-live",
        policy_version=policy_version,
        fixture_manifest_hash=None,
        config_summary={
            "provider": provider,
            "model": model,
            "separate_from_fixture_baseline": True,
        },
    )
    try:
        return store.create_run(run)
    except RecordAlreadyExistsError:
        existing_after_race = store.get_run(run_id)
        if existing_after_race is None:
            raise
        return existing_after_race


def _mcp_text(result: Any) -> str:
    content = getattr(result, "content", None)
    if isinstance(content, list):
        parts = []
        for item in content:
            text = getattr(item, "text", None)
            if text:
                parts.append(str(text))
        if parts:
            return "\n".join(parts)
    return str(result)


def _trace(
    run_id: str,
    event_type: str,
    status: str,
    summary: dict[str, Any],
) -> TrustTraceEvent:
    payload = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    return TrustTraceEvent(
        id=f"{run_id}:{event_type}:1",
        eval_run_id=run_id,
        event_type=event_type,
        status=status,
        occurred_at=datetime.now(UTC),
        summary=summary,
        payload_hash=sha256(payload.encode("utf-8")).hexdigest(),
        source_ref=f"smoke://{run_id}/{event_type}",
    )


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
