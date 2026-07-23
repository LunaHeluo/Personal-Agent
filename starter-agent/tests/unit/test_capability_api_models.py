from __future__ import annotations

import pytest
from pydantic import ValidationError
from datetime import UTC, datetime
from pathlib import Path

from starter_agent.capabilities.models import Server, Snapshot, Tool, canonical_json_sha256
from starter_agent.capabilities.store import CapabilityStore, RevisionConflictError
from starter_agent.skills.registry import SkillCandidateChangedError, SkillRegistry
from tests.unit.test_skill_parser import VALID_SKILL
from starter_agent.interfaces.capabilities_api import (
    ManagementMutation,
    PolicyMutation,
)


def test_management_mutation_requires_non_negative_revision() -> None:
    assert ManagementMutation(expected_revision=3).expected_revision == 3
    with pytest.raises(ValidationError):
        ManagementMutation(expected_revision=-1)


def test_policy_mutation_models_bounded_scope_rules() -> None:
    request = PolicyMutation(
        expected_revision=2,
        effect="always_confirm",
        domains=["example.com"],
        actions=["navigate"],
        parameter_constraints={"limit": {"max": 10}},
    )
    assert request.effect == "always_confirm"
    assert request.domains == ("example.com",)


def test_mcp_tool_state_is_persisted_with_per_tool_cas() -> None:
    store = CapabilityStore("sqlite:///:memory:", Path("."))
    store.create_server(
        Server(
            id="alpha",
            name="alpha",
            config_source="mcp.json",
            config_hash="a" * 64,
        )
    )
    schema = {"type": "object"}
    tool = Tool(
        snapshot_id="snapshot-alpha",
        server_id="alpha",
        upstream_name="write_job",
        model_alias="write_job",
        input_schema=schema,
        schema_hash=canonical_json_sha256(schema),
        enabled=False,
        review_state="unreviewed",
    )
    snapshot = Snapshot(
        id=tool.snapshot_id,
        server_id=tool.server_id,
        version=1,
        schema_hash="a" * 64,
        discovered_at=datetime.now(UTC),
        tool_count=1,
    )
    store.create_snapshot(snapshot, tools=(tool,))

    updated = store.update_tool(
        tool.snapshot_id,
        tool.upstream_name,
        expected_revision=0,
        enabled=True,
        review_state="approved",
    )

    assert updated.revision == 1
    assert store.list_tools(tool.snapshot_id)[0] == updated
    with pytest.raises(RevisionConflictError):
        store.update_tool(
            tool.snapshot_id,
            tool.upstream_name,
            expected_revision=0,
            enabled=False,
        )

    store.activate_snapshot("alpha", snapshot.id)
    next_tool = tool.model_copy(update={"snapshot_id": "snapshot-alpha-2"})
    next_snapshot = snapshot.model_copy(
        update={"id": next_tool.snapshot_id, "version": 2}
    )
    store.create_snapshot(next_snapshot, tools=(next_tool,))
    store.activate_refreshed_snapshot("alpha", next_snapshot.id)
    refreshed = store.list_tools(next_snapshot.id)[0]
    assert refreshed.enabled is True
    assert refreshed.review_state == "approved"
    assert refreshed.revision == updated.revision


def test_skill_reload_candidate_hash_blocks_toctou_and_does_not_publish(tmp_path) -> None:
    path = tmp_path / "example-skill" / "SKILL.md"
    path.parent.mkdir()
    path.write_text(VALID_SKILL, encoding="utf-8")
    registry = SkillRegistry(tmp_path)
    initial = registry.reload()
    candidate = registry.prepare_reload("example-skill")
    path.write_text(VALID_SKILL.replace("1.2.0", "1.2.1"), encoding="utf-8")

    with pytest.raises(SkillCandidateChangedError):
        registry.reload_one(
            "example-skill",
            expected_revision=initial.revision,
            expected_candidate_hash=candidate.snapshot_hash,
        )

    assert registry.snapshot() == initial
