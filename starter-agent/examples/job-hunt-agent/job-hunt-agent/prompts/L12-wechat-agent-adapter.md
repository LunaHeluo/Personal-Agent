# L12 · 微信 Agent Adapter

用途：在产品需求和总体设计确认后，让 Coding Agent 参考 WeChat iLink Bot SDK，把微信作为新的消息入口接入现有 Agent。

---BEGIN---
你是我的 Agent 产品接入工程协作伙伴。请使用中文工作。

目标：在不复制 Agent Core 的前提下，为现有项目增加微信入口。微信只负责登录、收发消息、身份与 Session 映射、渠道格式转换和运行状态提示；Prompt、Tool、Memory、RAG、Safety、Approval、Plan、编排、Trace 与业务状态继续由现有 Application Service 和 Agent Core 统一管理。

开始前请审查真实仓库、已经确认的产品需求与设计，以及用户提供的 WeChatBot SDK 参考项目或对应 SDK 文档。参考 SDK 的关键能力包括：扫码登录、凭证持久化、长轮询与游标、`context_token` 生命周期、文本自然分片、媒体上传下载、限流中间件、会话过期恢复和可插拔存储。优先复用成熟 SDK，不要自行重写 iLink 协议、二维码登录、CDN 加密或消息轮询。

先提出最多 5 个必须由用户确认的问题，至少确认：

1. 现有项目语言与运行时，以及选用 Node.js `@wechatbot/wechatbot`、Python `wechatbot-sdk`，还是独立 Gateway 进程。
2. 使用测试账号还是正式账号；允许访问的微信用户白名单。
3. 凭证、游标、`context_token` 和身份映射使用文件、SQLite、Redis 还是现有数据库。
4. 本次只支持文本，还是还要支持图片、语音、文件和视频；每类媒体的大小与 MIME 限制。
5. 微信端高风险动作使用一次性确认命令，还是跳转现有 Web Approval 页面。

确认后先生成 `docs/wechat-adapter-design.md` 和 `wechat-adapter-task.md`。输出后停止，不要修改代码；等总体 `agent-product-task.md` 也完成并收到“确认总体计划，开始执行”后，再按两个任务文档的依赖顺序实现。

设计和实现必须满足：

1. 形成清晰边界：
   `WeChatBot SDK → WeChat Adapter → Application Service → Agent Core`。
   Adapter 不直接调用求职 Tool，不维护第二套 Prompt、Memory、RAG、Approval 或 Agent Loop。
2. 将微信入站消息转换为项目统一的 `InboundMessage` 或等价结构，至少包含 channel、account_id、external_user_id、external_message_id、message_type、text、attachments、received_at 和 channel_context。
3. 使用 `(channel, account_id, external_user_id)` 映射内部用户与 Session；微信 `context_token` 只用于协议回复，不能替代内部 `session_id` 或聊天历史。
4. 对 `external_message_id` 做持久化去重和幂等处理。重复投递不能重复调用模型、重复执行 Tool 或重复创建 Run。
5. 每个微信账号在全集群只允许一个有效长轮询实例；多实例部署使用租约、锁或选主机制，避免游标互相覆盖。
6. 登录凭证、游标与 `context_token` 持久化到 Git 之外的受保护存储。日志、Trace、错误响应和前端不能输出完整 token、二维码内容或用户隐私。
7. 会话过期时按 SDK 能力重新登录或恢复；恢复前暂停消费，不丢失已确认的内部 Session。提供健康状态、结构化日志、优雅停止、退避重试和明确的人工处理提示。
8. 文本回复交给 SDK 按自然边界分片；每段保持同一回复上下文。Markdown 在发送前转换为微信可读文本，不能截断关键引用、审批编号或错误说明。
9. 媒体先做类型、大小、MIME、下载来源和恶意内容边界检查，再决定是否交给模型。超限、未知或未支持媒体要明确拒绝，不能静默丢失或直接塞满模型上下文。
10. 使用用户白名单、每用户限流和并发限制。未授权用户不得访问 Agent 的 Session、简历、邮件、RAG 或 Tool。
11. 高风险 Tool 仍经过现有 Pre Tool Call / Approval Gate。微信没有可靠按钮时，使用绑定 user、session、run、pending_action 且短期有效的一次性确认命令，或引导到现有 Web Approval 页面；普通“好”“确认”不能直接批准。
12. 将 Agent 的流式事件转换为适合微信的有限状态提示，避免把每个 token 都发送为一条消息。至少正确处理 started、tool_started、approval_required、failed 和 completed。
13. 配置通过 `.env.example` 或项目配置 Schema 暴露，但只写变量名和说明，不写真实凭证。Docker/Compose 为凭证与状态提供持久化 Volume。
14. 更新 README、架构图、Runbook 和故障处理，说明首次扫码、重启免扫码、强制重新登录、会话过期、轮询冲突、禁用微信入口和撤销凭证。

至少实现并执行以下验收：

- 使用测试微信账号扫码登录，收到一条文本并通过真实 Application Service 返回模型结果。
- 在微信发送“帮我搜索悉尼的 Agent 开发岗位，选一个读取完整 JD，再对照我的简历给出有来源的匹配结论”，能够观察真实 Search、Browser/RAG 与最终回复。
- 使用同一内部 Session 从 Web 或 CLI 继续对话，历史、Run、引用和 Todo/Plan 一致。
- 触发邮件发送时停在现有 Approval Gate；错误用户、过期确认、错误 Run 和重复确认都不能执行发送。
- 重复投递同一外部消息不会重复调用模型或 Tool。
- 进程重启后凭证、游标、`context_token` 映射和内部 Session 关系按设计恢复；会话失效时给出可操作提示。
- 长回复正确分片；未支持或超限媒体得到明确提示。
- 未授权微信用户、超频用户和并发超限请求被拦截，并留下不含敏感信息的审计记录。
- 单元测试使用 fake SDK 与 fake Application Service 覆盖适配逻辑；最终验收必须包含一次真实微信、真实模型和真实工具链路，不能只用 mock 判定通过。

实现完成后生成 `docs/wechat-adapter-acceptance.md`，记录通过项、失败项、未执行项、命令、脱敏证据、剩余风险和 PASS / PARTIAL / BLOCKED。真实微信链路、身份隔离、幂等、Approval Gate、凭证保护或恢复未通过时不得判定 PASS。
---END---
