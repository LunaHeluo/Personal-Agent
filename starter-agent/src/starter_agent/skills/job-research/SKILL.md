---
name: job-research
description: Use when 用户要求搜索公开岗位、读取完整公开 JD，或基于简历知识库证据比较岗位匹配度；不要用于通用求职建议、仅润色或翻译用户已提供的文本、自动投递或任何登录后操作。
metadata:
  version: 1.3.0
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
    - 多个候选岗位按公开 URL 分别读取；单个失败时保留错误并继续下一个候选。
    - 所有 Tool Call 必须经过 Pre-Tool-Call Gate；确认前不得执行。
---
# job-research

## Preconditions

- 用户明确给出岗位、城市或关键词时直接形成公开搜索条件；不使用默认岗位或默认城市。
- 用户要求“根据我的简历找岗位”但未给出岗位关键词时，先检索简历证据，再由模型生成最小公开搜索画像（短岗位/技术关键词和用户明确提供的地点）。画像必须引用检索到的 Chunk；RAG 无证据或画像校验失败时先说明缺口，不猜测搜索条件。
- 简历知识库必须处于可用作用域。若不可用，可继续核验 JD，但不得生成个人匹配结论。
- 从当前运行时能力快照读取 Tool 状态与 Schema。关闭的 Tool 只有轻量名称可见；说明能力未启用，并请求用户在能力管理页面启用或选择降级方案。
- 当前真实依赖契约：`search_jobs_serpapi` 接收必填 `query`，可选 `location`、`limit`；Playwright `browser_navigate` 接收必填 `url`，`browser_snapshot` 使用运行时发现的可选 `target`、`filename`、`depth`、`boxes`；`retrieve_resume_evidence` 接收必填 `query` 和可选 `top_k`。每次调用仍以最新 Schema 为准。

## Workflow

1. 确定搜索画像：显式岗位条件直接使用；简历驱动请求先调用 `retrieve_resume_evidence`，只把经隐私校验的短关键词和用户明确地点传给搜索 Tool，不向搜索 Tool 发送简历正文、姓名、联系方式、公司经历原文或秘密。
2. 使用 SerpAPI Tool `search_jobs_serpapi` 搜索公开岗位线索，保留标题、公司、地点、摘要、公开 URL 与检索时间；搜索摘要不等于完整 JD。地点通过 SerpAPI Locations API 动态规范化；无法规范化时把原地点保留在查询文本中并省略 provider `location` 参数，不维护城市静态映射。
3. 规范化并排序候选岗位 URL：优先 SerpAPI 结构化结果中的雇主或直接申请链接，分享链接其次，普通 organic result 最后；不按固定招聘网站或城市写死排序。
4. 对排序后的公开 URL 分别提出 allowlist 内 `mcp__playwright__browser_navigate` 调用，再用 `mcp__playwright__browser_snapshot` 读取页面；默认读取配置数量内的候选，不等待用户先提供或选择 URL。每个候选保留请求 URL、最终来源 URL、裁剪状态与独立 Tool Trace；列表页、登录墙、超时或字段不足时继续下一个候选。
5. 从页面提取职责、必备要求、加分项、地点与关键限制。缺失字段标记为未验证，不从搜索摘要补齐。
6. 对尚未检索简历证据的流程，使用 `retrieve_resume_evidence` 按要求检索当前作用域的简历 Chunk。只引用返回的 `chunk_id`、`source_ref`、版本、行号和原文片段；没有证据时标记能力缺口，不补写经历。
7. 生成带引用的匹配分析：每项 JD 判断链接 JD URL，每项正向匹配链接简历 Chunk 引用；区分“已匹配”“缺口”“待确认”。
8. 自动抓取的 JD 只用于公开岗位预览和候选比较，不要自动入库；完整 JD 只有在用户明确确认后，才可交给既有 `job_description_ingestion` 服务入库。

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
- 搜索画像无法生成：返回 `search_profile_required`，说明缺少的简历证据或校验失败；不退回固定岗位、固定城市或未经验证的个人信息。
- 地点不被 SerpAPI 接受：使用 Locations API 的规范名称重试；仍不支持时省略 `location` 并将地点加入查询文本，同时保留安全化 provider 错误与降级状态。
- 多个岗位：分别调用 Browser 读取并校验；达到有效 JD 目标数量或候选耗尽后停止，单个失败不得终止其他候选。
- Tool 关闭、Schema 变化或不在 allowlist：不尝试旧参数；请求启用、重新审查或聊天确认。

## Output Format

1. **岗位摘要**：岗位、公司、地点、关键限制、最终 JD URL。
2. **必备要求**：职责、必备要求、加分项及字段完整性。
3. **匹配证据**：要求、判断、简历原文、`chunk_id`、`source_ref`。
4. **能力缺口**：无证据或不满足项，不提供补写经历。
5. **待确认事项**：未验证字段、是否确认 JD 入库；公开只读候选抓取本身不要求先选择岗位。
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
