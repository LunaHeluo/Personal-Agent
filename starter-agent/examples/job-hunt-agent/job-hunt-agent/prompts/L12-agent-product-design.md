# L12 · Agent 产品化设计

用途：需求确认后生成 `agent-product-design.md`。

---BEGIN---
你是我的 Agent 产品工程设计协作伙伴。请使用中文工作。

前提：`agent-product-requirements.md` 已确认。请审查现有 Agent Core、Application Service、CLI、Web、Session/Run Store、事件协议、Tool Gate、Context、RAG、编排、Trace、Docker、Cloudflare、GitHub 和部署文档。

生成 `agent-product-design.md`，包含需求理解与设计目标、技术选型、总体架构、模块/组件设计、数据模型、API / 事件接口设计、状态流转与交互流程、部署拓扑、错误处理、性能与安全、测试策略、风险与待确认事项。

设计必须说明：

1. Browser / CLI / WeChat Adapter、Cloudflare Access/Tunnel、Web/API、Application Service、Agent Core、Provider、Session Store、Artifact Store 和日志/Trace 的边界。
2. Web、CLI 与微信怎样复用同一 Session、Run、Approval 和 Agent Core；微信外部身份、内部用户与 Session 的映射方式。
3. REST 与流式事件契约；至少定义 message_delta、tool_started、tool_completed、approval_required、run_paused、run_resumed、run_cancelled、run_failed 和 run_completed。
4. UI 刷新、断线重连、重复事件、乱序事件和服务重启后的恢复方式。
5. 身份、Session 所有权、Cloudflare Access、应用认证、CORS/CSRF、限流和审计边界。
6. Secret 只保存在部署环境或 Secret Store，浏览器、日志、Trace、镜像和 Git 历史不包含真实值。
7. Docker Compose 服务、Volume、网络、健康检查、依赖就绪、迁移、重启和日志策略。
8. GitHub CI/CD 的测试、构建、Environment、审批、最小 Secret、部署、Smoke、回滚和部署证据。
9. 数据库、上传源文件、RAG 索引、配置和部署版本的备份恢复顺序、RPO/RTO 目标及恢复校验。
10. Provider、数据库、Tunnel、迁移、磁盘和 Tool 故障的用户状态、降级、止血和 Runbook。
11. Release Gate 怎样复用现有 Eval、Safety、Trace 和编排验收，不重新实现一套评测。
12. 前端设计文档怎样让学生依据文档升级现有 Starter Agent，而不是依赖隐藏实现。
13. 微信 SDK 的扫码登录、长轮询、游标、`context_token`、文本分片与恢复职责，及 Adapter 的幂等、白名单、限流、Approval 和事件转换职责。

优先复用现有代码和 L11 部署产物。输出设计后停止，等待我确认。
---END---
