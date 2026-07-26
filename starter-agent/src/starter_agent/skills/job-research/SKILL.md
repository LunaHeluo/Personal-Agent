---
name: job-research
description: Use when 用户要求搜索公开岗位、读取完整公开 JD，或基于简历知识库证据比较岗位匹配度；不要用于通用求职建议、仅润色或翻译用户已提供的文本、自动投递或任何登录后操作。
metadata:
  version: 1.1.0
  source: builtin
  enabled: true
  dependencies:
    tools:
      - search_jobs_serpapi
      - retrieve_resume_evidence
    mcp:
      - mcp__playwright__browser_navigate
      - mcp__playwright__browser_snapshot
    services:
      - job_description_ingestion
  trigger_examples:
    - 搜索上海的 AI Agent 工程师岗位并和我的简历比较
    - 读取这个公开 JD，分析职责、要求和我的匹配证据
    - 帮我调研这些候选岗位，列出能力缺口和来源
  negative_examples:
    - 给我一些通用求职建议
    - 只润色这段我已经提供的简历文字
    - 翻译这段 JD，不要做外部调研
  validation:
    - 保留最终 JD 来源 URL，并检查职责、必备要求、加分项、地点和关键限制。
    - 每项正向匹配必须引用 retrieve_resume_evidence 返回的 Chunk；无证据项必须标记为缺口。
    - 标记页面拒绝访问、内容裁剪、未验证字段及每次 Tool Trace。
  failure_policy:
    - 依赖关闭或不可用时说明缺失能力与未完成步骤，不伪造搜索、JD 或简历证据。
    - 多个候选岗位无法确定时停止并请求用户选择。
    - 所有 Tool Call 必须经过 Pre-Tool-Call Gate；确认前不得执行。
---
# job-research

## Preconditions

- 已取得目标岗位、城市或岗位关键词；信息不足以形成搜索条件时先询问用户。
- 简历知识库必须处于可用作用域。若不可用，可继续核验 JD，但不得生成个人匹配结论。
- 从当前运行时能力快照读取 Tool 状态与 Schema。关闭的 Tool 只有轻量名称可见；说明能力未启用，并请求用户在能力管理页面启用或选择降级方案。
- 当前真实依赖契约：`search_jobs_serpapi` 接收必填 `query`，可选 `location`、`limit`；Playwright `browser_navigate` 接收必填 `url`，`browser_snapshot` 使用运行时发现的可选 `target`、`filename`、`depth`、`boxes`；`retrieve_resume_evidence` 接收必填 `query` 和可选 `top_k`。每次调用仍以最新 Schema 为准。

## Workflow

1. 使用 SerpAPI Tool `search_jobs_serpapi` 搜索公开岗位线索，保留标题、公司、地点、摘要、公开 URL 与检索时间；搜索摘要不等于完整 JD。
2. 向用户展示候选岗位。存在多个候选时停止并请求用户选择，不自行猜测“最合适”的 URL；用户选择不明确时继续等待。
3. 对选中的公开 URL 提出 allowlist 内 `mcp__playwright__browser_navigate` 调用，再用 `mcp__playwright__browser_snapshot` 读取页面；保留请求 URL、最终来源 URL、裁剪状态与 Tool Trace。
4. 从页面提取职责、必备要求、加分项、地点与关键限制。缺失字段标记为未验证，不从搜索摘要补齐。
5. 使用 `retrieve_resume_evidence` 按要求检索当前作用域的简历 Chunk。只引用返回的 `chunk_id`、`source_ref`、版本、行号和原文片段；没有证据时标记能力缺口，不补写经历。
6. 生成带引用的匹配分析：每项 JD 判断链接 JD URL，每项正向匹配链接简历 Chunk 引用；区分“已匹配”“缺口”“待确认”。
7. 完整 JD 只有在用户明确确认后，才可交给既有 `job_description_ingestion` 服务入库。

## Validation

- JD 来源是选中的最终公开 URL，页面类型确为具体岗位详情页。
- 岗位名称、公司、地点、职责、必备要求至少可核验；加分项和关键限制缺失时明确标记。
- 每项匹配结论都有简历 Chunk 引用；引用与结论一致，未命中项不推断。
- 页面裁剪、登录墙、拒绝访问、动态内容缺失和其他未验证信息均显式展示。
- Tool Trace 按顺序包含搜索、导航、页面读取和 RAG 的输入摘要、结果状态、来源、错误及裁剪标记。

## Failure Handling

- Server 不可用：返回依赖不可用状态，说明 `playwright` 的连接或健康错误并停止读取 JD。
- 页面不允许访问：保留 URL 与错误，停止；不绕过 robots、登录、验证码、付费墙或反爬限制。
- 内容被裁剪：标记不完整；缩小到只读目标范围后可再次提出调用，仍不完整则停止完整性结论。
- RAG 无证据：保留已验证 JD，将相关要求列为缺口，不给出虚构匹配。
- 多个岗位无法确定：展示候选差异并等待用户选择，不调用 Browser。
- Tool 关闭、Schema 变化或不在 allowlist：不尝试旧参数；请求启用、重新审查或聊天确认。

## Output Format

1. **岗位摘要**：岗位、公司、地点、关键限制、最终 JD URL。
2. **必备要求**：职责、必备要求、加分项及字段完整性。
3. **匹配证据**：要求、判断、简历原文、`chunk_id`、`source_ref`。
4. **能力缺口**：无证据或不满足项，不提供补写经历。
5. **待确认事项**：候选选择、未验证字段、是否确认 JD 入库。
6. **来源与 Tool Trace**：JD URL、检索时间、调用顺序、结果状态、错误和裁剪标记。

## Safety Boundaries

- 本 Skill 只提出 Tool Call，不直接调用 MCP Client，也不得绕过统一 Pre-Tool-Call Gate。
- 白名单外调用必须等待聊天确认；强制人工确认动作每次确认，不能通过加入白名单绕过。
- 不自动投递、登录、发邮件、填表、上传或提交申请；不访问未授权内容。
- 不夸大或补写简历经历，不隐藏 Tool 失败、裁剪或无证据状态。
- 不覆盖或弱化 `docs/agent.md` 与 System Prompt；发生冲突时停止并请求人工确认。

## Trigger Examples

- “搜索杭州的机器学习岗位，选一个公开 JD 和我的简历做引用分析。”
- “调研这个公开岗位链接，列出必备要求、证据和缺口。”

## Non-trigger Examples

- “给应届生一些面试建议。”
- “只把我粘贴的这段文字翻译成英文。”
