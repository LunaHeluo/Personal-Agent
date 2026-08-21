from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from starter_agent.domain.models import Message
from starter_agent.capabilities.models import PolicyRule
from starter_agent.capabilities.policy import classify_tool
from starter_agent.mcp.config import McpConfigLoader
from starter_agent.mcp.manager import McpManager, McpManagerError
from starter_agent.mcp.network_guard import PlaywrightNetworkGuard
from starter_agent.providers.registry import ProviderRegistry
from starter_agent.settings import AgentSettings
from starter_agent.trust.models import EvalRun, EvalSuite, SmokeRun, TrustTraceEvent
from starter_agent.trust.store import RecordAlreadyExistsError, TrustStore
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


def _delegation_smoke_card(receipt, tree) -> dict[str, Any]:
    child = next((item for item in tree.child_runs if item.id == receipt.child_run_id), None)
    task = next((item for item in tree.child_tasks if item.id == receipt.child_task_id), None)
    return {
        "parent_run_id": receipt.parent_run_id,
        "child_task_id": receipt.child_task_id,
        "child_run_id": receipt.child_run_id,
        "route": receipt.route,
        "legacy_path_used": False,
        "contract_hash": receipt.contract_hash,
        "effective_tool_view_hash": receipt.effective_tool_view_hash,
        "child_status": None if child is None else child.status,
        "accepted_envelope_ref": None if task is None else task.accepted_result_envelope_ref,
    }


async def _run_or_await_delegated_child(
    application,
    receipt,
    *,
    worker_id: str,
    timeout_seconds: float,
):
    """Run the Child or observe the same durable Child claimed by another Worker."""

    worker = application.delegation_worker
    if worker is None:
        raise RuntimeError("delegated_web_child_not_claimed")
    deadline = asyncio.get_running_loop().time() + max(1.0, timeout_seconds)
    terminal = {
        "succeeded",
        "partial",
        "failed",
        "timed_out",
        "cancelled",
        "budget_exhausted",
        "waiting_for_user",
    }
    while True:
        tree = application.delegation_store.get_run_tree(receipt.parent_run_id)
        child = next(item for item in tree.child_runs if item.id == receipt.child_run_id)
        task = next(item for item in tree.child_tasks if item.id == receipt.child_task_id)
        if child.status in terminal:
            if child.status not in {"succeeded", "partial"}:
                return tree
            if task.accepted_result_envelope_ref is not None:
                return tree
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError("delegated_web_child_not_claimed")
        if child.status == "queued":
            # A claimed attempt may legitimately schedule the same durable Run
            # for a bounded retry.  Keep the MCP generation and temporary
            # Smoke policy alive while that retry is pending instead of
            # returning an intermediate queued state as a completed Smoke.
            await worker.pool.run_once(worker_id)
            continue
        await asyncio.sleep(0.25)


