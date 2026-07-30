import json
from pathlib import Path

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
