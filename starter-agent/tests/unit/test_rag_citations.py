from uuid import uuid4

import pytest
from pydantic import ValidationError

from starter_agent.knowledge.citations import assemble_citations
from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.models import Evidence, GeneratedClaim


def test_citation_metadata_is_assembled_from_evidence() -> None:
    evidence = Evidence(
        evidence_id="E1",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="resume.md",
        version=2,
        section_path=["技能"],
        start_line=3,
        end_line=4,
        text="熟练 Python 和 SQL。",
    )
    claims = [
        GeneratedClaim(
            text="候选人熟练 Python。",
            evidence_ids=["E1"],
            quote="熟练 Python",
        )
    ]

    citations = assemble_citations(claims, [evidence])

    assert citations[0].filename == "resume.md"
    assert citations[0].document_version == 2
    assert citations[0].quote == "熟练 Python"


def test_distinct_quotes_are_validated_per_evidence() -> None:
    resume = Evidence(
        evidence_id="E1",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="resume.md",
        version=1,
        start_line=1,
        end_line=1,
        text="候选人熟悉 Python。",
    )
    job = Evidence(
        evidence_id="E2",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="job.md",
        version=1,
        start_line=1,
        end_line=1,
        text="岗位要求熟悉 Python。",
    )
    claim = GeneratedClaim.model_validate(
        {
            "text": "候选人的 Python 能力符合岗位要求。",
            "evidence_refs": [
                {"evidence_id": "E1", "quote": "候选人熟悉 Python"},
                {"evidence_id": "E2", "quote": "岗位要求熟悉 Python"},
            ],
        }
    )

    citations = assemble_citations([claim], [resume, job])

    assert [item.chunk_id for item in citations] == [
        resume.chunk_id,
        job.chunk_id,
    ]
    assert [item.quote for item in citations] == [
        "候选人熟悉 Python",
        "岗位要求熟悉 Python",
    ]
    assert claim.evidence_ids == ["E1", "E2"]
    assert claim.quote == "候选人熟悉 Python"


def test_legacy_single_evidence_populates_new_reference_shape() -> None:
    claim = GeneratedClaim(
        text="候选人熟悉 Python。",
        evidence_ids=["E1"],
        quote="熟悉 Python",
    )

    assert [item.model_dump() for item in claim.evidence_refs] == [
        {"evidence_id": "E1", "quote": "熟悉 Python"}
    ]
    assert claim.model_dump()["evidence_ids"] == ["E1"]
    assert claim.model_dump()["quote"] == "熟悉 Python"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "text": "claim",
            "evidence_refs": [
                {"evidence_id": "E1", "quote": "one"},
                {"evidence_id": "E1", "quote": "two"},
            ],
        },
        {
            "text": "claim",
            "evidence_refs": [
                {"evidence_id": "E1", "quote": "one"}
            ],
            "evidence_ids": ["E1"],
            "quote": "one",
        },
        {
            "text": "claim",
            "evidence_refs": [
                {"evidence_id": "E1", "quote": "   "}
            ],
        },
    ],
)
def test_invalid_reference_shapes_are_rejected(payload) -> None:
    with pytest.raises(ValidationError):
        GeneratedClaim.model_validate(payload)


def test_unknown_evidence_or_non_contiguous_quote_is_rejected() -> None:
    evidence = Evidence(
        evidence_id="E1",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="resume.md",
        version=1,
        start_line=1,
        end_line=1,
        text="Python SQL",
    )
    claim = GeneratedClaim(
        text="claim", evidence_ids=["E2"], quote="invented"
    )

    with pytest.raises(KnowledgeError) as error:
        assemble_citations([claim], [evidence])

    assert error.value.code == "citation_validation_failed"

