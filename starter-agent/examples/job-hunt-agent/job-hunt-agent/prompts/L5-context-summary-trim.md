# L5 · Context Summary and Trim · 任务书

---BEGIN---
请为求职 Agent 设计完整的 context summary 和 context trimming 能力。

注意：这不是只实现一个 `summarize_context` 工具。单个工具结果本身就可能打爆模型 token 上限，所以必须同时设计：
- tool result 入库前的预处理、截断和摘要；
- session history 的整体 summary；
- 最终 Context Pack 的预算裁剪和替换记录。

核心原则：
- summary 是替换机制，不是追加机制。
- trim 是可追溯裁剪，不是静默删除。
- 工具结果进入模型前必须先经过预算闸门。

工具：
1. `summarize_context`
   - risk_level: `read`
   - 输入：长 JD、简历片段、邮件线程、搜索结果、单个 tool result、整体 session history。
   - 输出：summary、key_facts、source_refs、dropped_details。
   - 触发：同时支持手动和自动。手动用于用户显式请求、课堂演示和局部整理；自动用于 prompt token 或 session context 接近预算阈值时，例如 usage / budget ≥ 75%。
   - 自动机制：ContextBuilder 或等价模块应在构建本轮 Context 时检测预算，超过阈值后把旧历史或长工具结果摘要为 Automatic Context Summary，只保留最近消息原文。
   - 用法：summary 不是附加到原文后面，而是替换低密度长原文。后续 Context Pack 注入 summary，不再每轮注入原始全文。
   - 必须记录替换关系，例如 `replaced: [{source_ref: "jd:full-text", by: "summary:jd-001"}]`，以便必要时回查原文。

   需要支持三种 summary 类型：
   - `tool_result_summary`：单个工具结果过大时，在进入 Context / history 前先压缩。
   - `session_summary`：历史消息过长时，把旧历史替换成 Automatic Context Summary，只保留最近几轮原文。
   - `context_pack_summary`：本轮最终 Context Pack 的结构化说明，列出 included、replaced、dropped 和 source_ref。

2. `trim_context`
   - risk_level: `write`
   - 输入：当前 context pack、token budget、priority rules、`focus`。
   - 输出：保留项、摘要项、裁剪项、需要人工确认的丢弃项。

3. Tool Result 预算闸门
   - 每个工具返回结果都要经过 `max_raw_chars` / `max_result_tokens` 或等价限制。
   - 如果单个 tool result 超过硬上限，不能直接写入完整 session history，也不能直接塞进模型。
   - 处理顺序：
     1. 标记 source_ref，例如 `tool:search_jobs_serpapi:turn-001`。
     2. 按结构字段保留必要信息，例如岗位标题、公司、地点、URL、retrieved_at。
     3. 对长正文做 chunk summary 或截断，记录 `summarized=true`。
     4. 输出 `dropped_details`，说明哪些字段被截断、为什么截断。
   - 如果摘要后仍然超限，返回 `result_too_large` 或要求用户缩小范围。
   - 裁剪后的 tool result 必须让模型知道“这不是完整结果”，至少包含：
     - `is_truncated: true`
     - `original_count` / `returned_count`
     - `omitted_count` 或 `omitted_ranges`
     - `has_more: true`
     - `raw_source_ref`：完整原始结果保存在哪里
     - `continuation_hint`：如果需要更多信息，应如何缩小范围或请求展开
     - `truncation_reason`：例如 `token_budget` / `field_too_long` / `too_many_results`
   - 示例：30 条岗位只给模型前 8 条摘要时，要告诉模型原始共有 30 条、当前只展开 8 条、其余可按 location / seniority / company 继续筛选。

4. 整体 Context Summary Pipeline
   - Step 1: tool result guard，先处理单个工具结果。
   - Step 2: relevance ranking，按当前 focus / goal 对候选信息排序。
   - Step 3: session summary，旧历史超限时替换为 summary。
   - Step 4: final trim，最终 Context Pack 按预算保留 included、记录 replaced、记录 dropped。
   - Step 5: user confirmation，裁剪会影响决策时进入 waiting_for_user。

