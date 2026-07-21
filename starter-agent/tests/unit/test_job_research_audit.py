from pathlib import Path


AUDIT_PATH = Path(__file__).parents[2] / "docs" / "job-research-implementation-audit.md"


def test_job_research_audit_records_repository_baseline() -> None:
    audit = AUDIT_PATH.read_text(encoding="utf-8")

    required_source_paths = (
        "src/starter_agent/settings.py",
        "src/starter_agent/bootstrap.py",
        "src/starter_agent/tools/registry.py",
        "src/starter_agent/tools/base.py",
        "src/starter_agent/tools/policy.py",
        "src/starter_agent/agent/runtime.py",
        "src/starter_agent/interfaces/api.py",
        "src/starter_agent/observability/logging.py",
        "src/web/index.html",
    )
    for source_path in required_source_paths:
        assert source_path in audit

    required_capability_names = (
        "search_jobs_serpapi",
        "search_job_description",
        "SafeWebFetcher",
        "JobDescriptionExtractor",
        "/retrieve",
        "KnowledgeApplicationService.retrieve()",
    )
    for capability_name in required_capability_names:
        assert capability_name in audit

    required_schema_facts = (
        "`query`：必填",
        "`location`：可选",
        "`limit`：1–10",
        "禁止额外字段",
    )
    for schema_fact in required_schema_facts:
        assert schema_fact in audit

    assert "`retrieve_resume_evidence` 尚未实现" in audit
    assert "Skill Registry 尚未实现" in audit
