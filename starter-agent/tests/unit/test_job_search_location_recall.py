import json
from pathlib import Path

from starter_agent.domain.models import ToolResult
from starter_agent.interfaces.api import _public_job_candidates
from starter_agent.job_research.diagnostics import compare_location_recall


FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "job_search"
    / "location-alias-serpapi.json"
)


def test_fixed_fixture_reports_improved_local_language_recall_and_ranking() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    report = compare_location_recall(fixture)

    assert report.before_chinese_title_count == 1
    assert report.after_chinese_title_count == 5
    assert report.raw_result_count == 6
    assert report.deduplicated_count == 6
    assert report.top_ten[0]["title"] == "AI 智能体算法工程师"
    assert "target_location_match" in report.top_ten[0]["reason_codes"]
    assert report.top_ten[-1]["title"] == "AI Engineer - MLabs"


def test_public_candidates_preserve_location_alias_ranking() -> None:
    result = ToolResult(
        ok=True,
        data={
            "location_aliases": ["北京", "Beijing"],
            "results": [
                {
                    "title": "AI Agent Engineer",
                    "company": "Target Employer",
                    "location": "Beijing",
                    "url": "https://target.example.test/openings/agent-1",
                    "url_kind": "organic",
                    "provider_position": 1,
                },
                {
                    "title": "AI Agent Engineer",
                    "company": "Other Employer",
                    "location": "New York",
                    "url": "https://other.example.test/jobs/agent-2",
                    "url_kind": "organic",
                    "provider_position": 0,
                    "snippet": (
                        "Responsibilities: build agents. "
                        "Requirements: production Python."
                    ),
                },
            ],
        },
    )

    candidates = _public_job_candidates(result)

    assert [candidate.company for candidate in candidates] == [
        "Target Employer",
        "Other Employer",
    ]
