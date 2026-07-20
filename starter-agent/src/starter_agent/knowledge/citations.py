from __future__ import annotations

from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.models import Citation, Evidence, GeneratedClaim


def assemble_citations(
    claims: list[GeneratedClaim], evidence: list[Evidence]
) -> list[Citation]:
    available = {item.evidence_id: item for item in evidence}
    citations: list[Citation] = []
    for claim_index, claim in enumerate(claims, start=1):
        if not claim.evidence_ids:
            raise KnowledgeError("citation_validation_failed")
        for evidence_index, evidence_id in enumerate(
            claim.evidence_ids, start=1
        ):
            item = available.get(evidence_id)
            if item is None or not claim.quote or claim.quote not in item.text:
                raise KnowledgeError("citation_validation_failed")
            citations.append(
                Citation(
                    citation_id=f"C{claim_index}.{evidence_index}",
                    document_id=item.document_id,
                    filename=item.filename,
                    document_version=item.version,
                    chunk_id=item.chunk_id,
                    page=item.page,
                    section=" / ".join(item.section_path) or None,
                    start_line=item.start_line,
                    end_line=item.end_line,
                    quote=claim.quote,
                )
            )
    return citations
