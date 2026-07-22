import pytest

from starter_agent.job_research.jd import JobDescriptionNormalizer


def _complete_payload() -> dict:
    return {
        "title": "Agent Engineer",
        "company": "Example Corp",
        "location": "Shanghai",
        "responsibilities": ["Build agent systems"],
        "requirements": ["Python experience"],
        "final_url": "https://jobs.example/roles/42",
        "content_sha256": "a" * 64,
    }


def test_normalizer_marks_complete_jd_and_builds_field_level_source_refs() -> None:
    normalized = JobDescriptionNormalizer().normalize(
        _complete_payload(),
        call_id="call-42",
        snapshot_id="snapshot-7",
        schema_hash="b" * 64,
    )

    assert normalized.is_complete is True
    assert normalized.completeness_reasons == ()
    assert set(normalized.field_source_refs) == {
        "title", "company", "location", "responsibilities", "requirements"
    }
    assert normalized.field_source_refs["title"].source_url == normalized.source_url
    assert normalized.field_source_refs["title"].call_id == "call-42"


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"final_url": ""}, "missing_final_url"),
        ({"requirements": []}, "missing_requirements"),
        ({"is_truncated": True}, "truncated_source"),
        ({"page_type": "listing"}, "listing_page"),
        ({"page_type": "login"}, "login_wall"),
        (
            {"final_url": "https://jobs.example/42?access_token=private"},
            "missing_final_url",
        ),
    ],
)
def test_normalizer_fails_closed_for_unverifiable_sources(
    updates: dict, reason: str
) -> None:
    payload = _complete_payload()
    payload.update(updates)

    normalized = JobDescriptionNormalizer().normalize(payload)

    assert normalized.is_complete is False
    assert reason in normalized.completeness_reasons
