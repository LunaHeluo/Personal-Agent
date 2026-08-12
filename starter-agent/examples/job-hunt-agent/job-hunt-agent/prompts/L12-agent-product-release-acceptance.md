# L12 · Agent 产品发布验收

用途：实现完成后生成 `docs/agent-product-release-acceptance.md`。

---BEGIN---
你是我的 Agent 产品发布验收协作伙伴。请使用中文工作。

独立审查产品需求、设计、任务、实现、测试、评测、安全记录、部署记录、Runbook 和备份恢复证据。执行真实验收并生成 `docs/agent-product-release-acceptance.md`。

必须验证：

1. 新环境依据 README 和 Compose 可以启动，依赖健康后 Application Service 才进入可用状态。
2. Web、CLI 与微信复用同一 Session、Run、Approval 和 Agent Core；刷新与重启后状态一致。
3. 流式文本、Tool、引用、预算、Plan/Todo、Checkpoint、审批、取消、失败和完成状态均来自真实后端。
4. 求职端到端路径使用真实模型、Search、Browser 和 RAG；邮件停在确认，不进行未授权发送。
5. 未授权用户被 Access 或应用认证拦截；用户不能读取其他 Session。
6. 浏览器、日志、Trace、镜像、构建产物和 Git 历史没有真实 Secret。
7. 真实 HTTPS 域名、Cloudflare Tunnel、Access、健康检查和 Web 对话可以访问。
8. GitHub 发布经过测试和生产 Environment 规则，失败部署可以回滚。
9. 数据库、源文件、RAG 索引和配置完成一次备份、清空测试环境、恢复和结果校验。
10. Provider、数据库或 Tunnel 故障至少演练一项，用户状态、Trace、降级和 Runbook 一致。
11. 现有 Eval、Safety、Delegation 和 Orchestration 关键回归全部通过。
12. Runbook 能由不了解实现的人完成启动、检查、备份、恢复、禁用危险 Tool 和回滚。
13. 微信测试账号能真实扫码登录、收发消息并运行求职链路；外部消息幂等、用户白名单、`context_token`、长轮询单实例、会话恢复、长文本分片和 Approval Gate 符合设计。

输出通过项、失败项、未执行项、命令、证据路径、剩余风险和 Release Gate：PASS / PARTIAL / BLOCKED。外部阻塞记录原始错误和最小用户动作。关键安全、Secret、备份恢复、真实 HTTPS、真实微信链路、核心求职路径和回归未通过时不得判定 PASS。
---END---
