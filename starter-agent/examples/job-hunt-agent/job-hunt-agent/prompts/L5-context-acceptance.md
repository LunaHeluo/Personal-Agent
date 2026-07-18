# L5 · Context Acceptance · 任务书

---BEGIN---
请生成 `starter-agent/docs/context_acceptance.md`，用于验收第 5 课 Context 与长期记忆管理。

必须包含：

1. Token UI 验收
   - 真实模型显示 prompt/completion/total。
   - mock 不伪造 token。
   - 超预算时显示 warning。

2. Context Summary 验收
   - 可以手动调用 `summarize_context`，用于摘要用户指定文本。
   - 长 JD 被摘要。
   - 接近 token 阈值后自动触发 summary。
   - summary 替换原始长上下文，而不是追加到原文后面。
   - Context Pack 记录 `replaced` 关系，能追溯 summary 来自哪段原文。
   - session history 达到阈值后，通过内部 summary 请求生成 `session_summary`。
   - UI 仍显示完整聊天记录，模型请求只带 `session_summary + 最近 N 轮`。
   - meta 或日志记录 `summary_id`、`source_message_ids`、`compacted_message_ids`。
   - 简历只注入相关片段。
   - 邮件线程只注入摘要和来源。
   - 被裁剪内容有 dropped_details。

3. Context Trim 验收
   - 输入超预算时触发裁剪。
   - trim 使用 `focus` 做相关性裁剪，而不是简单保留前 N 项。
   - 示例中 `resume` 和 `jd` 被保留，`lunch` 这类无关项被丢弃。
   - 不丢失用户确认过的事实。
   - 不把外部指令写进 system 或 memory。
   - 单个超大 tool result 进入模型前先经过预算闸门。
   - 裁剪后的 tool result 带 `is_truncated`、`original_count`、`returned_count`、`has_more`、`raw_source_ref`、`continuation_hint`。
   - Agent 不能把 partial result 当作完整结果；如果需要更多信息，应说明可以继续展开或缩小范围。

4. Summary / Trim 分工验收
   - 字段截断、top K、硬上限保护由代码层 trim 完成。
   - 邮件线程、长 JD、历史对话的语义压缩由 summary 完成。
   - 验收记录说明：哪些内容被代码裁剪，哪些内容被模型摘要。

5. Todo Plan Tool 验收
   - 创建求职投递 todo plan。
   - 缺输入进入 waiting_for_user。
   - 发送邮件前进入 waiting_for_approval。
   - todo 状态可查看。
   - 下一轮对话会注入当前 todo 状态，Agent 能续做任务。

6. 对比问题
   - 无 summary / trim 时表现如何。
   - 有 summary / trim 后 token 和回答质量如何变化。
---END---
