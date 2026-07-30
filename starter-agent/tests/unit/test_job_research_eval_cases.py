from pathlib import Path

import yaml

from starter_agent.trust.models import EvalCase


CASE_FILES = (
    Path("evals/job-research-cases.yaml"),
    Path("evals/job-research-safety-cases.yaml"),
)


def _load_cases() -> list[EvalCase]:
    cases: list[EvalCase] = []
    for path in CASE_FILES:
      payload = yaml.safe_load(path.read_text(encoding="utf-8"))
      cases.extend(EvalCase(**item) for item in payload["cases"])
    return cases


def test_job_research_eval_case_files_cover_required_layers_and_count() -> None:
    cases = _load_cases()
    assert len(cases) >= 12
    assert {
        "happy_path",
        "edge_case",
        "missing_information",
        "tool_failure",
        "conflicting_context",
        "safety_adversarial",
    }.issubset({case.layer for case in cases})


def test_job_research_eval_cases_cover_stage8_safety_regressions() -> None:
    case_ids = {case.id for case in _load_cases()}
    for expected in (
        "jr-tool-disabled-schema-hidden",
        "jr-schema-removed-not-callable",
        "jr-mcp-unavailable-fallback",
        "jr-rag-no-evidence",
        "jr-non-whitelist-approval",
        "jr-forced-approval-cannot-bypass",
        "jr-webpage-injection",
    ):
        assert expected in case_ids


def test_job_research_eval_cases_cover_knowledge_first_routing() -> None:
    case_ids = {case.id for case in _load_cases()}
    assert {
        "jr-conversation-greeting-no-tools",
        "jr-conversation-smalltalk-no-tools",
        "jr-job-knowledge-hit-no-network",
        "jr-job-resume-only-searches-and-reads-jd",
        "jr-job-no-profile-fails-closed",
        "jr-job-search-tool-disabled",
        "jr-job-browser-unavailable",
        "jr-knowledge-fact-no-web-fallback",
    }.issubset(case_ids)


def test_job_research_eval_cases_cover_unified_playwright_routing() -> None:
    case_ids = {case.id for case in _load_cases()}
    assert {
        "JR-ROUTE-FLEXIBLE-001",
        "JR-KB-MATCH-001",
        "JR-KB-LOCATION-MISS-001",
        "JR-KB-ROLE-MISS-001",
        "JR-KB-EXPIRED-001",
        "JR-LATEST-001",
        "JR-URL-FALLTHROUGH-001",
        "JR-MULTI-URL-001",
        "JR-LEGACY-SCHEMA-ABSENT-001",
        "JR-INJECTION-WEB-001",
    }.issubset(case_ids)


def test_job_research_eval_cases_cover_toolchain_reliability_regressions() -> None:
    case_ids = {case.id for case in _load_cases()}
    assert {
        "runtime_revision_stale",
        "collection_candidate_rejected",
        "single_block_jd_verified",
        "error_page_then_valid_jd",
        "partial_company_unverified",
    }.issubset(case_ids)


def test_job_research_eval_cases_have_deterministic_assertions_and_fixtures() -> None:
    for case in _load_cases():
        assert case.fixture_ids
        assert case.expected_outcome
        assert case.deterministic_assertions
        assert case.safety_level in {"standard", "elevated", "hard_gate"}
