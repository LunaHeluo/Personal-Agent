from starter_agent.job_research.candidates import rank_job_candidates


def test_direct_apply_link_ranks_before_share_and_organic() -> None:
    ranked = rank_job_candidates(
        [
            {
                "title": "Engineer",
                "url": "https://search.example.test/result",
                "url_kind": "organic",
                "provider_position": 0,
            },
            {
                "title": "Engineer",
                "url": "https://jobs.example.test/share/42",
                "url_kind": "structured_share",
                "provider_position": 0,
            },
            {
                "title": "Engineer",
                "url": "https://employer.example.test/jobs/42",
                "url_kind": "structured_apply",
                "provider_position": 0,
            },
        ],
        limit=5,
    )

    assert [item.url_kind for item in ranked] == [
        "structured_apply",
        "structured_share",
        "organic",
    ]
    assert [item.confidence for item in ranked] == [1.0, 0.7, 0.4]


def test_candidate_ranking_does_not_require_known_domain() -> None:
    ranked = rank_job_candidates(
        [
            {
                "title": "New Board Role",
                "url": "https://jobs.example-new.test/roles/42",
                "url_kind": "organic",
                "provider_position": 3,
            }
        ],
        limit=5,
    )

    assert ranked[0].url.endswith("/roles/42")


def test_candidate_ranking_deduplicates_and_rejects_non_http_urls() -> None:
    ranked = rank_job_candidates(
        [
            {
                "title": "Role",
                "url": "https://jobs.example.test/42#details",
                "url_kind": "organic",
                "provider_position": 2,
            },
            {
                "title": "Role",
                "url": "https://jobs.example.test/42",
                "url_kind": "structured_apply",
                "provider_position": 1,
            },
            {
                "title": "Unsafe",
                "url": "javascript:alert(1)",
                "url_kind": "structured_apply",
                "provider_position": 0,
            },
        ],
        limit=5,
    )

    assert len(ranked) == 1
    assert ranked[0].url_kind == "structured_apply"


def test_candidate_ranking_interleaves_distinct_jobs_before_mirror_links() -> None:
    ranked = rank_job_candidates(
        [
            {
                "title": "AI Agent Engineer",
                "company": "Example A",
                "location": "Chengdu",
                "url": "https://board-one.example/jobs/a",
                "url_kind": "structured_apply",
                "provider_position": 0,
            },
            {
                "title": "AI Agent Engineer",
                "company": "Example A",
                "location": "Chengdu",
                "url": "https://board-two.example/jobs/a-copy",
                "url_kind": "structured_apply",
                "provider_position": 0,
            },
            {
                "title": "Python Platform Engineer",
                "company": "Example B",
                "location": "Chengdu",
                "url": "https://example-b.test/careers/42",
                "url_kind": "structured_apply",
                "provider_position": 1,
            },
            {
                "title": "LLM Engineer",
                "company": "Example C",
                "location": "Chengdu",
                "url": "https://example-c.test/roles/7",
                "url_kind": "structured_apply",
                "provider_position": 2,
            },
        ],
        limit=3,
    )

    assert [item.company for item in ranked] == [
        "Example A",
        "Example B",
        "Example C",
    ]


def test_probable_job_detail_ranks_before_search_collection_at_same_priority() -> None:
    ranked = rank_job_candidates(
        [
            {
                "title": "15000+ Langchain jobs in Worldwide",
                "url": "https://example.test/jobs/langchain-jobs-worldwide",
                "url_kind": "organic",
                "provider_position": 0,
            },
            {
                "title": "Senior Python Infrastructure Engineer",
                "url": "https://example.test/jobs/python-infrastructure-engineer-42",
                "url_kind": "organic",
                "provider_position": 1,
            },
        ],
        limit=2,
    )

    assert ranked[0].title == "Senior Python Infrastructure Engineer"


