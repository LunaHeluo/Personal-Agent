import json
from uuid import uuid4

from starter_agent.agent.token_counter import TokenCounter
from starter_agent.agent.tool_result_guard import ToolResultGuard
from starter_agent.infrastructure.session_store import SQLiteSessionStore
from starter_agent.interfaces.api import ChatRequest


def test_guard_redacts_before_truncation_and_reports_complete_measurements() -> None:
    guard = ToolResultGuard(TokenCounter(safety_ratio=1), max_result_tokens=260)
    raw = json.dumps(
        {
            "ok": True,
            "data": {
                "authorization": "Bearer TOP-SECRET-TOKEN",
                "description": "Build trustworthy agents. " * 1_000,
            },
            "metadata": {
                "is_untrusted_external_content": True,
                "source_url": "https://jobs.example/42",
                "content_sha256": "c" * 64,
                "call_id": "call-42",
                "snapshot_id": "snapshot-7",
                "schema_hash": "d" * 64,
            },
        }
    )

    result = guard.guard(raw, "jobs", "call-42", "artifact:call-42")
    payload = json.loads(result.content)

    assert result.is_truncated is True
    assert result.truncation_reason == "token_budget"
    assert result.raw_result_bytes == len(raw.encode())
    assert result.raw_result_chars == len(raw)
    assert result.kept_result_bytes == len(result.content.encode())
    assert result.kept_result_chars == len(result.content)
    assert result.kept_result_tokens == result.context_result_tokens
    assert len(result.content_sha256) == 64
    assert "TOP-SECRET-TOKEN" not in result.redacted_content
    assert "TOP-SECRET-TOKEN" not in result.content
    assert payload["metadata"]["source_url"] == "https://jobs.example/42"
    assert payload["metadata"]["call_id"] == "call-42"
    assert payload["metadata"]["raw_source_ref"] == "artifact:call-42"


def test_guard_redacts_small_results_even_when_no_truncation_is_needed() -> None:
    guard = ToolResultGuard(TokenCounter(safety_ratio=1), max_result_tokens=2_000)
    raw = json.dumps({"ok": True, "data": {"cookie": "session=private"}})

    result = guard.guard(raw, "jobs", "call-small", "artifact:call-small")

    assert result.is_truncated is False
    assert result.truncation_reason is None
    assert "private" not in result.content
    assert "[redacted]" in result.content
    assert result.raw_source_ref is None


def test_tool_artifact_store_is_restricted_and_never_persists_unredacted_content(
    tmp_path,
) -> None:
    store = SQLiteSessionStore("sqlite:///artifacts.db", tmp_path)
    session_id = store.create_session()
    turn_id = uuid4()

    store.save_tool_artifact(
        source_ref="artifact:call-42",
        session_id=session_id,
        turn_id=turn_id,
        tool_name="jobs",
        content='{"authorization":"Bearer TOP-SECRET"}',
        server_id="jobs",
        snapshot_id="snapshot-7",
        schema_hash="a" * 64,
        requested_url="https://jobs.example/r/42",
        final_url="https://jobs.example/42",
        content_sha256="b" * 64,
        truncation_summary={"reason": "token_budget", "raw_bytes": 50},
    )

    artifact = store.get_tool_artifact("artifact:call-42")
    assert artifact is not None
    assert artifact["restricted"] is True
    assert "TOP-SECRET" not in artifact["content"]
    assert artifact["snapshot_id"] == "snapshot-7"
    assert artifact["truncation_summary"]["reason"] == "token_budget"


def test_api_client_cannot_disable_real_tool_result_governance() -> None:
    request = ChatRequest(message="run tool", tool_governance_enabled=False)

    assert request.tool_governance_enabled is True


def test_nested_sensitive_containers_are_redacted_in_context_and_artifact(
    tmp_path,
) -> None:
    secrets = ("COOKIE-VALUE", "AUTH-VALUE", "person@example.com", "Person Name", "FORM-VALUE")
    raw = json.dumps(
        {
            "cookies": [{"name": "sid", "value": secrets[0]}],
            "authorization": {"value": secrets[1]},
            "form": {
                "email": secrets[2],
                "name": secrets[3],
                "value": secrets[4],
            },
        }
    )
    guarded = ToolResultGuard(
        TokenCounter(safety_ratio=1), max_result_tokens=2_000
    ).guard(raw, "jobs", "call-deep", "artifact:call-deep")
    assert all(secret not in guarded.content for secret in secrets)

    store = SQLiteSessionStore("sqlite:///deep-artifact.db", tmp_path)
    session_id = store.create_session()
    store.save_tool_artifact(
        source_ref="artifact:call-deep",
        session_id=session_id,
        turn_id=uuid4(),
        call_id="call-deep",
        tool_name="jobs",
        content=raw,
    )
    artifact = store.get_tool_artifact("artifact:call-deep")
    assert artifact is not None
    assert all(secret not in artifact["content"] for secret in secrets)
