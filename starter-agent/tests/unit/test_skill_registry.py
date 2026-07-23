from pathlib import Path

from starter_agent.capabilities.store import CapabilityStore
from starter_agent.skills.registry import SkillRegistry

from tests.unit.test_skill_parser import VALID_SKILL


def _write_skill(root: Path, text: str = VALID_SKILL) -> Path:
    path = root / "example-skill" / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_registry_publishes_immutable_snapshot_and_enable_override(tmp_path: Path):
    _write_skill(tmp_path)
    registry = SkillRegistry(tmp_path)

    first = registry.reload()
    enabled_catalog = registry.lightweight_catalog()
    disabled = registry.set_enabled("example-skill", False)

    assert first.revision == 1
    assert first.stale is False
    assert first.skills[0].enabled is True
    assert disabled.revision == 2
    assert disabled.skills[0].enabled is False
    assert first.skills[0].enabled is True
    assert enabled_catalog == (
        {
            "name": "example-skill",
            "version": "1.2.0",
            "description": "Research an example safely.",
            "enabled": True,
            "dependency_state": "available",
        },
    )
    assert registry.lightweight_catalog() == ()


def test_failed_reload_keeps_last_good_definitions_and_marks_stale(tmp_path: Path):
    path = _write_skill(tmp_path)
    registry = SkillRegistry(tmp_path)
    good = registry.reload()
    path.write_text("---\nname: broken\n---\n", encoding="utf-8")

    stale = registry.reload()

    assert stale.revision == good.revision + 1
    assert stale.stale is True
    assert stale.last_error
    assert stale.skills == good.skills
    assert registry.get("example-skill") == good.skills[0]


def test_missing_dependency_is_visible_without_removing_skill(tmp_path: Path):
    _write_skill(tmp_path)
    registry = SkillRegistry(
        tmp_path,
        dependency_resolver=lambda dependency: dependency.name != "retrieve_resume_evidence",
    )

    snapshot = registry.reload()

    assert snapshot.skills[0].dependency_state == "dependency_unavailable"
    assert snapshot.skills[0].missing_dependencies == (
        "tool:retrieve_resume_evidence",
    )


def test_enable_override_survives_registry_restart(tmp_path: Path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root)
    store = CapabilityStore("sqlite:///:memory:", tmp_path)
    first = SkillRegistry(skills_root, store=store)
    first.reload()
    first.set_enabled("example-skill", False)

    restarted = SkillRegistry(skills_root, store=store)
    snapshot = restarted.reload()

    assert snapshot.skills[0].enabled is False
    assert store.get_skill("example-skill").enabled is False
