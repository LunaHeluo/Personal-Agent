# L5 · Context Memory Todo · 任务书

---BEGIN---
请阅读现有项目上下文管理机制，设计Context 与长期记忆管理方案。Token budget 和 todo plan 都是 Context 管理的一部分。

生成 `starter-agent/docs/context_token_plan.md`，必须包含：

1. 求职场景为什么会 Context 膨胀：
   - 搜索结果列表
   - JD 全文
   - 简历全文
   - 邮件线程
   - 草稿版本
   - 会话历史
   - 其他各类因素

2. Token 预算表：
   - system rules
   - current user request
   - todo plan
   - selected JD
   - resume summary
   - email summary
   - recent turns
   - tool results
   - 每一类都要写：目标预算、硬上限、超限时处理方式。
   - 特别注意：`tool_results` 不能只在最终 ContextBuilder 阶段才裁剪。单个工具结果本身就可能超过模型 token 上限，所以工具结果进入 session history 或 Context Pack 前必须先经过预算闸门。

3. 长期记忆规则：
   - 哪些信息值得跨 session 保存
   - 来源、置信度、过期时间
   - 如何查看、修改、删除
   - 外部网页 / 邮件内容不能直接写入长期记忆

4. Context 裁剪策略：
   - 原文何时保留
   - 何时只保留 summary
   - 何时只保留 citation / source id
   - 何时要求用户确认再丢弃
   - 单个 tool result 超过预算时，先做 per-tool-result truncation / chunk summary，再进入 Context。
   - 裁剪后的 tool result 必须带完整性元数据，让模型知道“这只是部分结果”：
     - `is_truncated`
     - `original_count`
     - `returned_count`
     - `omitted_count`
     - `has_more`
     - `raw_source_ref`
     - `continuation_hint`
     - `truncation_reason`
   - 多个 tool result 累计超预算时，做跨结果排序：按当前目标、来源可信度、最近性、用户确认程度排序。
   - session history 超预算时，保留最近 N 轮原文，把更早历史替换为 session summary。
   - 不允许静默丢弃会影响决策的事实；必须记录 dropped_details，必要时进入 waiting_for_user。

5. Summary 方案，而不只是 Summary 工具：
   - `summarize_context`
   - 输入：tool result / email thread / resume sections / 整体 history
   - 输出：短摘要、关键事实、来源 id、风险提醒
   - 需要设计三层 summary：
     1. `tool_result_summary`：单个工具结果太长时先压缩，例如 SerpAPI 返回很多岗位、邮箱线程很长、简历全文很长。
     2. `session_summary`：多轮对话历史太长时，把旧历史压成摘要，只保留最近几轮原文。
     3. `context_pack_summary`：本轮最终注入模型前，对 included / replaced / dropped 做结构化说明。
   - summary 必须是替换机制，不是追加机制。被 summary 替换的原文从热路径移出，只保留 source_ref，可在需要时回查。
   - summary 必须记录 `replaced`、`source_refs`、`dropped_details`、`risk_notes`。
   - 明确 Summary 与 Trim 的分工：
     - trim 由代码层完成，负责确定性的字段裁剪、数量限制、top K、metadata 保留和硬上限保护。
     - summary 由大模型完成，负责语义压缩、关键事实提取、风险提醒和待确认事项。
     - 推荐顺序是先代码 trim，再对仍然重要但过长的内容做 summary。
   - 需要设计 history compaction：
     - 达到明确阈值后，系统发起一次内部 summary 请求。
     - 建议触发条件：
       - `estimated_prompt_tokens / max_context_tokens >= 0.75`，且主要增长来自历史消息。
       - `history_tokens > history_budget_tokens`，例如超过 6000 tokens。
       - 历史消息数量超过 `keep_recent_messages` 可保留范围，例如最近 6 轮保留原文，更早消息进入 summary。
       - 下一次请求预计会超预算，并且仅靠 tool result trim 无法解决。
     - 不触发全文 / session summary 的情况：
       - 单个工具结果过大，先走 `tool_result_summary` 或 trim。
       - 当前用户输入过大，先要求缩小输入或做当前输入 summary。
       - 系统边界、当前目标、todo 状态、用户确认事实不能被压缩替换。
     - 这个请求不作为用户对话展示，只用于把旧历史压成 `session_summary`。
     - 收到 summary 后，用 `session_summary + 最近 N 轮原文` 替换后续发给模型的历史上下文。
     - UI 仍展示完整历史，只在调试区或 meta 里显示 `context compacted`、`summary_id`、`compacted_message_ids`。

6. Tool Result 预处理方案：
   - 每个工具都要有 `max_raw_chars` / `max_result_tokens` 或等价限制。
   - 搜索工具：只保留 title、company、location、url、retrieved_at、短 snippet；长 description 先截断或摘要。
   - 邮件工具：长线程按邮件分块，保留发件人、时间、主题、关键事实和 source_ref，不把全文直接塞进 Context。
   - 简历工具：全文可以保存在文件里，Context 中优先注入结构化 sections 和 summary。
   - 如果工具返回超过硬上限，应返回 `result_too_large` 或 `summarized=true`，不能把超大原文直接塞给模型。
   - 如果只是部分裁剪而不是失败，工具结果必须告诉模型：
     - 当前结果是 partial 还是 complete。
     - 还有多少结果或字段没有展开。
     - 原始内容的 source_ref。
     - 如果需要更多信息，下一步应该缩小搜索、展开某个 source_ref，还是请求用户确认。

7. Todo Plan 工具：
   - `todo_create`
   - `todo_update`
   - `todo_list`
   - 让模型显式管理求职任务进度，并在每一轮对话中看到当前 todo 状态。
   - 确保todo不会被压缩

8. 验收方案：
   - 构造一个超大单工具结果：例如 30 条岗位搜索结果或一段很长邮件线程，验证不会直接打爆模型上限。
   - 构造多个中等工具结果累计超限，验证会先排序、摘要、替换，再进入 Context。
   - 验证 `tool_result_summary`、`session_summary`、`context_pack_summary` 三层结果都有 source_ref。
   - 验证 dropped_details 说明了哪些内容被裁剪、为什么裁剪、是否需要用户确认。
   - 验证裁剪后的 tool result 明确告诉模型它是部分结果，并能继续获取更多信息。
   - 验证 history summary 后 UI 仍显示完整聊天记录，但模型请求中只发送 session_summary 和最近 N 轮。
---END---
