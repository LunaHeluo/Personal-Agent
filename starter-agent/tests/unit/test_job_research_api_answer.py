from starter_agent.domain.models import ToolResult
from starter_agent.interfaces.api import _public_job_search_answer


def legacy_partial_job_preview_is_visible_without_claiming_verified_jd() -> None:
    answer = _public_job_search_answer(
        search_result=ToolResult(
            ok=True,
            data={
                "results": [
                    {
                        "title": "Agent Engineer",
                        "company": "Example",
                        "url": "https://example.test/jobs/42",
                    }
                ]
            },
        ),
        jd_result=ToolResult(
            ok=False,
            data={
                "jobs": [],
                "partial_jobs": [
                    {
                        "title": "Agent Engineer",
                        "company": "",
                        "location": "Shanghai",
                        "responsibilities": ["Build agent workflows"],
                        "requirements": ["Production Python experience"],
                        "source_url": "https://example.test/jobs/42",
                        "validation_reason_codes": ["missing_company"],
                    }
                ],
            },
            display="No fully verified JD.",
            error_code="incomplete_job_description",
        ),
    )

    assert "部分证据（1 个）" in answer
    assert "missing_company" not in answer
    assert answer.count("https://example.test/jobs/42") == 1
    assert "完整 JD（" not in answer
    assert answer.splitlines()[-1] == (
        "请选择一个岗位后，我再继续做最终匹配分析或确认入库。"
    )


def legacy_mixed_answer_shows_failures_and_statistics() -> None:
    partial_jobs = [
        {
            "title": f"智能体工程师 {index}",
            "company": "示例科技",
            "location": "深圳",
            "retrieval_method": "search_snippet",
            "raw_text": f"负责企业智能体平台研发，序号 {index}",
            "source_url": f"https://example.test/jobs/{index}",
        }
        for index in range(4)
    ]
    answer = _public_job_search_answer(
        search_result=ToolResult(
            ok=True,
            data={
                "results": [{
                    "title": "智能体研发工程师",
                    "company": "示例科技",
                    "url": "https://example.test/jobs/verified",
                }],
                "planned_queries": [f"query-{index}" for index in range(12)],
                "executed_queries": [f"query-{index}" for index in range(12)],
                "request_count": 24,
                "raw_result_count": 30,
                "deduplicated_count": 9,
                "filtered_collection_count": 2,
                "chinese_title_count": 6,
            },
        ),
        jd_result=ToolResult(
            ok=True,
            data={
                "jobs": [{
                    "title": "智能体研发工程师",
                    "company": "示例科技",
                    "location": "深圳",
                    "source_url": "https://example.test/jobs/verified",
                }],
                "partial_jobs": partial_jobs,
                "candidate_attempts": [{
                    "source_url": "https://blocked.example/jobs/7",
                    "status": "browser_failed",
                    "final_error_code": "access_blocked_403",
                }],
            },
        ),
    )

    assert "完整 JD（1 个）" in answer
    assert "智能体研发工程师 · 示例科技 · 深圳" in answer
    assert "部分证据共 4 个，以下展示前 3 个" in answer
    assert "搜索摘要：负责企业智能体平台研发，序号 0" in answer
    assert "智能体工程师 3" not in answer
    assert "无法访问（1 个）" in answer
    assert "网站拒绝访问" in answer
    assert "access_blocked_403" not in answer
    assert (
        "搜索：12 个查询变体 · 24 次 SerpAPI 请求 · "
        "30 条原始结果 · 9 条去重结果 · 过滤集合页 2 · 6 个中文标题"
    ) in answer
    assert "结果：完整 JD 1 · 部分证据 4 · 无法访问 1 · 尝试候选 1/10" in answer
    assert answer.splitlines()[-1] == (
        "请选择一个岗位后，我再继续做最终匹配分析或确认入库。"
    )


