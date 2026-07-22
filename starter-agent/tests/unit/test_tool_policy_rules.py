from __future__ import annotations

from importlib import import_module

from starter_agent.capabilities.models import PolicyRule


def _policy_module():
    try:
        return import_module("starter_agent.capabilities.policy")
    except ModuleNotFoundError:
        assert False, "Task 7 tool policy module is missing"


def test_policy_priority_and_tool_granular_allowlist() -> None:
    policy_module = _policy_module()
    policy = policy_module.ToolPolicy()
    request = policy_module.PolicyRequest(
        server_id="playwright",
        tool_name="browser_navigate",
        action="navigate",
        scheme="https",
        domain="jobs.example.com",
        arguments={"url": "https://jobs.example.com/1"},
        role="user",
        data_classes=("job_keywords",),
        reviewed=True,
    )
    allow = PolicyRule(
        id="allow-navigation",
        server_id="playwright",
        tool_name="browser_navigate",
        effect="allowlist_auto",
        schemes=("https",),
        domains=("*.example.com",),
        actions=("navigate",),
        created_by="admin",
    )
    other_tool = allow.model_copy(
        update={"id": "wrong-tool", "tool_name": "browser_snapshot"}
    )

    assert policy.evaluate(request, (other_tool,)).outcome == "require_confirmation"
    assert policy.evaluate(request, (allow,)).outcome == "allow"

    always = allow.model_copy(update={"id": "confirm", "effect": "always_confirm"})
    decision = policy.evaluate(request, (allow, always))
    assert decision.outcome == "require_confirmation"
    assert decision.reason_code == "always_confirm"

    forbidden = request.model_copy(update={"action": "upload_resume"})
    decision = policy.evaluate(forbidden, (allow,))
    assert decision.outcome == "deny"
    assert decision.reason_code == "forbidden_action"


def test_policy_matches_role_data_and_parameter_constraints() -> None:
    policy_module = _policy_module()
    request = policy_module.PolicyRequest(
        server_id="serpapi",
        tool_name="search_jobs",
        action="read",
        arguments={"keywords": "AI PM", "location": "Shanghai"},
        role="researcher",
        data_classes=("job_keywords", "location"),
        reviewed=True,
    )
    matching = PolicyRule(
        id="search-allow",
        server_id="serpapi",
        tool_name="search_jobs",
        effect="allowlist_auto",
        actions=("read",),
        parameter_constraints={"location": {"enum": ["Shanghai"]}},
        data_classes=("job_keywords", "location"),
        roles=("researcher",),
        created_by="admin",
    )

    assert policy_module.ToolPolicy().evaluate(request, (matching,)).outcome == "allow"
    assert (
        policy_module.ToolPolicy()
        .evaluate(request.model_copy(update={"role": "guest"}), (matching,))
        .outcome
        == "require_confirmation"
    )


def test_policy_rule_schema_hash_must_match_authoritative_request_hash() -> None:
    policy_module = _policy_module()
    request = policy_module.PolicyRequest(
        server_id="playwright",
        tool_name="browser_navigate",
        action="navigate",
        schema_hash="a" * 64,
        arguments={"url": "https://jobs.example.com/1"},
        reviewed=True,
    )
    rule = PolicyRule(
        id="schema-bound",
        server_id="playwright",
        tool_name="browser_navigate",
        effect="allowlist_auto",
        actions=("navigate",),
        schema_hash="b" * 64,
        created_by="admin",
    )

    assert policy_module.ToolPolicy().evaluate(request, (rule,)).outcome == (
        "require_confirmation"
    )


def test_data_classification_is_inferred_when_caller_claims_no_sensitive_data() -> None:
    policy_module = _policy_module()
    inferred = policy_module.infer_data_classes(
        {
            "payload": {
                "resume_text": "Senior product manager with ten years experience",
                "authorization": "Bearer secret-value",
            }
        },
        schema={"type": "object"},
        metadata={"data_classes": ["job_keywords"]},
        claimed=(),
    )

    assert {"resume", "token", "job_keywords"}.issubset(inferred)


def test_serpapi_rejects_secrets_long_text_and_total_budget() -> None:
    policy_module = _policy_module()
    for payload in (
        {"query": "AI PM", "location": "x" * 501},
        {"keywords": "token=secret", "location": "Shanghai"},
        {"keywords": "x" * 400, "location": "y" * 400},
    ):
        try:
            policy_module.validate_serpapi_payload(
                payload,
                (),
                max_bytes=600,
            )
        except policy_module.ScopeDenied:
            continue
        assert False, f"unsafe SerpAPI payload accepted: {payload.keys()}"
