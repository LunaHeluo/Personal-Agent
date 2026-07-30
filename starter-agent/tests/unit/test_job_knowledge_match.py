from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from starter_agent.job_research.knowledge_match import (
    JobResearchCriteria,
    KnowledgeJobMatcher,
)
from starter_agent.knowledge.models import RetrievalMatch


FIXED_NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


def jd_match(
    *,
    location: str = "上海",
    role: str = "Python Backend Engineer",
    age_days: int = 5,
    status: str = "open",
    closing_date: str = "",
) -> RetrievalMatch:
    return RetrievalMatch(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_type="job_description",
        filename="job.md",
        version=1,
        section_path=[role],
        start_line=1,
        end_line=12,
        preview=(
            f"# {role}\n\n"
            f"- Company: Example Corp\n"
            f"- Location: {location}\n"
            f"- Status: {status}\n"
            f"- Closing Date: {closing_date}\n"
            "- Source URL: https://jobs.example.test/42\n\n"
            "## Requirements\n\n- Python\n"
        ),
        source_ref="job.md@v1#L1-L12",
        rank=1,
        created_at=FIXED_NOW - timedelta(days=age_days),
    )


@pytest.mark.parametrize(
    ("location", "role_terms", "age_days", "status", "fresh", "reason"),
    [
        ("上海", ("Python",), 5, "open", False, "matched"),
        ("深圳", ("Python",), 5, "open", False, "location_mismatch"),
        ("上海", ("Go",), 5, "open", False, "role_mismatch"),
        ("上海", ("Python",), 31, "open", False, "expired"),
        ("上海", ("Python",), 5, "closed", False, "closed"),
        ("上海", ("Python",), 5, "open", True, "explicit_freshness"),
    ],
)
def test_knowledge_job_decision_matrix(
    location: str,
    role_terms: tuple[str, ...],
    age_days: int,
    status: str,
    fresh: bool,
    reason: str,
) -> None:
    decision = KnowledgeJobMatcher().evaluate(
        criteria=JobResearchCriteria(
            location=location,
            role_terms=role_terms,
            explicit_freshness=fresh,
        ),
        matches=(jd_match(age_days=age_days, status=status),),
        now=FIXED_NOW,
        freshness_days=30,
    )

    assert decision.reason_code == reason
    assert decision.use_knowledge is (reason == "matched")


def test_missing_jd_and_missing_timestamp_fail_closed() -> None:
    matcher = KnowledgeJobMatcher()
    criteria = JobResearchCriteria(
        location="上海",
        role_terms=("Python",),
        explicit_freshness=False,
    )

    assert matcher.evaluate(
        criteria=criteria,
        matches=(),
        now=FIXED_NOW,
        freshness_days=30,
    ).reason_code == "missing_jd"

    without_time = jd_match().model_copy(update={"created_at": None})
    assert matcher.evaluate(
        criteria=criteria,
        matches=(without_time,),
        now=FIXED_NOW,
        freshness_days=30,
    ).reason_code == "expired"


def test_indexed_job_document_does_not_require_markdown_source_url_marker() -> None:
    match = jd_match().model_copy(
        update={
            "preview": (
                "# Python Backend Engineer\n\n"
                "工作地点：上海\n"
                "岗位职责\n负责 Python 服务开发\n"
                "任职要求\n熟悉 Python\n"
            )
        }
    )

    decision = KnowledgeJobMatcher().evaluate(
        criteria=JobResearchCriteria(
            role_terms=("Python",),
            explicit_freshness=False,
        ),
        matches=(match,),
        now=FIXED_NOW,
        freshness_days=30,
    )

    assert decision.use_knowledge is True
    assert decision.reason_code == "matched"


def test_matching_is_not_limited_to_a_fixed_city_list() -> None:
    match = jd_match(location="Berlin", role="Rust Platform Engineer")
    decision = KnowledgeJobMatcher().evaluate(
        criteria=JobResearchCriteria(
            location="Berlin",
            role_terms=("Rust",),
            explicit_freshness=False,
        ),
        matches=(match,),
        now=FIXED_NOW,
        freshness_days=30,
    )

    assert decision.use_knowledge is True


def test_shared_technology_does_not_hide_a_role_mismatch() -> None:
    match = jd_match(location="上海", role="Python Data Engineer")

    decision = KnowledgeJobMatcher().evaluate(
        criteria=JobResearchCriteria(
            location="上海",
            role_terms=("Python", "Backend", "Engineer"),
        ),
        matches=(match,),
        now=FIXED_NOW,
        freshness_days=30,
    )

    assert decision.use_knowledge is False
    assert decision.reason_code == "role_mismatch"


def test_two_shared_role_terms_allow_resume_profile_to_match_saved_jd() -> None:
    match = jd_match(location="深圳", role="AI Agent Engineer").model_copy(
        update={
            "preview": (
                "# AI Agent Engineer\n\n"
                "- Company: Example Corp\n"
                "- Location: 深圳\n"
                "- Status: open\n"
                "## Requirements\n\n- Large language model applications\n"
            )
        }
    )

    decision = KnowledgeJobMatcher().evaluate(
        criteria=JobResearchCriteria(
            location="深圳",
            role_terms=("Python", "AI", "Agent"),
        ),
        matches=(match,),
        now=FIXED_NOW,
        freshness_days=30,
    )

    assert decision.use_knowledge is True
    assert decision.reason_code == "matched"


def test_past_normalized_closing_date_overrides_recent_created_at() -> None:
    decision = KnowledgeJobMatcher().evaluate(
        criteria=JobResearchCriteria(
            role_terms=("Python", "Backend", "Engineer"),
            explicit_freshness=True,
        ),
        matches=(
            jd_match(location="\u4e0a\u6d77", age_days=1, closing_date="2026-07-26"),
        ),
        now=FIXED_NOW,
        freshness_days=30,
    )

    assert decision.use_knowledge is False
    assert decision.reason_code == "expired"


def test_engineer_and_developer_are_equivalent_role_suffixes() -> None:
    decision = KnowledgeJobMatcher().evaluate(
        criteria=JobResearchCriteria(
            role_terms=("Python", "Backend", "Engineer"),
        ),
        matches=(
            jd_match(location="\u4e0a\u6d77", role="Python Backend Developer"),
        ),
        now=FIXED_NOW,
        freshness_days=30,
    )

    assert decision.use_knowledge is True
    assert decision.reason_code == "matched"
