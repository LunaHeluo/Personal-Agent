from starter_agent.knowledge.parser import MarkdownParser


def test_parser_preserves_sections_blocks_and_source_lines() -> None:
    source = "\ufeff# 简历\r\n\r\n## 技能\r\n\r\n- Python\r\n- SQL\r\n\r\n```py\r\nprint('ok')\r\n```\r\n"

    parsed = MarkdownParser().parse(source)

    assert parsed.normalized_source.startswith("# 简历\n")
    assert [block.kind for block in parsed.blocks] == ["list", "code"]
    assert parsed.blocks[0].section_path == ["简历", "技能"]
    assert parsed.blocks[0].start_line == 5
    assert parsed.blocks[0].end_line == 6
    assert parsed.blocks[1].text == "```py\nprint('ok')\n```"


def test_setext_heading_and_table_are_recognized() -> None:
    parsed = MarkdownParser().parse(
        "项目经验\n========\n\n| 项目 | 结果 |\n| --- | --- |\n| Orion | 20% |\n"
    )

    assert len(parsed.blocks) == 1
    assert parsed.blocks[0].kind == "table"
    assert parsed.blocks[0].section_path == ["项目经验"]

