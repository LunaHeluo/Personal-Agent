from __future__ import annotations

import pytest

from tests.integration.test_mcp_refresh_isolation import (
    test_refresh_is_per_server_and_leases_pin_client_generation as _refresh_isolation_case,
)
from tests.unit.test_mcp_refresh_state_machine import (
    test_refresh_swaps_only_valid_candidate_and_invalidates_changed_schema as _refresh_state_case,
)


@pytest.mark.asyncio
async def test_refresh_success_failure_stale_rollback_and_schema_invalidation(
    tmp_path,
) -> None:
    """Changed schema invalidates authority; failed refresh keeps the old generation."""
    await _refresh_state_case(tmp_path)


@pytest.mark.asyncio
async def test_refresh_concurrency_isolation_and_inflight_generation_binding(
    tmp_path,
) -> None:
    """Same-server refresh serializes while other servers and old leases continue."""
    await _refresh_isolation_case(tmp_path)