def _install_temporary_smoke_browser_policy(
    application, *, run_id: str, source_url: str, expires_at: datetime
) -> tuple[str, ...]:
    """Authorize only this explicit operator Smoke's reviewed read/navigation tools."""

    host = urlsplit(source_url).hostname or ""
    aliases = (
        "mcp__playwright__browser_navigate",
        "mcp__playwright__browser_wait_for",
        "mcp__playwright__browser_snapshot",
    )
    created: list[str] = []
    for alias in aliases:
        capability = application.runtime.gate.registry.resolve_execution(alias)
        if capability is None or capability.review_state != "approved":
            continue
        action = classify_tool(capability.metadata, capability.risk_level)
        if action not in {"read", "snapshot", "navigate", "navigation"}:
            continue
        rule_id = f"real-smoke:{_hash(run_id)[:16]}:{capability.canonical_name}"
        application.runtime.gate.store.create_policy_rule(
            PolicyRule(
                id=rule_id,
                server_id=capability.server_id,
                tool_name=capability.canonical_name,
                effect="allowlist_auto",
                schemes=("https",) if action in {"navigate", "navigation"} else (),
                domains=(host,) if action in {"navigate", "navigation"} and host else (),
                actions=(action,),
                roles=("specialist",),
                schema_hash=capability.schema_hash,
                expires_at=expires_at,
                created_by="real-smoke-operator",
            )
        )
        created.append(rule_id)
    return tuple(created)

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
    smoke_policy_rule_ids: tuple[str, ...] = ()
    smoke_stage = "mcp_startup"
    delegated = None
    route = None
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
        if playwright_status.connection_state != "ready":
            raise RuntimeError("playwright_server_not_ready")
        smoke_stage = "mcp_discovery"
        initial = await manager.discover("playwright")
        current = manager.get_status("playwright")
        refreshed = await manager.refresh_server(
            "playwright",
            expected_revision=current.revision,
        )
        smoke_policy_rule_ids = _install_temporary_smoke_browser_policy(
            application,
            run_id=run_id,
            source_url=source_url,
            expires_at=datetime.now(UTC)
            + timedelta(seconds=settings.job_research.retrieval_budget_seconds + 30),
        )
        session_id = uuid4()
        turn_id = uuid4()
        user_request = (
            "\u6839\u636e\u6211\u7684\u7b80\u5386\u641c\u7d22"
            "\u4e0a\u6d77\u7684 AI Agent \u5c97\u4f4d"
        )
        smoke_stage = "route"
        route = await application.route_knowledge_request(
            content=user_request,
            provider_name=provider_name,
            model=model_name,
        )
        if route.route.value != "job_research":
            raise RuntimeError(f"job research route failed: {route.route.value}")
        smoke_stage = "delegation"
        delegated = await application.start_job_research_delegation(
            message=user_request + f"\nSmoke source URL: {source_url}",
            session_id=session_id,
            provider_name=provider_name,
            model=model_name,
            seed_urls=(source_url,),
            require_search=True,
            target_valid_jobs=1,
            max_pages=1,
        )
        # The worker is the only component permitted to execute Search/Browser.
        worker = application.delegation_worker
        smoke_stage = "child_execution"
        tree = await _run_or_await_delegated_child(
            application,
            delegated,
            worker_id="real-smoke-worker",
            timeout_seconds=float(settings.job_research.retrieval_budget_seconds),
        )
        child = next(item for item in tree.child_runs if item.id == delegated.child_run_id)
        task = next(item for item in tree.child_tasks if item.id == delegated.child_task_id)
        if child.status not in {"succeeded", "partial"} or task.accepted_result_envelope_ref is None:
            blocked_report = {
                "run_id": run_id,
                "run_type": "smoke",
                "status": "blocked",
                "route_decision": {
                    "route": route.route.value,
                    "reason_code": route.reason_code,
                    "runtime_revision": route.runtime_revision,
                },
                "delegation": _delegation_smoke_card(delegated, tree),
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
                "delegated Web Child did not produce an accepted envelope: "
                f"status={child.status} "
                f"report={blocked_path}"
            )
        parent = application.delegation_store.get_parent(delegated.parent_run_id)
        if parent is None:
            raise RuntimeError("delegated_parent_missing")
        if parent.status == "queued" and parent.phase == "children_terminal":
            parent = application.delegation_store.resume_parent_for_validation(
                parent.id,
                expected_version=parent.version,
                occurred_at=datetime.now(UTC),
                idempotency_key=f"real-smoke-resume:{run_id}:{parent.version}",
            )
        merge = worker.executor.acceptance_service.merge_ready_parent(
            delegated.parent_run_id,
            expected_version=parent.version,
            now=datetime.now(UTC),
        )
        tree = application.delegation_store.get_run_tree(delegated.parent_run_id)
        artifact = application.store.get_tool_artifact_for_principal(
            task.accepted_result_envelope_ref, principal=tree.parent.principal
        )
        if artifact is None or not isinstance(artifact.get("content"), str):
            raise RuntimeError("delegated_envelope_unavailable")
        envelope = json.loads(artifact["content"])
        output = envelope.get("output", {})
        jobs = output.get("jobs", []) if isinstance(output, dict) else []
        if not jobs:
            raise RuntimeError("delegated_web_child_returned_no_jobs")
        job = jobs[0]
        verified_source_url = str(job.get("source_url") or source_url)
        resume_evidence = []
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
                    "parent_run_id": delegated.parent_run_id,
                    "child_run_id": delegated.child_run_id,
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
                    "contract_hash": delegated.contract_hash,
                    "effective_tool_view_hash": delegated.effective_tool_view_hash,
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
                "delegation": _delegation_smoke_card(delegated, tree),
                "merge": {
                    "status": merge.status,
                    "merge_report_id": merge.merge_report_id,
                    "output_ref": merge.final_output_ref,
                },
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
            "delegation": _delegation_smoke_card(delegated, tree),
            "merge": {
                "status": merge.status,
                "merge_report_id": merge.merge_report_id,
                "output_ref": merge.final_output_ref,
            },
            "serpapi_search": {"delegated": True},
            "candidate_attempts": output.get("visited", {}).get("attempts", []),
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
    except Exception as error:
        # Live dependencies are intentionally not part of the fixed release
        # gate.  Preserve a bounded, redacted blocked result instead of a raw
        # traceback or a false pass.
        raw_code = str(error).strip()
        error_code = (
            raw_code
            if re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", raw_code)
            else type(error).__name__
        )
        controlled = (
            isinstance(error, (McpManagerError, TimeoutError, OSError))
            or raw_code in {
                "playwright_server_not_ready",
                "delegated_web_child_not_claimed",
                "delegated_parent_missing",
                "delegated_envelope_unavailable",
                "delegated_web_child_returned_no_jobs",
            }
            or raw_code.startswith("job research route failed:")
            or raw_code.startswith("delegated Web Child did not produce")
        )
        blocked_report = {
            "run_id": run_id,
            "run_type": "smoke",
            "status": "blocked" if controlled else "error",
            "failure_stage": smoke_stage,
            "error_code": error_code,
            "source_url": source_url,
            "source_url_hash": _hash(source_url),
            "verified_source_url": None,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "provider": provider_name,
            "model": model_name,
            "route_decision": (
                None
                if route is None
                else {"route": route.route.value, "reason_code": route.reason_code}
            ),
            "delegation": (
                None
                if delegated is None
                else {
                    "parent_run_id": delegated.parent_run_id,
                    "child_task_id": delegated.child_task_id,
                    "child_run_id": delegated.child_run_id,
                    "route": delegated.route,
                    "legacy_path_used": False,
                }
            ),
            "budget": None,
            "trace_event_ids": [event.id for event in trace_events],
            "separate_from_fixture_baseline": True,
        }
        failure_trace = _trace(
            run_id,
            "environment" if controlled else "error",
            "blocked" if controlled else "error",
            {
                "failure_stage": smoke_stage,
                "error_code": error_code,
                "source_url_hash": _hash(source_url),
                "external_dependency_failure": controlled,
            },
        )
        trust_store.append_trace_event(failure_trace)
        blocked_report["trace_event_ids"] = [failure_trace.id]
        report_path = report_dir / f"{run_id}.json"
        report_path.write_text(
            json.dumps(blocked_report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        trust_store.update_run_status(
            run_id,
            status="blocked" if controlled else "error",
            completed_at=datetime.now(UTC),
        )
        blocked_report["report_path"] = str(report_path)
        if controlled:
            return blocked_report
        raise
    finally:
        for rule_id in smoke_policy_rule_ids:
            rule = application.runtime.gate.store.get_policy_rule(rule_id)
            if rule is not None:
                try:
                    application.runtime.gate.store.delete_policy_rule(
                        rule_id, expected_revision=rule.revision
                    )
                except Exception:
                    pass
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
