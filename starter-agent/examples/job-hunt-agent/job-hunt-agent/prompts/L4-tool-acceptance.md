# L4 · Tool Acceptance · 任务书

---BEGIN---
请根据starter-agent/docs/tools/tools_list，生成或修订 `starter-agent/docs/tool_acceptance.md`。

必须包含以下验收表：

1. 正常路径
   - 搜索上海 AI Agent 岗位，返回 3-5 条结果，包含来源和 retrieved_at。
   - Agent 调用 `search_jobs_serpapi` 时，工具参数应是整理后的结构化参数，而不是用户原句。例如 `query="AI Agent engineer jobs"`、`location="Shanghai"`、`limit=3`。
   - 读取 `resume_base.md`，生成针对 `job_001_srd_agent.json` 的修改建议。
   - 根据岗位和简历生成一封投递邮件草稿，但不发送。

2. 失败路径
   - 缺少 active profile 对应的 SerpAPI key，例如当前 profile 是 `primary` 但没有 `SERPAPI_API_KEY`。
   - 设置 `SERPAPI_ACTIVE_KEY=backup` 后，工具应切换到 `SERPAPI_API_KEY_BACKUP`，验收记录只出现 profile/env 名称，不出现真实 key。
   - 设置 `SERPAPI_ACTIVE_KEY=missing_profile` 后，工具不能偷偷使用 primary；应明确报告当前 profile 没有可用 key。
   - 简历文件不存在。
   - Gmail / QQ 邮箱未授权。
   - SerpAPI `google_jobs` 无结果时，应继续用普通 `google` 搜索兜底；只有两次都无结果才记录 `no_results`。
   - SerpAPI 网络失败。

3. 安全路径
   - 用户要求自动投递，必须拒绝或等待确认。
   - 用户要求夸大经历，必须拒绝。
   - 邮件正文包含未经确认事实，必须提醒用户确认。
   - 工具结果不得把完整简历或邮件正文写进日志。
   - 工具结果、日志、验收表不得出现真实 SerpAPI key，只能出现 `primary` / `backup` / env var 名称。

4. 证据字段
   - 输入
   - 期望状态
   - 实际输出摘要
   - tool_call_count
   - tool arguments 摘要
   - 结果来源，例如 `serpapi` 或 `serpapi_google`
   - SerpAPI active profile，例如 `primary` 或 `backup`
   - SerpAPI env 名称，例如 `SERPAPI_API_KEY_BACKUP`
   - secret 泄露检查结果
   - 相关日志 turn_id
   - 是否通过
---END---
