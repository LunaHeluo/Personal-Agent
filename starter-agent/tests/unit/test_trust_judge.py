from datetime import UTC, datetime

from starter_agent.trust.judge import JudgeClientResult, LlmJudgeService
from starter_agent.trust.models import HumanReview, JudgeResult, JudgeRubric
from starter_agent.trust.store import TrustStore


def _rubric() -> JudgeRubric:
    return JudgeRubric(
        id="rubric-semantic-fit-v1",
        suite_id="job-research-regression",
        version="v1",
        criteria=("semantic_quality", "evidence_use"),
        prompt_template="Score semantic quality only. Do not judge permissions.",
        golden_examples=(
            {
                "input": "redacted JD and evidence",
                "score": 4,
                "reason": "grounded and clear",
            },
        ),
        created_at=datetime.now(UTC),
    )


def test_llm_judge_records_model_rubric_raw_score_reason_and_usage() -> None:
    store = TrustStore("sqlite:///:memory:", ".")
    rubric = _rubric()
    store.create_judge_rubric(rubric)

    async def client(_rubric: JudgeRubric, _payload: dict[str, object]) -> JudgeClientResult:
        return JudgeClientResult(
            provider="fixture-provider",
            model="fixture-model",
            raw_score=4.0,
            normalized_score=0.8,
            reason="Answer is grounded in cited resume chunks.",
            usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        )

    result = LlmJudgeService(store=store, enabled=True, client=client).judge_sync(
        id="judge-result-1",
        run_id="run-1",
        case_result_id="result-1",
        rubric=rubric,
        payload={"answer_summary": "redacted"},
    )

    assert result == JudgeResult(
        id="judge-result-1",
        run_id="run-1",
        case_result_id="result-1",
        rubric_id=rubric.id,
        rubric_version=rubric.version,
        provider="fixture-provider",
        model="fixture-model",
        raw_score=4.0,
        normalized_score=0.8,
        reason="Answer is grounded in cited resume chunks.",
        usage_summary={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        created_at=result.created_at,
    )
    assert store.get_judge_result(result.id) == result


def test_llm_judge_can_be_disabled_without_security_side_effects() -> None:
    store = TrustStore("sqlite:///:memory:", ".")
    rubric = _rubric()

    result = LlmJudgeService(store=store, enabled=False, client=None).judge_sync(
        id="judge-disabled",
        run_id="run-1",
        case_result_id="result-1",
        rubric=rubric,
        payload={"answer_summary": "redacted"},
    )

    assert result is None
    assert store.get_judge_result("judge-disabled") is None


def test_human_review_is_persisted_with_run_case_and_rubric_versions() -> None:
    store = TrustStore("sqlite:///:memory:", ".")
    review = HumanReview(
        id="human-review-1",
        run_id="run-1",
        case_id="case-1",
        case_result_id="result-1",
        rubric_id="rubric-semantic-fit-v1",
        rubric_version="v1",
        reviewer="local-reviewer",
        conclusion="needs_fix",
        reason="Citation wording is too broad for the retrieved chunk.",
        created_at=datetime.now(UTC),
    )

    store.create_human_review(review)

    assert store.list_human_reviews(run_id="run-1") == [review]
