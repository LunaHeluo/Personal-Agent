from __future__ import annotations

import pytest
from pydantic import ValidationError

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