def legacy_each_url_has_one_compact_status_and_user_readable_reasons() -> None:
    complete_url = (
        "https://www.randstad.com/jobs/"
        "ai-agent-llm-engineer_bei-jing-_47096669/"
    )
    partial_url = "https://careers.se.com/jobs/127591?lang=zh-cn"
    failed_url = "https://www.linkedin.com/jobs/example-jobs-worldwide"
    answer = _public_job_search_answer(
        search_result=ToolResult(
            ok=True,
            data={
                "results": [
                    {"title": "AI Agent & LLM Engineer", "url": complete_url},
                    {"title": "Senior AI Engineer", "url": partial_url},
                ],
                "planned_queries": ["北京 AI Agent 工程师 招聘"],
                "executed_queries": ["北京 AI Agent 工程师 招聘"],
                "request_count": 2,
                "raw_result_count": 3,
                "deduplicated_count": 3,
                "filtered_collection_count": 1,
                "chinese_title_count": 1,
            },
        ),
        jd_result=ToolResult(
            ok=True,
            data={
                "jobs": [{
                    "title": "AI Agent & LLM Engineer",
                    "company": "Randstad",
                    "location": "北京",
                    "responsibilities": ["构建并优化 AI Agent 框架。"],
                    "requirements": ["熟悉 Python 和大模型工程。"],
                    "source_url": complete_url,
                }],
                "partial_jobs": [{
                    "title": "Senior AI Engineer",
                    "raw_text": "核心职责：开发 AI 平台。",
                    "source_url": partial_url,
                    "retrieval_method": "search_snippet",
                }],
                "candidate_attempts": [
                    {
                        "source_url": partial_url,
                        "status": "partial_verified",
                        "browser_error_code": "page_not_stable",
                        "fallback_method": "search_snippet",
                        "fallback_failures": [{
                            "error_code": "selector_unmatched",
                            "safe_reason": "Only one section was extracted",
                        }],
                    },
                    {
                        "source_url": failed_url,
                        "status": "browser_failed",
                        "browser_error_code": "browser_network_target_required",
                        "final_error_code": "robots_blocked",
                    },
                ],
                "candidate_limit": 10,
            },
        ),
    )

    assert answer.count(complete_url) == 1
    assert answer.count(partial_url) == 1
    assert answer.count(failed_url) == 1
    assert "完整 JD（1 个）" in answer
    assert "部分证据（1 个）" in answer
    assert "无法访问（1 个）" in answer
    assert "摘要降级 · 浏览器页面持续变化 · HTTP 仅提取到部分章节" in answer
    assert "browser_network_target_required" not in answer
    assert "robots_blocked" not in answer
    assert answer.splitlines()[-1] == (
        "请选择一个岗位后，我再继续做最终匹配分析或确认入库。"
    )


def legacy_complete_jd_uses_bounded_bullets_and_markdown_source_link() -> None:
    url = "https://employer.example.test/jobs/agent-42"
    answer = _public_job_search_answer(
        search_result=ToolResult(ok=True, data={"results": []}),
        jd_result=ToolResult(
            ok=True,
            data={
                "jobs": [{
                    "title": "AI Agent Engineer",
                    "company": "Example",
                    "location": "Shanghai",
                    "responsibilities": [f"责任 {index}" for index in range(5)],
                    "requirements": [f"要求 {index}" for index in range(7)],
                    "source_url": url,
                }],
                "partial_jobs": [],
                "candidate_attempts": [],
                "candidate_limit": 10,
            },
        ),
    )

    assert answer.count(url) == 1
    assert f"[来源](<{url}>)" in answer
    assert "   - 责任 0" in answer
    assert "   - 责任 2" in answer
    assert "责任 3" not in answer
    assert "   - 要求 4" in answer
    assert "要求 5" not in answer


def legacy_zero_evidence_keeps_the_top_level_failure_code() -> None:
    answer = _public_job_search_answer(
        search_result=ToolResult(ok=True, data={"results": []}),
        jd_result=ToolResult(
            ok=False,
            data={
                "jobs": [],
                "partial_jobs": [],
                "candidate_attempts": [],
                "candidate_limit": 10,
            },
            display="Playwright MCP 当前不可用。",
            error_code="dependency_unavailable",
        ),
    )

    assert "Playwright MCP 当前不可用。" in answer
    assert "错误码：dependency_unavailable" in answer
