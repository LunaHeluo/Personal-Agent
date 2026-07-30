from __future__ import annotations

from importlib import import_module
import pytest

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


def test_policy_applies_scope_rule_to_every_recursive_target() -> None:
    policy_module = _policy_module()
    request = policy_module.PolicyRequest(
        server_id="playwright",
        tool_name="browser_navigate",
        action="navigate",
        schema_hash="a" * 64,
        target_scopes=(
            ("https", "jobs.example.com"),
            ("https", "evil.example.net"),
        ),
        arguments={"url": "https://jobs.example.com"},
        reviewed=True,
    )
    rule = PolicyRule(
        id="scoped",
        server_id="playwright",
        tool_name="browser_navigate",
        effect="allowlist_auto",
        schemes=("https",),
        domains=("*.example.com",),
        actions=("navigate",),
        schema_hash="a" * 64,
        created_by="admin",
    )

    assert policy_module.ToolPolicy().evaluate(request, (rule,)).outcome == (
        "require_confirmation"
    )


def test_browser_and_serp_structural_payload_rules_reject_neutral_pii_text() -> None:
    policy_module = _policy_module()
    with pytest.raises(policy_module.ScopeDenied, match="browser_payload"):
        policy_module.validate_browser_payload(
            "navigate",
            {"url": "https://jobs.example.com", "payload": "harmless free text"},
        )
    with pytest.raises(policy_module.ScopeDenied, match="sensitive_url_query"):
        policy_module.reject_sensitive_url_query(
            "https://jobs.example.com?q=jane@example.com"
        )
    for query in (
        "jane@example.com product manager",
        "+86 13800138000 product manager",
        "I led product teams for ten years",
        "AI PM\nMy experience includes delivery",
    ):
        with pytest.raises(policy_module.ScopeDenied, match="serpapi_fields"):
            policy_module.validate_serpapi_payload({"query": query}, (), max_bytes=600)


@pytest.mark.parametrize(
    ("action", "arguments"),
    [
        ("input", {"selector": "#email", "value": "a"}),
        ("fill", {"selector": "#email", "value": "a"}),
        ("type", {"selector": "#email", "text": "a"}),
        ("upload", {"selector": "#resume", "path": "resume.pdf"}),
        ("login", {"url": "https://jobs.example.com/login"}),
        ("submit", {"selector": "button[type=submit]"}),
        ("message", {"text": "hello"}),
    ],
)
def test_phase_one_browser_mutations_are_directly_denied(action, arguments) -> None:
    policy_module = _policy_module()
    with pytest.raises(policy_module.ScopeDenied, match="forbidden_action"):
        policy_module.validate_browser_payload(action, arguments)


def test_click_and_script_accept_only_provably_read_only_structures() -> None:
    policy_module = _policy_module()
    policy_module.validate_browser_payload(
        "click", {"selector": "button[aria-label='Details']", "button": "left"}
    )
    for arguments in (
        {"selector": "button", "text": "Jane Doe"},
        {"ref": "job-card", "value": "private"},
        {"selector": "button", "payload": {"email": "jane@example.com"}},
    ):
        with pytest.raises(policy_module.ScopeDenied, match="browser_payload"):
            policy_module.validate_browser_payload("click", arguments)

    policy_module.validate_browser_payload(
        "script", {"script": "return document.title"}
    )
    for script in (
        "fetch('https://example.com/collect')",
        "document.body.innerHTML = 'changed'",
        "document.querySelector('input').value = 'Jane Doe'",
        "return 'jane@example.com resume'",
        "return window.someUnknownFunction()",
    ):
        with pytest.raises(policy_module.ScopeDenied):
            policy_module.validate_browser_payload("script", {"script": script})


@pytest.mark.parametrize(
    "query",
    [
        "Product Manager 2019 2024",
        "employment from 2020 to 2024",
        "I worked at Acme from January to March",
        "John Smith software engineer at Google",
        "Jane Doe employment at Acme",
        "2024-01-01 to 2025-06-30 product manager",
        "Shanghai",
    ],
)
def test_serpapi_rejects_history_dates_and_non_job_intent(query: str) -> None:
    policy_module = _policy_module()
    with pytest.raises(policy_module.ScopeDenied, match="serpapi_fields"):
        policy_module.validate_serpapi_payload({"query": query}, (), max_bytes=600)