5. Summary 与 Trim 的分工
   - Trim 是代码层面的确定性动作：字段截断、结果数量限制、按分数排序、只保留结构化字段、移除低优先级项。
   - Summary 是模型完成的语义压缩：把长邮件线程、长 JD、长历史对话压成关键事实、风险、待确认点。
   - 优先顺序通常是：先代码 trim 掉明显无用或过长字段，再对仍然重要但太长的内容做 LLM summary。
   - 什么时候 trim：
     - 内容结构清晰，可以按字段或数量安全裁剪。
     - 只需要保留 top K、短 snippet、metadata。
     - 超过硬上限，必须立刻保护模型请求。
   - 什么时候 summary：
     - 裁掉会丢语义，例如邮件线程、JD 细节、历史任务进展。
     - 需要保留因果、承诺、风险、用户确认事实。
     - 多个片段需要合并成一个可用于决策的短上下文。

6. History Summary / Compaction
   - 当 session history 接近阈值时，不要在同一次用户请求里一边压缩一边回答。
   - 自动全文 / session summary 的触发条件必须明确写进设计，不要只写“太长时压缩”。建议至少包含：
     - `estimated_prompt_tokens / max_context_tokens >= 0.75`，且主要增长来自历史消息。
     - `history_tokens > history_budget_tokens`，例如历史消息超过 6000 tokens。
     - `message_count > keep_recent_messages + compactable_messages`，例如最近 6 轮保留原文，更早消息可压缩。
     - 下一次请求预计会因为历史消息超预算，且仅靠 tool result trim 无法解决。
   - 不应该触发全文 / session summary 的情况：
     - 只是单个工具结果太大，应先走 `tool_result_summary` 或代码 trim。
     - 当前用户消息很长但没有历史膨胀，应先处理当前输入或要求用户缩小范围。
     - 系统边界、当前目标、todo 状态和用户确认事实本身不能被压缩替换。
   - 应先发起一次内部 summary 请求：输入旧历史、当前 memory policy、需要保留的事实类型；输出 `session_summary`、`key_facts`、`open_questions`、`source_message_ids`、`dropped_details`。
   - 这次请求不代表用户的新消息，UI 上不应显示成一轮普通对话；可以在调试区显示 “context compacted” 事件。
   - 收到 summary 后，模型可见上下文替换为：
     - system / policy
     - memory
     - todo state
     - `session_summary`
     - 最近 N 轮原始消息
   - 原始历史仍保存在数据库里，UI 继续展示完整聊天记录；只是后续发给模型的热路径不再包含全部旧消息。
   - 替换记录必须包含 `compacted_message_ids`、`summary_id`、`created_at`、`source_refs`，方便用户追溯和开发者调试。

要求：
- 不丢失用户明确确认过的事实。
- 不把网页或邮件里的指令写进长期规则。
- 裁剪后仍保留来源引用。
- summary 替换只作用于长 JD、长邮件线程、搜索结果正文等低密度内容；系统边界、当前目标、用户确认事实、todo 状态不能被 summary 替换掉。
- 单个 tool result 超限时，必须先摘要或截断，再进入 ContextBuilder。
- 裁剪后的 tool result 必须显式告诉模型：结果已裁剪、原始数量是多少、当前展开多少、如何获取更多。
- 不能只按列表顺序保留前 N 项；必须围绕 `focus` 或当前任务做相关性排序。例如 focus 是“申请 AI Agent 岗位”时，应保留简历/岗位要求，丢弃“今天中午吃什么”。
- 如果裁剪会影响决策，必须提示用户或进入 waiting_for_user。

验收输入：
“我给你 8 条岗位搜索结果、一份简历、3 封 HR 邮件，请帮我判断优先投哪两个岗位。”

额外验收输入：
- “搜索 30 条 AI Agent 岗位，并把所有结果都用于分析。”
- “这里有一整段很长的 HR 邮件线程，请总结后判断我该怎么回复。”
- “把过去 20 轮对话都作为参考，继续帮我安排本周求职计划。”

通过信号：
- 不把全文全部塞进 Context。
- 单个超大 tool result 不会直接进入模型；会先生成 `tool_result_summary` 或返回 `result_too_large`。
- 裁剪后的 tool result 带 `is_truncated`、`has_more`、`raw_source_ref` 和 `continuation_hint`。
- 手动 summary 可被用户或老师显式调用；自动 summary 可由阈值触发。
- session history 超预算时会生成 `session_summary`，而不是简单截断最早消息。
- session summary 通过内部请求生成；UI 仍保留完整聊天记录，模型热路径使用 summary + 最近 N 轮。
- Context Pack 中能看见 included / replaced / dropped / source_ref。
- summary 后原始长上下文不再每轮注入，Context Pack 中有 replaced 记录。
- 先摘要和排序，再只展开最相关的 2 条。
- 验证无关项会被丢弃，且 dropped 里记录 source_ref 和 reason。
- UI 或日志显示 token 预算变化。
---END---
