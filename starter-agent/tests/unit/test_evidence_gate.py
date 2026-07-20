from uuid import uuid4

from starter_agent.knowledge.evidence import EvidenceSufficiencyGate
from starter_agent.knowledge.models import Evidence


def evidence(text: str, evidence_id: str = "E1") -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename=f"{evidence_id}.md",
        version=1,
        start_line=1,
        end_line=1,
        text=text,
    )


def test_gate_refuses_empty_and_missing_question_number() -> None:
    gate = EvidenceSufficiencyGate()

    assert gate.evaluate("HR 邮箱是什么？", []).reason == "no_evidence"
    decision = gate.evaluate(
        "候选人是否提升了 35%？",
        [evidence("候选人负责性能优化。")],
    )

    assert decision.allowed is False
    assert decision.reason == "insufficient_evidence"


def test_gate_marks_conflicting_numbers_from_multiple_documents() -> None:
    decision = EvidenceSufficiencyGate().evaluate(
        "项目提升多少？",
        [evidence("项目提升 20%。", "E1"), evidence("项目提升 35%。", "E2")],
    )

    assert decision.allowed is True
    assert decision.conflict is True

