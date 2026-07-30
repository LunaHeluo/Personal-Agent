from starter_agent.domain.models import ToolResult
from starter_agent.interfaces.api import _public_job_search_answer


def test_partial_job_preview_is_visible_without_claiming_verified_jd() -> None:
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

    assert "\u90e8\u5206\u9a8c\u8bc1" in answer
    assert "\u5f85\u786e\u8ba4" in answer
    assert "missing_company" in answer
    assert "https://example.test/jobs/42" in answer
    assert "\u5df2\u81ea\u52a8\u8bfb\u53d6\u516c\u5f00 JD \u9884\u89c8" not in answer


def test_mixed_answer_shows_total_display_limit_snippets_failures_and_statistics() -> None:
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

    assert "智能体研发工程师 · 示例科技 · 深圳" in answer
    assert "部分岗位证据共 4 个，以下展示前 3 个" in answer
    assert "搜索摘要：负责企业智能体平台研发，序号 0" in answer
    assert "智能体工程师 3" not in answer
    assert "https://blocked.example/jobs/7 · access_blocked_403" in answer
    assert "查询变体：12/12" in answer
    assert "SerpAPI 请求：24" in answer
    assert "原始结果：30；去重后：9；中文标题：6" in answer
    assert "完整 JD：1；部分证据：4；失败链接：1" in answer
