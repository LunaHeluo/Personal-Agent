# Tool Acceptance

## `search_jobs_serpapi` 真实验收记录

- 验收时间：2026-07-12（UTC 检索时间由 ToolResult 逐条记录）
- 用户目标：搜索悉尼的 AI Agent 工程师岗位，返回 5 条结果
- 模型：`zhipu / glm-4.7`
- 状态：通过

### 执行证据

| 字段 | 实际结果 |
|---|---|
| session_id | `78a2bb70-b8a5-4d11-9a33-71c18520019d` |
| turn_id | `2359d802-2531-4689-8d56-1b84e27ebb94` |
| tool_call_count | `1` |
| tool | `search_jobs_serpapi` |
| tool arguments 摘要 | `query="AI Agent engineer jobs"`、`location="Sydney"`、`limit=5` |
| 首选搜索 | `google_jobs` |
| fallback | `google`（首选搜索无结果后执行） |
| active profile | `primary` |
| API key env | `SERPAPI_API_KEY` |
| secret 泄露检查 | 真实 Key 在日志中零命中；HTTP 客户端 INFO URL 日志已关闭 |
| 模型调用 | 第一次返回 1 个 tool call；工具完成后第二次返回最终答案 |

### 返回来源摘要

真实搜索返回 5 条公开网页线索：

1. SEEK：Sydney AI Engineer jobs
2. Indeed：Sydney Artificial Intelligence jobs
3. LinkedIn：Australia Conversational AI Engineer jobs
4. Glassdoor：Sydney Artificial Intelligence Engineer jobs
5. Built In Sydney：Sydney AI jobs

每条结果均包含来源 URL、`source=serpapi_google` 和 UTC `retrieved_at`。GLM-4.7
最终答案保留了来源链接和检索时间，并明确提醒用户打开来源复核岗位是否仍有效。

### 发现与修复

首次真实验收发现 `httpx` 的 INFO 日志会打印包含 `api_key` 查询参数的完整 URL。
已完成：

- 将 `httpx` 和 `httpcore` 日志级别提高到 WARNING；
- 将已有日志中的该凭据替换为 `[REDACTED]`；
- 扫描日志确认真实 Key 零残留；
- 增加 `test_http_client_info_logs_are_disabled` 防回归测试；
- 重新执行真实 GLM-4.7 端到端测试，日志未再出现请求 URL。

### 未执行项

backup profile 的真实外部请求未执行，因为本次只提供并使用了 primary Key。profile
切换、不存在 profile 不回退、缺 Key 安全失败均已由 pytest 覆盖。

## Resume Management 最小方案验收记录

- 验收时间：2026-07-12
- 输入：`examples/job-hunt-agent/job-hunt-agent/data/resume_base.md`
- 状态：通过

### 验收链路

1. `read_resume` 读取 Markdown 简历并生成 SHA-256；
2. `draft_resume_patch` 将原文中已有的
   `Built a retrieval-augmented generation framework` 保守调整为
   `Built and evaluated a retrieval-augmented generation framework`；
3. evidence 使用原简历中的同一真实文本片段，未增加不存在的用户规模、上线状态或
   ownership；
4. `save_resume_version` 在 `confirmed=true` 且父版本 SHA 一致后保存新版本；
5. 新版本保存到
   `data/resumes/versions/resume_base/v0001_rag-evaluation.md`；
6. 原始简历 SHA 在保存前后保持一致；
7. 保存文件 SHA 与 `versions.json` 中记录的 SHA 一致；
8. `compare_resume` 的 diff、路径穿越、缺少依据、未确认保存和父版本冲突均有 pytest
9. `compare_resume_to_jd` 使用简历与具体 `job_id`/完整 JD 生成可溯源证据矩阵；缺少具体
   岗位时返回 `missing_job_description`，没有原文证据的能力只标记为 gap
   覆盖。

版本索引采用 `data/resumes/versions.json`，不使用数据库。索引不保存简历正文，只记录
版本 ID、父版本、文件路径、SHA、时间和 turn/session ID。简历正文只保存在本地版本
文件中，原文件永不覆盖。
