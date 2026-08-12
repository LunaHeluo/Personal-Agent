# L12 · Agent 产品化任务计划

用途：设计确认后生成 `agent-product-task.md`。

---BEGIN---
你是我的 Agent 产品工程实现协作伙伴。请使用中文工作。

前提：产品需求和设计已确认。现在只生成 `agent-product-task.md`，不要修改代码。

任务文档使用有序 Task1 / Task2 / Task3 ...；每个 Task 包含任务目标、子任务、依赖关系、验收标准、预估复杂度。不要生成“状态”字段。

至少覆盖：

1. 审计现有 Agent Core、Application Service、Web、CLI、Session/Run Store、事件协议和部署产物。
2. 固化 Session、Run、Approval、Artifact 与流式事件 Schema。
3. 让 Web 和 CLI 真实复用同一 Application Service、Session 和 Agent Core。
4. 使用 `L12-wechat-agent-adapter.md` 完成微信 Adapter、身份与 Session 映射、幂等、扫码登录、长轮询恢复、限流和 Approval Gate 接入。
5. 完成 Web 的会话恢复、流式输出、Tool/来源/预算/Plan 状态、审批、取消、错误和重试。
6. 完成身份、Session 所有权、Access、应用认证、CORS/CSRF、限流和 Secret 边界。
7. 完成 Docker Compose、健康检查、Volume、迁移、重启、日志和新环境启动。
8. 完成 GitHub 测试、构建、受保护部署、Smoke Test、部署记录和回滚。
9. 完成数据库、上传源文件、RAG 索引、微信状态和配置的备份恢复脚本与演练。
10. 完成 Provider、数据库、Tunnel、微信会话/轮询、磁盘和 Tool 故障处理及 `docs/runbook.md`。
11. 执行固定 Eval、安全回归、Secret 扫描、真实 HTTPS、真实微信端到端和恢复演练。
12. 生成 `docs/agent-product-release-acceptance.md` 所需证据。

每个任务应可独立执行并按顺序验收。输出计划后停止；收到“确认计划，开始执行”后再修改代码。
---END---
