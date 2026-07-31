from uuid import uuid4

import pytest

from starter_agent.job_research.selection import parse_job_selection_reference


@pytest.mark.parametrize(
    ("message", "ordinal"),
    [
        ("选择第一个岗位做匹配分析", 1),
        ("选择第二个岗位", 2),
        ("选 2", 2),
        ("第 3 个", 3),
        ("Candidate 4", 4),
    ],
)
def test_parse_job_selection_ordinal(message: str, ordinal: int) -> None:
    reference = parse_job_selection_reference(message)

    assert reference is not None
    assert reference.ordinal == ordinal
    assert reference.candidate_id is None


def test_parse_exact_candidate_id() -> None:
    candidate_id = uuid4()

    reference = parse_job_selection_reference(
        f"选择 Candidate ID：{candidate_id} 做匹配"
    )

    assert reference is not None
    assert reference.candidate_id == candidate_id
    assert reference.ordinal is None


@pytest.mark.parametrize(
    "message",
    [
        "根据我的简历搜索北京岗位",
        "第一个问题是什么",
        "Candidate experience summary",
        "选择岗位方向",
    ],
)
def test_non_selection_message_is_not_claimed(message: str) -> None:
    assert parse_job_selection_reference(message) is None
