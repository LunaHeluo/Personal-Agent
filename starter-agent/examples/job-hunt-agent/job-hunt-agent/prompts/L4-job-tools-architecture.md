# L4 · Job Hunt Tools Architecture · 任务书

---BEGIN---
请读：
- `starter-agent/docs/agent.md`
- `starter-agent/docs/architecture.md`
- `starter-agent/docs/workflow.md`
- `starter-agent/evals/acceptance_cases.yaml`

请为求职 Agent 设计 `starter-agent/docs/tools/job_hunt_tools.md`，内容必须包含：

1. 为什么求职 Agent 需要工具，而不是只靠 Prompt：
   - 岗位信息会过期，需要搜索和来源。
   - 简历内容必须来自真实文件，不能靠模型编造。
   - 邮件读取/草稿/发送有隐私和外部副作用。
   - 外部 API key 需要有存储、切换和脱敏策略，不能散落在工具代码里。

2. 至少 5 个工具 Contract：
   - `search_jobs_serpapi`
   - `read_resume`
   - `draft_resume_patch`
   - `gmail_search` / `qq_mail_search`
   - `gmail_create_draft` / `qq_mail_create_draft`
   - 可选：`send_email`，但必须标记为 external 且需要人工确认。

3. 每个工具写清：
   - 用户价值
   - 为什么不能只用 Prompt
   - 输入 Schema
   - 输出 Schema
   - 数据源
   - 风险等级：read / write / external
   - 是否幂等
   - 超时、错误码、脱敏规则
   - 如果工具需要外部凭据，写清 key 存在哪里、如何切换 profile、工具结果中允许暴露哪些非敏感字段
   - 需要的 pytest 或人工验收步骤

4. `search_jobs_serpapi` 的密钥设计必须包含：
   - YAML 只保存 env var 名称，不保存真实 key。
   - `.env` 或系统环境变量保存真实 key。
   - `tools.serpapi.active_key` 作为默认 profile。
   - `SERPAPI_ACTIVE_KEY` 可以覆盖 active profile。
   - 至少支持 `primary -> SERPAPI_API_KEY` 和 `backup -> SERPAPI_API_KEY_BACKUP`。
   - 工具返回可以记录 `api_key_profile` 和 `api_key_env`，但不能记录真实 key。
   - 缺少当前 profile 对应 key 时，返回 `missing_api_key`，不能假装搜索成功。

5. 写清哪些可以交给 Coding Agent 生成：
   - Tool 类代码
   - settings 中的 key profile 解析
   - Pydantic/JSON Schema
   - pytest 骨架
   - README 示例命令

6. 写清哪些必须人工验收：
   - 是否夸大简历经历
   - 是否未经确认发送邮件
   - 搜索结果是否保留来源和时间
   - SerpAPI primary / backup profile 是否能切换
   - 日志、工具结果、验收记录是否没有真实 API key
   - 邮件和简历隐私是否进入日志
---END---
