from __future__ import annotations

import pytest
from starlette.requests import Request

from starter_agent.interfaces.capabilities_api import (
    ManagementPrincipal,
    PrincipalResolver,
    require_role,
)


def _request(host: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "client": (host, 1234),
        }
    )


@pytest.mark.asyncio
async def test_local_resolver_only_trusts_loopback() -> None:
    resolver = PrincipalResolver()
    principal = await resolver(_request("127.0.0.1"))
    assert principal == ManagementPrincipal(subject="local-user", role="admin")

    with pytest.raises(Exception) as denied:
        await resolver(_request("10.0.0.7"))
    assert getattr(denied.value, "status_code", None) == 503


def test_roles_are_fail_closed() -> None:
    require_role(ManagementPrincipal(subject="v", role="viewer"), "viewer")
    with pytest.raises(Exception) as denied:
        require_role(ManagementPrincipal(subject="v", role="viewer"), "operator")
    assert getattr(denied.value, "status_code", None) == 403

