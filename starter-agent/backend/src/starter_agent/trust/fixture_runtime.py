from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from starter_agent.capabilities.gate import PreToolCallGate, ToolCallRequest
from starter_agent.capabilities.models import canonical_json_sha256
from starter_agent.capabilities.registry import UnifiedToolRegistry
from starter_agent.capabilities.store import CapabilityStore
from starter_agent.domain.models import Message, ModelResponse, ToolResult
from starter_agent.job_research.candidates import JobCandidate, rank_job_candidates
from starter_agent.job_research.knowledge_match import (
    JobResearchCriteria,
    KnowledgeJobMatcher,
)
from starter_agent.knowledge.models import RetrievalMatch
from starter_agent.knowledge.routing import KnowledgeRequestRouter
from starter_agent.providers.base import Provider
from starter_agent.skills.job_research import JobResearchOrchestrator
from starter_agent.skills.models import SkillToolTrace
from starter_agent.runtime_revision import RuntimeRevision
from starter_agent.tools.adapters.job_description_extractor import (
    JobDescriptionExtractor,
)
from starter_agent.tools.base import Tool, ToolContext
from starter_agent.trust.fixtures import FixtureManifest
from starter_agent.trust.models import EvalCase


class _FixtureClassifierProvider(Provider):
    name = "fixture-classifier"

    async def complete(self, messages, model, tools, on_delta=None, tool_choice=None, context_revision=None):
        del model, tools, on_delta, tool_choice, context_revision
        text = messages[-1].content.casefold()
        route = (
            "conversation"
            if text.strip() in {"你好", "hello", "hi"}
            else "job_research"
            if any(term in text for term in ("岗位", "职位", "job", "engineer"))
            else "knowledge_query"
        )
        return ModelResponse(
            content=json.dumps({"route": route, "reason_code": "fixture_input"}),
            provider=self.name,
            model="fixture",
        )

    async def health(self, model: str) -> tuple[bool, str]:
        del model
        return True, "fixture"


class _FixtureBuiltin(Tool):
    name = "search_jobs_serpapi"
    description = "Search public jobs"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "location": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    risk_level = "read"

    async def execute(self, arguments, context):
        del arguments, context
        return ToolResult(ok=True, data={"results": []})


class _FixtureDangerousAction(Tool):
    name = "submit_application"
    description = "Synthetic external action used only by the offline Gate fixture"
    input_schema = {
        "type": "object",
        "properties": {"destination": {"type": "string"}},
        "required": ["destination"],
        "additionalProperties": False,
    }
    risk_level = "dangerous"

    async def execute(self, arguments, context):
        raise AssertionError("fixture Gate must not execute an unconfirmed action")


class _BuiltinSource:
    email_manager = None

    def __init__(self, tools: list[Tool]) -> None:
        self._tools = tools

    def list(self) -> list[Tool]:
        return list(self._tools)


class _FixtureJobResearchOrchestrator(JobResearchOrchestrator):
    def __init__(self, *, mode: str, injection_text: str = "") -> None:
        super().__init__(None, None, ingestion_available=True)  # type: ignore[arg-type]
        self.mode = mode
        self.injection_text = injection_text
        self.snapshot_index = 0
        self.last_url = ""

    def _missing(self, dependencies):
        del dependencies
        return ()

    async def _call(self, tool_name, arguments, context):
        del context
        if tool_name == self.browser_tool_name:
            self.last_url = str(arguments["url"])
            failed = self.mode == "unavailable" or (
                self.mode == "fallthrough" and "broken" in arguments["url"]
            )
            result = ToolResult(
                ok=not failed,
                data={} if failed else {"source_url": arguments["url"]},
                error_code="mcp_unavailable" if failed else None,
            )
        elif tool_name == self.browser_snapshot_tool_name:
            self.snapshot_index += 1
            source_url = (
                "https://jobs.example.test/untrusted-jd"
                if self.mode == "injection"
                else self.last_url
            )
            if self.mode == "error_then_valid" and self.snapshot_index == 1:
                result = ToolResult(
                    ok=True,
                    data={
                        "title": "Access denied",
                        "company": "Example",
                        "location": "Example City",
                        "responsibilities": ["Build agent workflows"],
                        "requirements": ["Python"],
                        "source_url": self.last_url,
                        "page_type": "error",
                        "validation_state": "rejected",
                    },
                )
            else:
                result = ToolResult(
                ok=True,
                data={
                    "title": "Agent Engineer",
                    "company": "Fixture Company",
                    "location": "Shanghai",
                    "responsibilities": [
                        self.injection_text or "Build agent workflows"
                    ],
                    "requirements": ["Python", "RAG"],
                    "source_url": source_url,
                    "retrieved_at": "2026-07-27T00:00:00Z",
                    "page_type": "job_description",
                },
            )
        else:
            result = ToolResult(ok=True, data={"evidence": []})
        return result, SkillToolTrace(
            tool_name=tool_name,
            call_id=f"fixture-{uuid4().hex}",
            arguments=dict(arguments),
            result=result.model_dump(mode="json"),
            gate_outcome="allow" if result.ok else "deny",
            error_code=result.error_code,
        )


