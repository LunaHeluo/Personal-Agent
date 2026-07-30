import json
from datetime import date, datetime

import pytest

from starter_agent.job_research.jd import JobDescriptionNormalizer


def _artifact(**updates) -> dict:
    data = {
        "title": "Agent Engineer",
        "company": "Example Corp",
        "location": "Shanghai",
        "responsibilities": ["Build agent systems"],
        "requirements": ["Python experience"],
        "final_url": "https://jobs.example/roles/42",
        "content_sha256": "a" * 64,
    }
    data.update(updates.pop("data", {}))
    artifact = {
        "source_ref": "tool:jobs:turn-real:call-real",
        "content": json.dumps(
            {
                "ok": True,
                "data": data,
                "metadata": {
                    "call_id": "forged-call",
                    "snapshot_id": "forged-snapshot",
                    "schema_hash": "f" * 64,
                },
            }
        ),
        "restricted": True,
        "server_id": "jobs",
        "call_id": "call-real",
        "snapshot_id": "snapshot-real",
        "schema_hash": "b" * 64,
        "final_url": data.get("final_url"),
        "source_content_sha256": data.get("content_sha256"),
        "content_sha256": "c" * 64,
        "truncation_summary": {"reason": "token_budget"},
    }
    artifact.update(updates)
    return artifact


def test_normalizer_uses_restricted_artifact_provenance_for_field_source_refs() -> None:
    normalized = JobDescriptionNormalizer().normalize_artifact(_artifact())

    assert normalized.is_complete is True
    assert normalized.completeness_reasons == ()
    assert set(normalized.field_source_refs) == {
        "title", "company", "location", "responsibilities", "requirements"
    }
    source = normalized.field_source_refs["title"]
    assert source.source_url == "https://jobs.example/roles/42"
    assert source.call_id == "call-real"
    assert source.snapshot_id == "snapshot-real"
    assert source.schema_hash == "b" * 64
    assert source.artifact_ref == "tool:jobs:turn-real:call-real"


@pytest.mark.parametrize(
    ("artifact", "reason"),
    [
        (_artifact(final_url=""), "missing_final_url"),
        (_artifact(data={"requirements": []}), "missing_requirements"),
        (_artifact(data={"page_type": "listing"}), "listing_page"),
        (_artifact(data={"page_type": "login"}), "login_wall"),
        (
            _artifact(final_url="https://jobs.example/42?access_token=private"),
            "missing_final_url",
        ),
    ],
)
def test_normalizer_fails_closed_for_unverifiable_artifacts(
    artifact: dict, reason: str
) -> None:
    normalized = JobDescriptionNormalizer().normalize_artifact(artifact)

    assert normalized.is_complete is False
    assert reason in normalized.completeness_reasons


def test_unrestricted_or_caller_claimed_truncation_recovery_is_rejected() -> None:
    untrusted = _artifact(
        restricted=False,
        data={"requirements": [], "truncation_recovered": True},
    )

    with pytest.raises(ValueError, match="restricted_artifact_required"):
        JobDescriptionNormalizer().normalize_artifact(untrusted)


def test_normalizer_preserves_optional_freshness_and_status_metadata() -> None:
    normalized = JobDescriptionNormalizer().normalize_artifact(
        _artifact(
            data={
                "retrieved_at": "2026-07-27T08:30:00Z",
                "status": "open",
                "closing_date": "2026-08-31",
            }
        )
    )

    assert normalized.retrieved_at == datetime.fromisoformat(
        "2026-07-27T08:30:00+00:00"
    )
    assert normalized.status == "open"
    assert normalized.closing_date == date(2026, 8, 31)
    markdown = normalized.to_markdown()
    assert "- Retrieved At: 2026-07-27T08:30:00+00:00" in markdown
    assert "- Status: open" in markdown
    assert "- Closing Date: 2026-08-31" in markdown
