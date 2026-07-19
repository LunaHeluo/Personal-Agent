from uuid import uuid4

import pytest

from starter_agent.knowledge.chunker import KnowledgeChunker
from starter_agent.knowledge.errors import KnowledgeError
from starter_agent.knowledge.models import KnowledgeScope
from starter_agent.knowledge.parser import MarkdownParser


def test_chunker_is_deterministic_and_preserves_location() -> None:
    parsed = MarkdownParser().parse(
        "# Resume\n\n## Skills\n\nPython and SQL.\n\n## Experience\n\nBuilt a safe demo.\n"
    )
    values = dict(
        parsed=parsed,
        scope=KnowledgeScope(user_id="u", project_id="p"),
        knowledge_base_id=uuid4(),
        document_id=uuid4(),
        version_id=uuid4(),
        version=1,
        filename="resume.md",
    )
    chunker = KnowledgeChunker(target_chars=30, max_chars=80, overlap_chars=5)

    first = chunker.chunk(**values)
    second = chunker.chunk(**values)

    assert [item.content_sha256 for item in first] == [
        item.content_sha256 for item in second
    ]
    assert first[0].section_path == ["Resume", "Skills"]
    assert first[0].start_line == 5
    assert first[0].source_ref.endswith("#L5-L5")


def test_chunker_rejects_heading_only_document() -> None:
    parsed = MarkdownParser().parse("# Resume\n\n## Skills\n")

    with pytest.raises(KnowledgeError) as error:
        KnowledgeChunker().chunk(
            parsed=parsed,
            scope=KnowledgeScope(user_id="u", project_id="p"),
            knowledge_base_id=uuid4(),
            document_id=uuid4(),
            version_id=uuid4(),
            version=1,
            filename="resume.md",
        )

    assert error.value.code == "document_no_indexable_content"