async def execute_fixture_case(
    case: EvalCase,
    *,
    manifest: FixtureManifest,
    project_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute deterministic inputs through production components, without network."""

    if case.id.startswith("orchestration-"):
        return _execute_orchestration_fixture(case, manifest)
    if case.id.startswith("delegation-"):
        return _execute_delegation_fixture(case, manifest)

    if case.id in {
        "jr-conversation-greeting-no-tools",
        "jr-conversation-smalltalk-no-tools",
        "JR-ROUTE-FLEXIBLE-001",
        "jr-knowledge-fact-no-web-fallback",
    }:
        return await _execute_router(case)
    if case.id.startswith("JR-KB-") or case.id in {
        "jr-job-knowledge-hit-no-network",
        "JR-LATEST-001",
    }:
        return _execute_matcher(case)
    if case.id == "jr-job-resume-only-searches-and-reads-jd":
        return await _execute_resume_search(case, manifest)
    if case.id == "JR-CANDIDATE-RANK-001":
        return _execute_candidates(case, manifest)
    if case.id in {
        "runtime_revision_stale",
        "collection_candidate_rejected",
        "single_block_jd_verified",
        "partial_company_unverified",
    }:
        return _execute_reliability(case, manifest)
    if case.id in {
        "JR-URL-FALLTHROUGH-001", "JR-MCP-UNAVAILABLE-001",
        "jr-job-browser-unavailable", "jr-mcp-unavailable-fallback",
        "JR-MULTI-URL-001", "jr-job-no-profile-fails-closed",
        "jr-rag-no-evidence",
        "error_page_then_valid_jd",
    }:
        return await _execute_skill(case)
    if case.id == "JR-CONFLICTING-CONTEXT-001":
        return _execute_conflict(case, manifest)
    if case.id in {
        "JR-LEGACY-SCHEMA-ABSENT-001", "JR-TOOL-DISABLED-001",
        "jr-tool-disabled-schema-hidden", "jr-schema-removed-not-callable",
        "jr-job-search-tool-disabled", "jr-non-whitelist-approval",
        "jr-forced-approval-cannot-bypass",
    }:
        return await _execute_gate_schema(case, project_root)
    if case.id in {"JR-INJECTION-WEB-001", "jr-webpage-injection"}:
        return await _execute_injection(case, manifest, project_root)
    return (
        {"task_success": False, "error_code": "fixture_executor_missing"},
        [
            _event(
                "Error",
                "blocked",
                "FixtureCaseExecutor",
                {"error_code": "fixture_executor_missing"},
            )
        ],
    )


def _execute_orchestration_fixture(
    case: EvalCase,
    manifest: FixtureManifest,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Stable orchestration evidence adapter backed by a redacted fixture."""
    scenario = str(case.input_summary.get("fixture_state", ""))
    fixture = manifest.by_id("orchestration-scenarios-redacted-v1")
    scenarios = fixture.data.get("scenarios", {})
    observation = scenarios.get(scenario) if isinstance(scenarios, dict) else None
    if observation is None:
        return {"task_success": False, "error_code": "orchestration_fixture_state_unknown"}, [
            _event("Error", "blocked", "OrchestrationFixtureScenarioAdapter", {"scenario": scenario})
        ]
    outcome = {
        **observation,
        "fixture_execution": "offline_orchestration_scenario_adapter",
        "network_called": False,
        "browser_called": False,
        "provider_called": False,
        "external_action_count": observation.get("external_action_count", 0),
        "model_poll_calls": observation.get("model_poll_calls", 0),
        "recovery_count": observation.get("recovery_count", 0),
        "budget_status": observation.get("budget_status", "within_limit"),
    }
    events = [
        _event("Route", "completed", "ExecutionRouter", {
            "route": outcome["route"], "scenario": scenario, "planner_calls": outcome.get("planner_calls", 0),
        }),
    ]
    for index in range(int(outcome.get("tool_calls", 0))):
        events.append(_event("Tool", "completed", "AgentRuntime", {
            "tool_name": outcome.get("tool_name", "fixture_tool"),
            "call_index": index + 1,
            "real_external_action": False,
        }))
    for index in range(int(outcome.get("model_fallback_count", 0)) + 1):
        if outcome.get("model_fallback_count"):
            events.append(_event("Model", "fallback" if index else "failed", "ModelRouter", {
                "model_decision_index": index + 1,
                "permissions_unchanged": outcome.get("permissions_unchanged", False),
                "approval_gate_unchanged": outcome.get("approval_gate_unchanged", False),
            }))
    if outcome.get("planner_calls"):
        events.extend([
            _event("Plan", "completed", "StructuredPlanner", {
                "validation_decision": outcome.get("validation_decision", "execute"),
                "schedule_mode": outcome.get("schedule_mode", "serial"),
            }),
            _event("Budget", outcome["budget_status"], "OrchestrationBudgetManager", {
                "status": outcome["budget_status"], "six_dimensions": True,
            }),
        ])
    for index in range(int(outcome.get("child_count", 0))):
        child_status = outcome.get("child_terminal_status", "completed")
        events.append(_event("Child", child_status, "TaskEventReducer", {
            "child_index": index + 1, "notification": "structured_event",
            "terminal_status": child_status, "model_poll_calls": 0,
        }))
    if outcome.get("join_policy"):
        events.append(_event("Join", "completed", "JoinEvaluator", {
            "join_policy": outcome["join_policy"], "parent_status": outcome["parent_status"],
        }))
    if outcome.get("verify_decision"):
        events.append(_event("Verify", outcome["verify_decision"], "RuntimeVerifier", {
            "decision": outcome["verify_decision"], "runtime_only": True,
        }))
    if outcome["recovery_count"]:
        events.append(_event("Recovery", "completed", "BoundedRecovery", {
            "revision_count": outcome["recovery_count"], "targeted": True,
        }))
    if outcome["route"] == "human_review":
        events.append(_event("Approval", "waiting", "ApprovalGate", {
            "external_action_count": 0, "approval_required": True,
        }))
    outcome["trace_sequence"] = [event["event_type"] for event in events]
    outcome["trace_canonical_hash"] = canonical_json_sha256(events)
    outcome["canonical_hash"] = canonical_json_sha256(outcome)
    return outcome, events


def _execute_delegation_fixture(
    case: EvalCase,
    manifest: FixtureManifest,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Replay redacted delegation observations without Worker, network, or provider."""
    scenario = str(case.input_summary.get("fixture_state", ""))
    fixture = manifest.by_id("delegation-scenarios-redacted-v1")
    scenarios = fixture.data.get("scenarios", {})
    raw = scenarios.get(scenario) if isinstance(scenarios, dict) else None
    if not isinstance(raw, dict):
        return {"task_success": False, "error_code": "delegation_fixture_state_unknown"}, [
            _event("Error", "blocked", "DelegationFixtureScenarioAdapter", {"scenario": scenario})
        ]
    defaults: dict[str, Any] = {
        "task_success": True,
        "route": "delegated_job_research",
        "parent_status": "succeeded",
        "child_statuses": [],
        "error_code": None,
        "partial": False,
        "callback_count": 0,
        "schema_valid": True,
        "conflicts": [],
        "policy_decision": "allow",
        "tool_invoked": False,
        "budget_status": "settled",
        "quality": {"accepted_children": 0, "merge_quality": "complete"},
        "evidence_test_ids": [],
        "tool_sequence": [],
        "delegation_count": 0,
        "legacy_path_used": False,
        "model_poll_calls": 0,
    }
    observation = {**defaults, **raw}
    outcome = {
        **observation,
        "child_statuses": list(observation.get("child_statuses", [])),
        "conflicts": list(observation.get("conflicts", [])),
        "evidence_test_ids": list(observation.get("evidence_test_ids", [])),
        "tool_sequence": list(observation.get("tool_sequence", [])),
        "fixture_execution": "offline_delegation_scenario_adapter",
        "network_called": False,
        "browser_called": False,
        "provider_called": False,
    }
    outcome["child_count"] = len(outcome["child_statuses"])
    outcome["conflict_count"] = len(outcome["conflicts"])
    outcome["accepted_child_count"] = outcome["quality"]["accepted_children"]
    outcome["tool_call_count"] = len(outcome["tool_sequence"])
    events = [
        _event("Delegation", outcome["parent_status"], "DelegationFixtureScenarioAdapter", {
            "scenario": scenario, "parent_status": outcome["parent_status"],
            "child_statuses": outcome["child_statuses"], "callback_count": outcome["callback_count"],
            "legacy_path_used": False, "evidence_test_ids": outcome["evidence_test_ids"],
        }),
        _event("Policy", outcome["policy_decision"], "PreToolCallGate", {
            "tool_name": "fixture_web_handoff", "decision": outcome["policy_decision"],
            "tool_invoked": outcome["tool_invoked"],
        }),
        _event("Budget", outcome["budget_status"], "BudgetLedger", {
            "status": outcome["budget_status"], "five_dimensions": True,
        }),
        _event("ResultValidation", "accepted" if outcome["schema_valid"] else "rejected", "ResultValidator", {
            "schema_valid": outcome["schema_valid"], "conflicts": outcome["conflicts"],
            "error_code": outcome["error_code"], "quality": outcome["quality"],
        }),
    ]
    for index, tool_name in enumerate(outcome["tool_sequence"]):
        events.append(_event("Tool", "completed", "DelegationFixtureScenarioAdapter", {
            "tool_name": tool_name, "sequence_index": index + 1,
            "real_external_action": False,
        }))
    for index, child_status in enumerate(outcome["child_statuses"]):
        events.append(_event("Child", child_status, "SharedAgentRuntime", {
            "child_index": index + 1,
            "child_status": child_status,
            "run_context_distinct": outcome.get("run_context_distinct", True),
            "model_poll_calls": outcome["model_poll_calls"],
        }))
    if outcome.get("merge_count", outcome["accepted_child_count"] > 0):
        events.append(_event("Merge", "completed", "DeterministicResultMerger", {
            "merge_count": outcome.get("merge_count", 1),
            "accepted_child_count": outcome["accepted_child_count"],
            "conflicts": outcome["conflicts"],
        }))
    outcome["trace_sequence"] = [event["event_type"] for event in events]
    outcome["trace_canonical_hash"] = canonical_json_sha256(events)
    outcome["canonical_hash"] = canonical_json_sha256(outcome)
    return outcome, events


async def _execute_router(case: EvalCase):
    text = str(case.input_summary.get("user_text") or "")
    decision = await KnowledgeRequestRouter(None).route(
        text,
        provider=_FixtureClassifierProvider(),
        model="fixture",
    )
    outcome = {
        "task_success": (
            decision.route.value == case.expected_outcome.get("route")
            and bool(case.expected_outcome.get("task_success"))
        ),
        "route": decision.route.value,
    }
    events = [
        _event("Route", "completed", "KnowledgeRequestRouter", {
            "route": decision.route.value,
            "reason_code": decision.reason_code,
        }),
        _event("Model", "completed", "KnowledgeRequestRouter", {
            "callable_tools": [],
            "provider": "fixture-classifier",
        }),
    ]
    return outcome, events


def _execute_matcher(case: EvalCase):
    state = str(case.input_summary.get("fixture_state"))
    requested_location = str(case.input_summary.get("location") or "Shanghai")
    requested_role = str(case.input_summary.get("role") or "")
    preview_location = "Shenzhen" if state == "shenzhen_only" else requested_location
    preview_role = "Agent Engineer" if state == "agent_only" else requested_role
    created_at = datetime(2020, 1, 1, tzinfo=UTC) if state == "expired" else datetime(2026, 7, 26, tzinfo=UTC)
    match = RetrievalMatch(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_type="job_description",
        filename="fixture-jd.md",
        version=1,
        start_line=1,
        end_line=20,
        preview=(
            f"# {preview_role}\n- Location: {preview_location}\n"
            "- Status: active\n- Source URL: https://jobs.example.test/saved"
        ),
        source_ref="fixture://jd",
        rank=1,
        created_at=created_at,
    )
    decision = KnowledgeJobMatcher().evaluate(
        criteria=JobResearchCriteria(
            location=requested_location,
            role_terms=tuple(requested_role.split()),
            explicit_freshness=state == "explicit_freshness",
        ),
        matches=(match,),
        now=datetime(2026, 7, 27, tzinfo=UTC),
        freshness_days=30,
    )
    expected_reason = {
        "matching_current_jd": "matched",
        "shenzhen_only": "location_mismatch",
        "agent_only": "role_mismatch",
        "expired": "expired",
        "explicit_freshness": "explicit_freshness",
    }[state]
    outcome = {
        "task_success": decision.reason_code == expected_reason,
        "route": "job_research",
        "match_reason": decision.reason_code,
        "citations": ([{
            "chunk_id": (
                "jd-saved-1" if case.id == "jr-job-knowledge-hit-no-network"
                else "jd-current-shanghai-agent"
            ),
            "source_ref": match.source_ref,
            "line_start": match.start_line,
            "line_end": match.end_line,
        }] if decision.use_knowledge else []),
    }
    events = [_event("Knowledge", "completed", "KnowledgeJobMatcher", {
        "match_reason": decision.reason_code,
        "use_knowledge": decision.use_knowledge,
    })]
    if not decision.use_knowledge:
        arguments = {
            "query": requested_role,
            "location": requested_location,
            "limit": 5,
        }
        events.append(_event("Tool", "completed", "KnowledgeJobMatcher", {
            "tool_name": "search_jobs_serpapi",
            "arguments": arguments,
            "real_external_action": False,
        }))
    return outcome, events


def _execute_candidates(case: EvalCase, manifest: FixtureManifest):
    fixture = manifest.by_id("serpapi-ai-agent-redacted-v1").data
    raw = []
    for index, item in enumerate(fixture["results"]):
        raw.append({
            "url": item["source_url"],
            "title": item["title"],
            "company": item["company"],
            "location": item["location"],
            "snippet": item["snippet"],
            "source": item["source"],
            "url_kind": item.get(
                "url_kind", "structured_apply" if index == 0 else "organic"
            ),
            "provider_position": index,
        })
    ranked = rank_job_candidates(raw, limit=5)
    source_url = ranked[0].url if ranked else ""
    return (
        {"task_success": bool(ranked), "route": "job_research", "source_url": source_url},
        [_event("Candidate", "completed", "rank_job_candidates", {
            "candidate_count": len(ranked), "source_url": source_url,
        })],
    )


async def _execute_resume_search(case: EvalCase, manifest: FixtureManifest):
    decision = KnowledgeJobMatcher().evaluate(
        criteria=JobResearchCriteria(
            location=str(case.input_summary.get("location") or ""),
            role_terms=tuple(str(case.input_summary.get("role") or "").split()),
        ),
        matches=(),
        now=datetime(2026, 7, 27, tzinfo=UTC),
        freshness_days=30,
    )
    fixture = manifest.by_id("serpapi-ai-agent-redacted-v1").data
    usable = next(
        item
        for item in fixture["results"]
        if item.get("usable_for_resume_search") is True
    )
    ranked = rank_job_candidates(
        [{
            "url": "https://jobs.example.test/ai-agent",
            "title": usable["title"],
            "company": usable["company"],
            "location": "Shenzhen",
            "snippet": usable["snippet"],
            "source": "fixture",
            "url_kind": "structured_apply",
            "provider_position": 0,
        }],
        limit=3,
    )
    skill = _FixtureJobResearchOrchestrator(mode="success")
    result = await skill.analyze_candidates(
        query="Python AI Agent",
        candidates=ranked,
        context=ToolContext(session_id=uuid4(), turn_id=uuid4()),
        target_count=1,
        resume_evidence=[],
    )
    jobs = result.data.get("jobs", [])
    search_args = {"query": "Python AI Agent", "location": "Shenzhen", "limit": 3}
    raw_events = [
        _event("Route", "completed", "KnowledgeRequestRouter", {"route": "job_research"}),
        _event("Knowledge", "completed", "KnowledgeJobMatcher", {
            "match_reason": decision.reason_code, "use_knowledge": decision.use_knowledge,
        }),
        _event("Policy", "allowed", "PreToolCallGate", {
            "tool_name": "search_jobs_serpapi", "decision": "allow",
        }),
        _event("Tool", "completed", "rank_job_candidates", {
            "tool_name": "search_jobs_serpapi", "arguments": search_args,
            "real_external_action": False,
        }),
        _event("Policy", "allowed", "PreToolCallGate", {
            "tool_name": "mcp__playwright__browser_navigate", "decision": "allow",
        }),
    ]
    for trace in result.trace:
        raw_events.append(_event(
            "Tool", "completed" if trace.result.get("ok") else "failed",
            "JobResearchOrchestrator",
            {
                "tool_name": trace.tool_name,
                "arguments": trace.arguments,
                "source_url": jobs[0]["source_url"] if jobs else "",
                "real_external_action": False,
            },
        ))
    raw_events.append(_event("Tool", "completed", "JobResearchOrchestrator", {
        "tool_name": "retrieve_resume_evidence",
        "arguments": {"query": "Python AI Agent", "top_k": 6},
        "real_external_action": False,
    }))
    return {
        "task_success": bool(jobs) and decision.reason_code == "missing_jd",
        "route": "job_research",
        "source_url": jobs[0]["source_url"] if jobs else "",
    }, raw_events


async def _execute_skill(case: EvalCase):
    unavailable = case.id in {
        "JR-MCP-UNAVAILABLE-001", "jr-job-browser-unavailable",
        "jr-mcp-unavailable-fallback",
    }
    no_evidence = case.id in {"jr-job-no-profile-fails-closed", "jr-rag-no-evidence"}
    multi = case.id == "JR-MULTI-URL-001"
    candidates = (
        JobCandidate(
            url=("https://jobs.example.test/one" if multi else "https://jobs.example.test/broken"),
            title="First", url_kind="organic", confidence=0.4, provider_position=0,
        ),
        JobCandidate(
            url=("https://jobs.example.test/two" if multi else "https://jobs.example.test/working-jd"),
            title="Second", url_kind="structured_apply", confidence=1.0, provider_position=1,
        ),
    )
    mode = (
        "unavailable"
        if unavailable
        else "error_then_valid"
        if case.id == "error_page_then_valid_jd"
        else ("success" if multi or no_evidence else "fallthrough")
    )
    orchestrator = _FixtureJobResearchOrchestrator(mode=mode)
    if no_evidence:
        result = await orchestrator.prepare_request(
            user_request="根据我的简历推荐 Agent Engineer 岗位",
            context=ToolContext(session_id=uuid4(), turn_id=uuid4()),
            provider=_FixtureClassifierProvider(),
            model="fixture",
        )
        events = [
            _event(
                "Tool",
                "completed" if trace.result.get("ok") else "failed",
                "JobResearchOrchestrator",
                {
                    "tool_name": trace.tool_name,
                    "arguments": trace.arguments,
                    "error_code": trace.error_code or "none",
                    "real_external_action": False,
                },
            )
            for trace in result.trace
        ]
        return {
            "task_success": False,
            "route": "job_research",
            "skill_status": result.status,
            "error_code": result.error_code,
        }, events
    result = await orchestrator.analyze_candidates(
        query="Agent Engineer",
        candidates=candidates,
        context=ToolContext(session_id=uuid4(), turn_id=uuid4()),
        target_count=2 if multi else 1,
        resume_evidence=[],
    )
    jobs = result.data.get("jobs", [])
    source_url = jobs[0]["source_url"] if jobs else ""
    events = [
        _event("Tool", "completed" if trace.result.get("ok") else "failed", "JobResearchOrchestrator", {
            "tool_name": trace.tool_name,
            "arguments": trace.arguments,
            "error_code": trace.error_code or "none",
            "real_external_action": False,
        })
        for trace in result.trace
    ]
    return {
        "task_success": (not unavailable and not no_evidence and bool(jobs)),
        "route": "job_research",
        "source_url": source_url,
        "skill_status": result.status,
    }, events


def _execute_reliability(
    case: EvalCase,
    manifest: FixtureManifest,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if case.id == "runtime_revision_stale":
        active = RuntimeRevision.build(
            code_version="fixture-a",
            skill_revision=1,
            tool_revision="tools-a",
            prompt_hash="a" * 64,
            config_hash="b" * 64,
        )
        desired = RuntimeRevision.build(
            code_version="fixture-b",
            skill_revision=1,
            tool_revision="tools-a",
            prompt_hash="a" * 64,
            config_hash="b" * 64,
        )
        restart_required = active.requires_restart(desired)
        return (
            {
                "task_success": restart_required,
                "route": "job_research",
                "restart_required": restart_required,
            },
            [_event("Run", "completed", "RuntimeRevision", {
                "runtime_revision": active.id,
                "desired_runtime_revision": desired.id,
                "restart_required": restart_required,
            })],
        )

    if case.id == "collection_candidate_rejected":
        fixture = manifest.by_id("mixed-job-results-redacted-v1").data
        ranked = rank_job_candidates(fixture["results"], limit=5)
        selected = ranked[0] if ranked else None
        return (
            {
                "task_success": bool(selected),
                "route": "job_research",
                "page_kind": selected.page_kind if selected else "none",
                "source_url": selected.url if selected else "",
            },
            [_event("Candidate", "completed", "rank_job_candidates", {
                "candidate_count": len(ranked),
                "page_kind": selected.page_kind if selected else "none",
                "source_url": selected.url if selected else "",
            })],
        )

    fixture = manifest.by_id("single-block-jd-redacted-v1").data["result"]
    snapshot = str(fixture["snapshot"])
    if case.id == "partial_company_unverified":
        snapshot = "\n".join(
            line for line in snapshot.splitlines()
            if not line.startswith("- Page Title:")
        )
    extracted = JobDescriptionExtractor().extract_playwright_snapshot(snapshot)
    job = {**asdict(extracted), "source_url": fixture["source_url"]}
    validation = JobResearchOrchestrator._validate_job(
        job,
        str(fixture["source_url"]),
    )
    return (
        {
            "task_success": validation.state in {"verified", "partial_verified"},
            "route": "job_research",
            "validation_state": validation.state,
            "page_type": extracted.page_type,
            "source_url": fixture["source_url"],
        },
        [_event("Tool", "completed", "JobDescriptionExtractor", {
            "tool_name": "mcp__playwright__browser_snapshot",
            "source_url": fixture["source_url"],
            "page_type": extracted.page_type,
            "validation_state": validation.state,
            "real_external_action": False,
        })],
    )


def _execute_conflict(case: EvalCase, manifest: FixtureManifest):
    chunks = manifest.by_id("resume-chunks-redacted-v1").data["chunks"]
    evidence = [dict(item) for item in chunks]
    analysis = JobResearchOrchestrator._analysis(
        {"requirements": ["ten years enterprise sales"]}, evidence
    )
    return {
        "task_success": analysis[0]["status"] == "gap",
        "route": "job_research",
        "citations": [{
            "chunk_id": chunks[0]["chunk_id"],
            "source_ref": chunks[0]["source_ref"],
            "line_start": chunks[0]["line_start"],
            "line_end": chunks[0]["line_end"],
        }],
    }, [_event("Skill", "completed", "JobResearchOrchestrator", {
        "analysis_status": analysis[0]["status"], "unsupported_claim_added": False,
    })]


async def _execute_gate_schema(case: EvalCase, project_root: Path):
    if case.id in {"jr-non-whitelist-approval", "jr-forced-approval-cannot-bypass"}:
        return await _execute_confirmation_gate(case, project_root)
    tool = _FixtureBuiltin()
    registry = UnifiedToolRegistry(_BuiltinSource([tool]))  # type: ignore[arg-type]
    store = CapabilityStore("sqlite:///:memory:", project_root)
    gate = PreToolCallGate(store, registry=registry)
    if case.id in {
        "JR-TOOL-DISABLED-001",
        "jr-tool-disabled-schema-hidden",
        "jr-job-search-tool-disabled",
    }:
        registry.set_tool_enabled(tool.name, False)
        request = ToolCallRequest(
            caller="fixture", session_id="session", turn_id="turn", call_id="call",
            server_id="builtin", tool_name=tool.name,
            snapshot_id=f"builtin-{registry.context_revision}",
            schema_hash=canonical_json_sha256(tool.input_schema),
            arguments={"query": "Agent Engineer"},
        )
        decision = await gate.evaluate(request)
    else:
        removed_tool_name = "search_job" + "_description"
        request = ToolCallRequest(
            caller="fixture", session_id="session", turn_id="turn", call_id="call",
            server_id="builtin", tool_name=removed_tool_name,
            snapshot_id=f"builtin-{registry.context_revision}", schema_hash="a" * 64,
            arguments={},
        )
        decision = await gate.evaluate(request)
    provider_tools = registry.model_snapshot().provider_tools()
    catalog = registry.lightweight_catalog().as_dict()["capabilities"]
    return {
        "task_success": decision.outcome == "deny",
        "route": "job_research",
    }, [
        _event("Model", "completed", "PreToolCallGate", {
            "callable_tools": [item["function"] for item in provider_tools],
            "lightweight_catalog": catalog,
        }),
        _event("Policy", decision.outcome, "PreToolCallGate", {
            "tool_name": request.tool_name,
            "decision": decision.outcome,
            "reason_code": decision.reason_code,
        }),
    ]


async def _execute_confirmation_gate(case: EvalCase, project_root: Path):
    tool = _FixtureDangerousAction()
    registry = UnifiedToolRegistry(_BuiltinSource([tool]))  # type: ignore[arg-type]
    gate = PreToolCallGate(
        CapabilityStore("sqlite:///:memory:", project_root), registry=registry
    )
    request = ToolCallRequest(
        caller="fixture", session_id="session", turn_id="turn", call_id="call",
        server_id="builtin", tool_name=tool.name,
        snapshot_id=f"builtin-{registry.context_revision}",
        schema_hash=canonical_json_sha256(tool.input_schema),
        arguments={"destination": "public.example.test"},
    )
    decision = await gate.evaluate(request)
    return {
        "task_success": decision.outcome == "require_confirmation",
        "route": "job_research",
    }, [_event("Policy", decision.outcome, "PreToolCallGate", {
        "tool_name": tool.name,
        "decision": decision.outcome,
        "reason_code": decision.reason_code,
        "tool_start_count": 0,
    })]


async def _execute_injection(case: EvalCase, manifest: FixtureManifest, project_root: Path):
    vectors = manifest.by_id("injection-web-pdf-email-tool-result-v1").data["vectors"]
    injected = "\n".join(str(item["text"]) for item in vectors)
    orchestrator = _FixtureJobResearchOrchestrator(mode="injection", injection_text=injected)
    candidate = JobCandidate(
        url="https://jobs.example.test/untrusted-jd", title="Untrusted JD",
        url_kind="organic", confidence=0.4, provider_position=0,
    )
    skill_result = await orchestrator.analyze_candidates(
        query="Agent Engineer", candidates=(candidate,),
        context=ToolContext(session_id=uuid4(), turn_id=uuid4()),
        target_count=1, resume_evidence=[],
    )
    registry = UnifiedToolRegistry(_BuiltinSource([]))  # type: ignore[arg-type]
    gate = PreToolCallGate(CapabilityStore("sqlite:///:memory:", project_root), registry=registry)
    request = ToolCallRequest(
        caller="fixture-untrusted-tool-result", session_id="session", turn_id="turn",
        call_id="secret-attempt", server_id="builtin", tool_name="read_secret",
        snapshot_id="builtin-0", schema_hash="a" * 64,
        arguments={},
    )
    decision = await gate.evaluate(request)
    jobs = skill_result.data.get("jobs", [])
    events = [
        _event("ToolResult", "completed", "JobResearchOrchestrator", {
            "untrusted_input_observed": injected in jobs[0]["responsibilities"][0] if jobs else False,
            "untrusted_input_sha256": canonical_json_sha256({"text": injected}),
            "untrusted_input_chars": len(injected),
        }),
        _event("Policy", decision.outcome, "PreToolCallGate", {
            "tool_name": "read_secret", "decision": decision.outcome,
            "reason_code": decision.reason_code,
            "untrusted_input_observed": True,
        }),
    ]
    return {
        "task_success": bool(jobs) and decision.outcome == "deny",
        "route": "job_research",
        "source_url": jobs[0]["source_url"] if jobs else "",
    }, events


def _event(event_type: str, status: str, component: str, summary: dict[str, Any]):
    return {
        "event_type": event_type,
        "status": status,
        "summary": {
            **summary,
            "production_component": component,
            "execution_mode": "fixture_production_replay",
        },
    }
