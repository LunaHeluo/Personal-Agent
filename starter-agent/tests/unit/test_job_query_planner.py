from starter_agent.job_research.query_planner import build_job_query_plan
from starter_agent.tools.adapters.serpapi_location import LocationResolution


def test_query_plan_crosses_original_and_canonical_location_aliases() -> None:
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

    assert plan.queries == (
        "深圳 AI Agent 工程师 招聘",
        "Shenzhen AI Agent 工程师 招聘",
        "深圳 智能体工程师 招聘",
        "Shenzhen 智能体 Engineer jobs",
        "深圳 大模型应用工程师 招聘",
        "Shenzhen 大模型应用工程师 招聘",
        "深圳 生成式 AI 工程师 招聘",
        "Shenzhen Generative AI Engineer jobs",
        "深圳 AI Agent Engineer jobs",
        "Shenzhen AI Agent Engineer jobs",
        "深圳 LLM Application Engineer jobs",
        "Shenzhen LLM Application Engineer jobs",
    )
    assert plan.location_aliases == ("深圳", "Shenzhen")
    assert plan.canonical_location == "Shenzhen,Guangdong Province,China"
    assert plan.hl == "zh-cn"
    assert plan.gl == "cn"
    assert len(plan.queries) == 12


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
