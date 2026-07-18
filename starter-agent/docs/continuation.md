# 模型调用上限与续写

`runtime.max_model_calls` 限制一次用户请求中 Provider 的调用轮数。工具型 Agent 的一次任务可能
包含多轮“模型选择工具 → 工具返回 → 模型继续判断”，因此触发该上限不代表输出 Token 用完，
也不等同于模型 Context 超限。

当前 `config/config.yaml` 的上限为 4。达到上限时，Runtime 返回
`finish_reason=continuation_required`，而不是将本轮标记为普通请求失败。返回值包含：

- `continuation.reason=max_model_calls`；
- 本轮 `model_calls` 与 `tool_calls`；
- 用于下一轮的 `next_message`；
- 本轮已经产生的真实 Provider usage。

Runtime 会先持久化 assistant tool-call 消息及对应 tool result。前端在同一个 assistant 对话框中
保留工具执行记录，并显示“继续生成”按钮。用户点击后，前端沿用原 Session 发送
`next_message`；新的运行预算从零开始，但 Context 中保留上一轮的工具结果。System Prompt 要求
优先复用这些结果，不重复执行已经成功的相同工具。

以下情况不应通过续写绕过：重复的相同工具调用、`max_tool_calls`、总运行时间、Context Token
硬上限或工具策略拒绝。这些情况仍返回对应错误，需要修正调用逻辑、输入或配置。

为了兼容已有 SQLite 数据库，启动时会执行加法迁移，为 `messages` 表补充
`tool_calls_json`。这使跨请求恢复时能够还原 Provider 所需的 assistant tool-call 结构。
