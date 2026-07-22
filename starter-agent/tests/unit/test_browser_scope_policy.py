from __future__ import annotations

from importlib import import_module
import ipaddress

import pytest


def _policy_module():
    try:
        return import_module("starter_agent.capabilities.policy")
    except ModuleNotFoundError:
        assert False, "Task 7 browser scope policy module is missing"


async def _public_resolver(_host: str):
    return [ipaddress.ip_address("93.184.216.34")]


async def _mixed_resolver(_host: str):
    return [
        ipaddress.ip_address("93.184.216.34"),
        ipaddress.ip_address("127.0.0.1"),
    ]


async def test_browser_scope_reuses_fail_closed_url_and_redirect_validation() -> None:
    policy_module = _policy_module()
    public = policy_module.BrowserScopePolicy(resolver=_public_resolver)
    validated = await public.validate_url("https://jobs.example.com/opening")
    assert validated.hostname == "jobs.example.com"

    mixed = policy_module.BrowserScopePolicy(resolver=_mixed_resolver)
    unsafe_urls = (
        "file:///etc/passwd",
        "https://user:password@jobs.example.com/opening",
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost/admin",
    )
    for url in unsafe_urls:
        with pytest.raises(policy_module.ScopeDenied, match="unsafe_url"):
            await public.validate_url(url)
    with pytest.raises(policy_module.ScopeDenied, match="unsafe_url"):
        await mixed.validate_url("https://jobs.example.com/opening")
    with pytest.raises(policy_module.ScopeDenied, match="unsafe_redirect"):
        await public.validate_redirects(
            "https://jobs.example.com/opening",
            ("http://127.0.0.1/admin",),
        )


def test_browser_sensitive_outbound_and_serpapi_fields_are_denied() -> None:
    policy_module = _policy_module()
    browser = policy_module.BrowserScopePolicy(resolver=_public_resolver)

    with pytest.raises(policy_module.ScopeDenied, match="sensitive_outbound"):
        browser.validate_outbound(("resume",), 100)
    with pytest.raises(policy_module.ScopeDenied, match="outbound_budget"):
        browser.validate_outbound(("job_keywords",), 10_001, max_bytes=10_000)

    policy_module.validate_serpapi_payload(
        {"keywords": "AI product manager", "location": "Shanghai"},
        ("job_keywords", "location"),
    )
    with pytest.raises(policy_module.ScopeDenied, match="serpapi_fields"):
        policy_module.validate_serpapi_payload(
            {"keywords": "AI PM", "resume": "private resume"},
            ("job_keywords", "resume"),
        )