@pytest.mark.parametrize(
    "query", ["AI product manager", "Python backend engineer", "Kubernetes DevOps"]
)
def test_serpapi_accepts_short_job_role_or_skill_search(query: str) -> None:
    _policy_module().validate_serpapi_payload({"query": query}, (), max_bytes=600)


def test_serpapi_rejects_compact_name_and_employer_prefix() -> None:
    policy_module = _policy_module()

    with pytest.raises(policy_module.ScopeDenied, match="serpapi_fields"):
        policy_module.validate_serpapi_payload(
            {"query": "Alice OpenAI Python engineer"}, (), max_bytes=600
        )


@pytest.mark.parametrize(
    "query",
    ["Machine Learning Engineer", "Natural Language Processing Engineer"],
)
def test_serpapi_accepts_title_case_multiword_roles(query: str) -> None:
    _policy_module().validate_serpapi_payload({"query": query}, (), max_bytes=600)


@pytest.mark.parametrize(
    "query",
    [
        "Senior Product Manager led global teams and delivered growth",
        "Product manager led teams",
        "Backend engineer built and managed payment systems",
        "Data scientist achieved revenue growth",
        "Software engineer responsible for platform delivery",
        "产品经理主导全球团队并交付增长",
        "后端工程师负责支付平台开发",
        "曾任数据科学家从事推荐系统",
    ],
)
def test_serpapi_rejects_resume_sentences_and_achievement_verbs(query: str) -> None:
    policy_module = _policy_module()
    with pytest.raises(policy_module.ScopeDenied, match="serpapi_fields"):
        policy_module.validate_serpapi_payload({"query": query}, (), max_bytes=600)


@pytest.mark.parametrize(
    "query",
    [
        "Senior Product Manager AI",
        "Python Backend Engineer",
        "高级产品经理 AI",
        "Python 后端工程师",
    ],
)
def test_serpapi_accepts_compact_job_keyword_bags(query: str) -> None:
    _policy_module().validate_serpapi_payload({"query": query}, (), max_bytes=600)


@pytest.mark.parametrize(
    "query",
    [
        "产品经理",
        "财务经理",
        "后端工程师",
        "移动端开发",
        "商业分析师",
        "用户体验设计师",
        "电商运营",
        "市场经理",
        "企业销售",
        "财务会计",
        "技术招聘",
        "人力资源",
        "项目经理",
        "管理顾问",
        "研究员",
        "解决方案架构师",
        "软件测试",
        "系统运维",
        "Finance Manager",
        "Senior Accountant",
        "Operations Consultant",
        "HR Recruiter",
    ],
)
def test_serpapi_accepts_common_job_role_queries(query: str) -> None:
    _policy_module().validate_serpapi_payload({"query": query}, (), max_bytes=600)


def test_serpapi_accepts_bounded_location_alias_search_plan() -> None:
    _policy_module().validate_serpapi_payload(
        {
            "query": "AI Agent",
            "query_variants": [
                "上海 AI Agent 工程师 招聘",
                "Shanghai AI Agent Engineer jobs",
            ],
            "location": "上海",
            "location_alias": "Shanghai",
            "hl": "zh-cn",
            "gl": "cn",
            "google_domain": "google.com",
            "limit": 5,
        },
        (),
        max_bytes=2000,
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"query_variants": ["AI Agent"] * 13},
        {"google_domain": "evil.example"},
        {"hl": "zh cn"},
        {"gl": "china"},
        {"location_alias": "上海"},
    ],
)
def test_serpapi_rejects_unsafe_or_unbounded_search_plan(extra) -> None:
    with pytest.raises(_policy_module().ScopeDenied, match="serpapi_fields"):
        _policy_module().validate_serpapi_payload(
            {"query": "AI Agent", **extra}, (), max_bytes=2000
        )


@pytest.mark.parametrize(
    "query",
    [
        "财务经理负责全球预算",
        "产品经理主导增长项目",
        "后端工程师交付支付系统",
        "Operations Manager led global teams",
        "Accountant responsible for reporting",
        "HR Recruiter achieved hiring targets",
    ],
)
def test_common_roles_do_not_bypass_resume_verb_rejection(query: str) -> None:
    policy_module = _policy_module()
    with pytest.raises(policy_module.ScopeDenied, match="serpapi_fields"):
        policy_module.validate_serpapi_payload({"query": query}, (), max_bytes=600)
