from pathlib import Path


HTML = Path("src/web/index.html").read_text(encoding="utf-8")


def test_capability_ui_calls_only_task11_management_endpoints() -> None:
    for endpoint in (
        "/v1/capabilities/servers",
        "/health-check",
        "/connect",
        "/disconnect",
        "/refresh",
        "/enable",
        "/disable",
        "/v1/capabilities/tools",
        "/schema",
        "/review",
        "/policies",
        "/v1/capabilities/skills",
        "/reload",
        "/raw",
        "/v1/capabilities/confirmations/",
        "/decisions",
    ):
        assert endpoint in HTML


def test_mutations_use_cas_loading_locks_and_authoritative_rereads() -> None:
    for contract in (
        "expected_revision",
        '"If-Match"',
        "capabilityState.pendingOperations",
        "isCapabilityOperationPending",
        "finally",
        "await loadCapabilityServer",
        "await loadCapabilityTool",
        "await loadCapabilitySkills",
        "await loadCapabilitySkill",
    ):
        assert contract in HTML


def test_dangerous_operations_use_management_confirmation_decisions() -> None:
    for contract in (
        "renderCapabilityConfirmation",
        "decideCapabilityConfirmation",
        "confirmation.revision",
        "idempotency_key",
        'decision: "once"',
        'decision: "cancel"',
    ):
        assert contract in HTML


def test_single_server_refresh_is_scoped_to_the_selected_server() -> None:
    assert "async function refreshCapabilityServer" in HTML
    assert "await loadCapabilityServer(serverId)" in HTML
    assert "loadCapabilityServers()" not in HTML[
        HTML.index("async function refreshCapabilityServer") :
        HTML.index("async function refreshCapabilityServer") + 700
    ]
