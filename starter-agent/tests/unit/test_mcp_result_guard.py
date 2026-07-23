import json
from urllib.parse import parse_qs, quote, unquote, urlsplit
from uuid import uuid4

from starter_agent.agent.token_counter import TokenCounter
from starter_agent.agent.tool_result_guard import (
    ToolResultGuard,
    redact_tool_result_content,
)
import starter_agent.agent.tool_result_guard as guard_module
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


def test_shared_redactor_handles_multiline_inline_spaced_and_url_secrets() -> None:
    content = (
        "Authorization: Bearer TOP-SECRET\n"
        "api_key = value with spaces\n"
        "inline secret=INLINE-SECRET then continue\n"
        "open https://alice:PASS@example.test/path?token=URL-TOKEN&ok=yes now"
    )

    redacted = redact_tool_result_content(content)

    for secret in ("TOP-SECRET", "value with spaces", "INLINE-SECRET", "PASS", "URL-TOKEN"):
        assert secret not in redacted
    assert "\\1" not in redacted


def test_provenance_url_sanitizer_rejects_ambiguous_or_invalid_whole_values() -> None:
    sanitizer = getattr(guard_module, "sanitize_provenance_url", None)
    assert sanitizer is not None

    assert sanitizer("https://example.test/?token=value with spaces") is None
    assert sanitizer("https://example.test/\nnext") is None
    assert sanitizer("https://") is None
    assert sanitizer("file:///private/path") is None


def test_provenance_url_sanitizer_strips_userinfo_and_sensitive_query() -> None:
    sanitizer = getattr(guard_module, "sanitize_provenance_url", None)
    assert sanitizer is not None

    sanitized = sanitizer(
        "https://alice:password@example.test/path?token=secret&ok=yes"
    )

    assert sanitized == "https://example.test/path?ok=yes"


def test_provenance_url_sanitizer_recurses_nested_urls_with_a_depth_limit() -> None:
    sanitizer = getattr(guard_module, "sanitize_provenance_url", None)
    assert sanitizer is not None
    nested = "https://user:password@leaf.test/path?token=secret&ok=leaf"
    for index in range(8):
        nested = (
            f"https://level-{index}.test/path?"
            f"next={quote(nested, safe='')}&ok=level-{index}"
        )

    sanitized = sanitizer(nested)

    assert sanitized is not None
    expanded = sanitized
    for _ in range(10):
        expanded = unquote(expanded)
    assert "password" not in expanded
    assert "secret" not in expanded
    assert "user@" not in expanded
    assert len(expanded) < len(unquote(nested)) * 2


def test_provenance_url_sanitizer_removes_untrusted_url_like_parameters() -> None:
    sanitizer = getattr(guard_module, "sanitize_provenance_url", None)
    assert sanitizer is not None
    safe_nested = quote(
        "https://user:password@inner.test/path?api_key=secret&ok=inner",
        safe="",
    )

    sanitized = sanitizer(
        "https://outer.test/path?"
        f"redirect={safe_nested}&redirect_hint=not-a-url&ok=outer"
    )

    assert sanitized is not None
    query = parse_qs(urlsplit(sanitized).query)
    assert query["ok"] == ["outer"]
    assert "redirect_hint" not in query
    assert query["redirect"] == ["https://inner.test/path?ok=inner"]


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
