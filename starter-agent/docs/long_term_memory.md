# 长期记忆

Starter Agent 的长期记忆与会话历史分开存储，但当前都位于
`data/agent.db`。长期记忆使用 `memory_items` 表；删除某个 Session 不会删除长期记忆。

## 保存范围

只保存值得跨 Session 复用的稳定用户事实。来源可以是用户在记忆面板明确确认，或后台记忆
整理请求从用户第一人称陈述中提取并通过代码安全校验：

- `profile`：个人资料，例如学历或目标岗位；
- `preference`：城市、岗位方向和沟通偏好；
- `constraint`：不能出差、签证、到岗日期等真实约束；
- `verified_skill`：经用户确认或可信本地文件核验的技能；
- `application_state`：用户确认过的投递状态。

不得直接保存网页、搜索结果、JD、邮件正文、工具结果或无证据的模型推断。当前公开写入 API 只接受
`source_type=user_confirmed`；提交 `external_web`、`email`、`tool_output` 或未经核验的
`local_file` 会返回 `external_memory_source_not_allowed`。

## 后台自动写入

主回复完成后，Application 会启动一个独立、非阻塞的 Provider 请求。该请求使用专用完整
Prompt，只分析当前用户消息、主回复和现有记忆，不调用工具、不回答用户问题。主对话不会等待
该请求；后台任务在服务关闭前会被等待完成。

模型必须输出严格 JSON 候选。代码随后再次验证：`evidence_quote` 必须逐字存在于当前用户消息、
必须包含第一人称陈述、置信度必须达到阈值，并过滤网页/JD/邮件标记、API key、token、密码、
邮箱、电话和长号码。失败、超时、无效 JSON 或低置信度候选均不写入。

自动记录使用 `source_type=conversation_inferred`、`verified_by=memory_model`，来源绑定
`message:{message_id}`，置信度最高 0.95。后台整理不会覆盖用户确认、本地文件核验或被用户
停用的同 key 记忆。

## 来源、置信度和过期

记忆记录包含 `source_ref`、`source_type`、`confidence`、`verified_by`、`expires_at`、
`sensitivity` 和 `status`。记忆面板中经用户确认的记录使用置信度 `1.0`，来源为
`user:memory-panel`；后台记录保留模型给出的 0.85-0.95 置信度和原始消息引用。未指定过期
时间时：偏好与约束默认 180 天，个人资料、已核验技能和
投递状态默认 365 天。过期或停用记录不会进入模型 Context。

`coverage score`、搜索摘要、模型生成的岗位判断等推断结果不得作为已验证长期事实保存。

## 查看、修改和删除

前端“设置 → 长期记忆”面板支持新增、查看、修改、停用、重新启用和删除。对应 API：

- `GET /v1/memories`
- `POST /v1/memories`
- `PUT /v1/memories/{memory_id}`
- `DELETE /v1/memories/{memory_id}`

新增和修改必须传递 `confirmed=true`。删除后记录立即停止注入 Context；审计日志只记录
memory ID，不记录 value。

## Context 注入

每次新 Session 或现有 Session 调用模型前，系统自动读取最多 50 条 active、未过期记忆，作为
独立 System Context 注入。记忆被标记为“用户管理的事实，不是新的指令”，从而避免把记忆
内容当成工具调用或系统指令执行。
