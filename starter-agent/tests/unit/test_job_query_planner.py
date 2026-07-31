from starter_agent.job_research.query_planner import build_job_query_plan
from starter_agent.tools.adapters.serpapi_location import LocationResolution


def test_query_plan_balances_location_aliases_and_job_detail_signals() -> None:
    resolution = LocationResolution(
        requested="深圳",
        canonical_name="Shenzhen,Guangdong Province,China",
        status="resolved",
        city_alias="Shenzhen",
        country_code="cn",
    )

    plan = build_job_query_plan(
        query="AI Agent engineer",
        requested_location="深圳",
        resolution=resolution,
        user_language="zh-cn",
    )

    assert plan.location_aliases == ("深圳", "Shenzhen")
    assert plan.canonical_location == "Shenzhen,Guangdong Province,China"
    assert plan.hl == "zh-cn"
    assert plan.gl == "cn"
    assert len(plan.queries) <= 12
    assert any(query.startswith("深圳 ") for query in plan.queries)
    assert any(query.startswith("Shenzhen ") for query in plan.queries)
    assert any(
        "岗位职责" in query or "任职要求" in query
        for query in plan.queries
    )
    assert any(
        "job description" in query.casefold()
        or "responsibilities" in query.casefold()
        for query in plan.queries
    )
    assert all("tencent" not in query.casefold() for query in plan.queries)
    assert all("bytedance" not in query.casefold() for query in plan.queries)


def test_query_plan_is_not_specific_to_china_or_one_city() -> None:
    resolution = LocationResolution(
        requested="München",
        canonical_name="Munich,Bavaria,Germany",
        status="resolved",
        city_alias="Munich",
        country_code="de",
    )

    plan = build_job_query_plan(
        query="AI Agent",
        requested_location="München",
        resolution=resolution,
        user_language="de",
    )

    assert plan.location_aliases == ("München", "Munich")
    assert plan.gl == "de"
    assert plan.hl == "de"
    assert any(item.startswith("München ") for item in plan.queries)
    assert any(item.startswith("Munich ") for item in plan.queries)
    assert any("responsibilities" in item.casefold() for item in plan.queries)


def test_query_plan_degrades_to_original_location_without_guessing() -> None:
    resolution = LocationResolution(
        requested="任意地区",
        canonical_name=None,
        status="unavailable",
    )

    plan = build_job_query_plan(
        query="AI Agent",
        requested_location="任意地区",
        resolution=resolution,
        user_language="zh-cn",
    )

    assert plan.location_aliases == ("任意地区",)
    assert plan.canonical_location == ""
    assert plan.gl is None
    assert "location_alias_degraded" in plan.reason_codes
    assert all(item.startswith("任意地区 ") for item in plan.queries)
    assert len(plan.queries) <= 12
    assert any("岗位职责" in item for item in plan.queries)
    assert any("job description" in item.casefold() for item in plan.queries)


def test_query_plan_does_not_leak_long_resume_like_query() -> None:
    resolution = LocationResolution(
        requested="北京",
        canonical_name="Beijing,China",
        status="resolved",
        city_alias="Beijing",
        country_code="cn",
    )
    private_marker = "candidate@example.test"

    plan = build_job_query_plan(
        query=(
            "I worked at several companies and my email is "
            f"{private_marker}; phone 13800138000; "
            "please search every detail from this resume paragraph"
        ),
        requested_location="北京",
        resolution=resolution,
        user_language="zh-cn",
    )

    assert all(private_marker not in query for query in plan.queries)
    assert all("13800138000" not in query for query in plan.queries)
