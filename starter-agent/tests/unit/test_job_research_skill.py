from pathlib import Path

from starter_agent.agent.context import ContextBuilder
from starter_agent.domain.models import Message
from starter_agent.skills.registry import SkillRegistry
from starter_agent.skills.selector import SkillSelector


SKILLS_ROOT = Path(__file__).parents[2] / "skills"


def test_job_research_definition_contains_fixed_governed_workflow():
    registry = SkillRegistry(SKILLS_ROOT)
    snapshot = registry.reload()
    skill = registry.get("job-research")

    assert snapshot.stale is False
    assert skill is not None
    assert skill.enabled is True
    assert {item.key for item in skill.dependencies} == {
        "tool:search_jobs_serpapi",
        "tool:retrieve_resume_evidence",
        "mcp:mcp__playwright__browser_navigate",
        "service:job_description_ingestion",
    }
    steps = [
        "Use SerpAPI",
        "select a URL",
        "Use Browser",
        "Validate the JD",
        "Use RAG",
        "JD sources",
        "user confirmation",
    ]
    fixed_steps = skill.definition.split("## Fixed Steps", 1)[1].split(
        "## Verification", 1
    )[0]
    positions = [fixed_steps.index(token) for token in steps]
    assert positions == sorted(positions)


def test_selector_triggers_research_but_not_general_advice_or_rewrite():
    registry = SkillRegistry(SKILLS_ROOT)
    registry.reload()
    selector = SkillSelector(registry)

    assert selector.select("请帮我搜索上海的 AI Agent 岗位").name == "job-research"
    assert selector.select("读取这个公开 JD 并和我的简历比较").name == "job-research"
    assert selector.select("给我一些通用求职建议") is None
    assert selector.select("只润色这段已经提供的文字") is None


def test_context_has_light_catalog_until_a_skill_is_triggered(tmp_path: Path):
    identity = tmp_path / "identity.md"
    prompt = tmp_path / "system.md"
    identity.write_text("Agent", encoding="utf-8")
    prompt.write_text("{identity}", encoding="utf-8")
    registry = SkillRegistry(SKILLS_ROOT)
    registry.reload()
    builder = ContextBuilder(
        identity,
        prompt,
        skill_registry=registry,
        skill_selector=SkillSelector(registry),
    )

    idle = builder.build([Message(role="user", content="你好")])
    triggered = builder.build(
        [Message(role="user", content="请搜索 AI Agent 岗位")]
    )

    assert "Enabled Skills" in idle[1].content
    assert "job-research" in idle[1].content
    assert "SerpAPI" not in idle[1].content
    assert "Full Skill Definition: job-research" in triggered[2].content
    assert "SerpAPI" in triggered[2].content
