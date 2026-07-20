from uuid import uuid4

import pytest

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