def test_candidate_ranking_rejects_observed_collection_search_and_spam_urls() -> None:
    ranked = rank_job_candidates(
        [
            {
                "title": "成都AI岗位招聘",
                "url": "https://m.cd.example/job/aizhaopintopic/",
                "url_kind": "organic",
                "provider_position": 0,
            },
            {
                "title": "成都ai工程师招聘信息",
                "url": "https://jobs.example/zhaopin/9b84146f8feadf8a03xy09m8EA~~/",
                "url_kind": "organic",
                "provider_position": 1,
            },
            {
                "title": "AI成都{外围联系电话}电话=微信13404032909提供工作室",
                "url": "https://social.example/jobs/ai-chengdu-jobs?position=1&pageNum=0",
                "url_kind": "organic",
                "provider_position": 2,
            },
            {
                "title": "AI Agent Platform Engineer",
                "company": "New Employer",
                "location": "Chengdu",
                "url": "https://new-board.example/roles/agent-platform-42",
                "url_kind": "organic",
                "provider_position": 3,
            },
        ],
        limit=5,
    )

    assert [item.url for item in ranked] == [
        "https://new-board.example/roles/agent-platform-42"
    ]


def test_candidate_ranking_rejects_live_city_and_category_collection_pages() -> None:
    ranked = rank_job_candidates(
        [
            {
                "title": "上海招聘人工智能人才",
                "url": "https://www.liepin.com/city-sh/career/rengongzhineng/",
                "url_kind": "organic",
                "provider_position": 0,
            },
            {
                "title": "人工智能招聘信息 - 上海前程无忧",
                "url": "https://msearch.51job.com/jobs/shanghai/we06/rengongzhineng/",
                "url_kind": "organic",
                "provider_position": 1,
            },
            {
                "title": "上海 AI Agent 工程师",
                "url": "https://m.liepin.com/job/1982974339.shtml",
                "url_kind": "organic",
                "provider_position": 2,
            },
        ],
        limit=5,
        location_aliases=("上海", "Shanghai"),
    )

    assert [item.url for item in ranked] == [
        "https://m.liepin.com/job/1982974339.shtml"
    ]


def test_candidate_ranking_keeps_unknown_direct_detail_without_domain_allowlist() -> None:
    ranked = rank_job_candidates(
        [
            {
                "title": "机器学习工程师招聘",
                "company": "Example",
                "location": "杭州",
                "url": "https://unknown-employer.example/careers/openings/ml-engineer-7",
                "url_kind": "structured_apply",
                "provider_position": 0,
            }
        ],
        limit=5,
    )

    assert len(ranked) == 1
    assert ranked[0].company == "Example"


def test_candidate_ranking_classifies_detail_and_rejects_observed_collection_shapes() -> None:
    ranked = rank_job_candidates(
        [
            {
                "title": "AI Agent Engineer",
                "company": "Example",
                "location": "Shanghai",
                "url": "https://employer.test/careers/openings/agent-engineer-42",
                "url_kind": "structured_apply",
                "provider_position": 2,
            },
            {
                "title": "AI agent jobs in Shanghai",
                "url": "https://board.test/q-ai-agent-l-shanghai-jobs.html",
                "url_kind": "organic",
                "provider_position": 0,
            },
            {
                "title": "We are hiring",
                "url": "https://community.test/posts/2264530973952763/",
                "result_type": "social_post",
                "url_kind": "organic",
                "provider_position": 1,
            },
        ],
        limit=5,
    )

    assert [item.title for item in ranked] == ["AI Agent Engineer"]
    assert ranked[0].page_kind == "job_detail_candidate"
    assert ranked[0].score > 0.8
    assert "structured_job_link" in ranked[0].reason_codes


def test_candidate_ranking_rejects_query_driven_mobile_jobs_list() -> None:
    ranked = rank_job_candidates(
        [
            {
                "title": "Technical opening",
                "url": "https://jobs.example.test/m/jobs?q\\u003dAi+China",
                "url_kind": "organic",
                "provider_position": 0,
            },
            {
                "title": "AI Agent Engineer",
                "url": "https://careers.example.test/job/agent-42",
                "url_kind": "organic",
                "provider_position": 1,
            },
        ],
        limit=5,
    )

    assert [item.url for item in ranked] == [
        "https://careers.example.test/job/agent-42"
    ]


