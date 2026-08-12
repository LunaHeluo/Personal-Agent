# L12 · Agent 产品化需求

用途：生成 `agent-product-requirements.md`，明确 Web、部署、运维和发布范围。

---BEGIN---
你是我的 Agent 产品开发协作伙伴。请使用中文工作。

我要把现有 Agent Core 交付为可访问、可使用、可运维、可发布的产品。先审查真实仓库，以及已经确认的 Agent 架构、评测、安全、编排和 `DEPLOYMENT.md`，再提出最多 5 个必须由用户补充的问题。

第一阶段只生成 `agent-product-requirements.md`，包含：

- 需求背景
- 功能范围
- 目标用户与使用场景
- 用户故事
- 功能需求
- 非功能需求
- 验收标准
- 边界情况
- 风险与待确认事项

功能需求至少覆盖：

- Web 与 CLI 调用同一个 Application Service 和 Agent Core，不复制 Prompt、Tool、Memory、RAG、Gate 或编排逻辑。
- 微信通过独立 WeChat Adapter 接入同一个 Application Service；扫码登录、长轮询、`context_token` 和渠道格式由 Adapter/SDK 处理，业务状态与 Agent 能力不另存一套。
- 创建、列出、恢复和继续 Session；页面刷新或服务重启后，聊天展示与真实运行状态一致。
- 流式消息、Tool 状态、来源引用、Token/预算、Plan/Todo、Checkpoint、Interrupt、审批、取消、错误和重试均有真实后端事件。
- 关闭的 Tool/MCP 不暴露完整 Schema；高风险 Tool 在执行前进入确认流程。
- Web 不接触模型、邮箱、SerpAPI、Cloudflare 或数据库 Secret。
- 复用现有 Docker、Cloudflare Named Tunnel、Access、域名和 GitHub 预检结果，支持本机与云服务器两种部署模式。
- 区分匿名公开、Cloudflare Access 保护和应用账号三种访问策略，不默认公开私人简历、邮件和求职记录。
- Docker Compose 包含健康检查、持久化 Volume、迁移或初始化、重启策略和可诊断日志。
- GitHub 发布链路包含测试、构建、部署审批、生产 Secret、部署记录、Smoke Test 和失败回滚。
- Runbook 覆盖启动、停止、健康检查、日志、备份、恢复、禁用危险 Tool、微信会话失效或轮询冲突、Provider 故障、Cloudflare Tunnel 故障和回滚。
- 发布前执行固定评测、安全门禁、Secret 扫描、备份恢复演练和真实 HTTPS 端到端验收。

至少覆盖求职 Agent 的真实路径：搜索岗位、读取完整 JD、检索简历证据、生成匹配结论、生成邮件草稿并在发送前等待确认。

不要实现第二套 Agent Runtime 或前端静态假状态。输出需求后停止，等待我确认。
---END---
