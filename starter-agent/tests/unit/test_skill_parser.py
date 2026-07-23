from pathlib import Path

import pytest

from starter_agent.skills.parser import SkillParseError, SkillParser


VALID_SKILL = """\
---
name: example-skill
description: Research an example safely.
version: 1.2.0
source: builtin
enabled: true
dependencies:
  tools: [search_jobs_serpapi, retrieve_resume_evidence]
  mcp: [mcp__playwright__browser_navigate]
trigger_examples:
  - 搜索 AI 岗位
negative_examples:
  - 给我通用求职建议
validation:
  - final source URL is present
failure_policy:
  - stop on missing evidence
---
# Example Skill

## Inputs

- query

## Steps

1. Search.
"""


def test_parser_loads_typed_frontmatter_and_preserves_full_definition(tmp_path: Path):
    path = tmp_path / "example-skill" / "SKILL.md"
    path.parent.mkdir()
    path.write_text(VALID_SKILL, encoding="utf-8")

    skill = SkillParser().parse_file(path)

    assert skill.name == "example-skill"
    assert skill.version == "1.2.0"
    assert skill.enabled is True
    assert [(item.kind, item.name) for item in skill.dependencies] == [
        ("tool", "search_jobs_serpapi"),
        ("tool", "retrieve_resume_evidence"),
        ("mcp", "mcp__playwright__browser_navigate"),
    ]
    assert skill.trigger_examples == ("搜索 AI 岗位",)
    assert skill.negative_examples == ("给我通用求职建议",)
    assert "## Steps" in skill.definition
    assert len(skill.snapshot_hash) == 64


@pytest.mark.parametrize(
    "source",
    [
        "# missing frontmatter",
        VALID_SKILL.replace("name: example-skill\n", ""),
        VALID_SKILL.replace("trigger_examples:\n  - 搜索 AI 岗位\n", ""),
        VALID_SKILL.replace("dependencies:\n", "dependencies: invalid\n#"),
    ],
)
def test_parser_rejects_incomplete_or_malformed_skill(source: str, tmp_path: Path):
    path = tmp_path / "broken" / "SKILL.md"
    path.parent.mkdir()
    path.write_text(source, encoding="utf-8")

    with pytest.raises(SkillParseError):
        SkillParser().parse_file(path)