def test_candidate_ranking_keeps_unknown_candidate_after_probable_detail() -> None:
    ranked = rank_job_candidates(
        [
            {
                "title": "Agent Engineer",
                "company": "Example",
                "url": "https://employer.test/jobs/agent-engineer-42",
                "url_kind": "structured_apply",
                "provider_position": 1,
            },
            {
                "title": "Technical opening",
                "url": "https://unknown.test/openings/42",
                "url_kind": "organic",
                "provider_position": 0,
            },
        ],
        limit=5,
    )

    assert [item.page_kind for item in ranked] == [
        "job_detail_candidate",
        "unknown_candidate",
    ]


def test_candidate_ranking_merges_query_and_engine_provenance_by_url() -> None:
    ranked = rank_job_candidates(
        [
            {
                "title": "智能体研发工程师",
                "company": "示例科技",
                "location": "深圳",
                "url": "https://careers.example.cn/jobs/42#details",
                "url_kind": "organic",
                "provider_position": 2,
                "matched_queries": ["深圳 AI Agent 工程师 招聘"],
                "search_engines": ["google"],
            },
            {
                "title": "智能体研发工程师",
                "company": "示例科技",
                "location": "深圳",
                "url": "https://careers.example.cn/jobs/42",
                "url_kind": "structured_apply",
                "provider_position": 0,
                "matched_queries": ["Shenzhen AI Agent Engineer jobs"],
                "search_engines": ["google_jobs"],
            },
        ],
        limit=5,
        location_aliases=("深圳", "Shenzhen"),
    )

    assert len(ranked) == 1
    assert ranked[0].url_kind == "structured_apply"
    assert ranked[0].matched_queries == (
        "深圳 AI Agent 工程师 招聘",
        "Shenzhen AI Agent Engineer jobs",
    )
    assert ranked[0].search_engines == ("google", "google_jobs")


def test_local_chinese_detail_ranks_before_non_target_english_aggregator() -> None:
    ranked = rank_job_candidates(
        [
            {
                "title": "AI Engineer - MLabs",
                "location": "United States",
                "url": "https://builtin.com/job/ai-engineer/9866209",
                "url_kind": "structured_apply",
                "provider_position": 0,
                "snippet": "Build machine learning products.",
            },
            {
                "title": "智能体研发工程师（深圳）",
                "company": "示例科技",
                "location": "深圳",
                "url": "https://careers.example.cn/jobs/agent-42",
                "url_kind": "organic",
                "provider_position": 8,
                "snippet": "岗位职责：研发企业智能体。任职要求：熟悉 Python 和大模型。",
            },
        ],
        limit=5,
        location_aliases=("深圳", "Shenzhen"),
    )

    assert ranked[0].title == "智能体研发工程师（深圳）"
    assert "target_location_match" in ranked[0].reason_codes
    assert "chinese_title" in ranked[0].reason_codes
    assert "job_section_signals" in ranked[0].reason_codes
    assert "aggregator_signal" in ranked[1].reason_codes
    assert "non_target_location" in ranked[1].reason_codes


def test_employer_detail_ranks_before_aggregator_mirror() -> None:
    ranked = rank_job_candidates(
        [
            {
                "title": "AI Agent Engineer",
                "company": "Example",
                "location": "Munich",
                "url": "https://builtin.com/job/agent-engineer/7",
                "url_kind": "organic",
                "provider_position": 0,
            },
            {
                "title": "AI Agent Engineer",
                "company": "Example",
                "location": "Munich",
                "url": "https://jobs.example.com/careers/agent-engineer-7",
                "url_kind": "organic",
                "provider_position": 4,
            },
        ],
        limit=2,
        location_aliases=("München", "Munich"),
    )

    assert ranked[0].url == "https://jobs.example.com/careers/agent-engineer-7"
    assert "employer_detail_signal" in ranked[0].reason_codes
