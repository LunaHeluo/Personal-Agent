# L10 · 求职调研任务委派设计

用途：需求确认后生成 `job-application-delegation-design.md`，不生成任务计划、不修改代码。

---BEGIN---
你是我的 Agent 工程设计协作伙伴。请使用中文工作。

前提：`job-application-delegation-requirements.md` 已确认。请审查现有 Runtime、Tool/MCP/RAG、Task/Todo、Budget、Pre-Tool-Call Gate、Eval、Trace、日志、API 与前端状态模型。

生成 `job-application-delegation-design.md`，必须包含：

- 需求理解与设计目标
- 技术选型
- 总体架构设计
- 模块/组件设计
- 数据模型
- API / 服务接口设计
- 状态流转与交互流程
- 错误处理
- 性能与安全考虑
- 测试策略
- 风险与待确认事项

设计必须说明：

1. Coordinator、内部 `delegate_task` 入口、Specialist Registry、Dispatcher、Worker Pool、Child Agent Runtime、Result Validator、Merger 与现有 Runtime 的关系。
2. 为什么委派入口可以表现为 Tool Call，但执行语义必须是创建独立 Child Run；说明它与普通 Tool、固定 Workflow 的差异和接口边界。
3. Specialist Registry 的配置来源、Schema、版本、启停、缓存刷新、能力匹配和不存在/禁用时的错误。
4. Parent Run、Child Task、Child Run、Task Contract、Budget Allocation、Result Envelope、Merge Report 的字段与状态机。
5. `job_web_researcher` 与 `profile_evidence_analyst` 的独立 System Prompt、输入、最小 Tool 集、输出 Schema 和不可越过的边界。
6. `job_web_researcher` 的网页推进状态机：候选链接 → 打开/等待渲染 → 定位正文 → 展开或进入详情页 → 提取字段 → 完整性检查 → 下一页/停止；说明最大页面数、最大步骤、每页超时、去重和停止条件。
7. 为什么单个稳定页面的一次读取保持为普通 Tool，而跨页面探索、根据 Observation 决定下一步、异常恢复和结果压缩由 Subagent 承担；主 Agent 只定义目标与结果契约，不参与导航过程。
   - 审计并画出当前直接抓取网页 Workflow 的入口、Router/API/Service/前端调用链、输出契约和测试依赖。
   - 设计迁移后的唯一主路径：符合多页面/动态页面条件的请求只能进入 `delegate_task(job_web_researcher, task_contract)`。
   - 说明旧 Workflow 怎样移除路由、废弃或收敛为底层单页 Tool/兼容 Adapter，怎样避免双轨、重复抓取、重复计费和重复写入。
   - 说明兼容策略、默认关闭的短期回滚开关、回滚期限，以及 route、legacy_path_used、child_run_id 等迁移观测字段。
8. 网页异常分类与处理：加载失败、404、重定向、动态渲染超时、选择器失效、空正文和重复页怎样有限重试或降级；登录、验证码、权限和站点拒绝访问怎样暂停并请求用户处理，不得绕过限制。
9. 网页上下文治理：原始 HTML、Snapshot、重复 DOM 和中间页面怎样留在 Child Trace/Artifact；怎样裁剪或总结单页内容；主 Agent 只吸收标准化 JD 字段、来源、缺失和错误。
10. Child Agent Runtime 怎样运行多轮 Model/Tool Loop，以及停止条件、最大步骤和与父 Runtime 的隔离。
11. 怎样把现有 `AgentRuntime / AgentLoop` 设计为可复用的无状态执行代码，并让 `runtime.run(parent_spec, parent_context)` 与 `runtime.run(child_spec, child_context)` 使用同一路径；禁止复制 Agent 对象或另建第二套 Loop。
12. Shared Infrastructure 与 Run-scoped State 的边界：哪些 Model Client、Tool 实现、Registry、Trace/Artifact/RAG 服务可以共享；哪些 messages、working memory、todo/plan、effective tool view、budget、cancellation、summary/trim 和 output buffer 必须随 RunContext 隔离。
13. Child Context Assembly 的字段所有权：Coordinator、Registry、Runtime、Context Builder 分别提供什么，冲突时怎样确定优先级。
14. 最小上下文包怎样构建，怎样通过 artifact_id、knowledge_scope、chunk_id 等引用加载必要数据，并避免复制完整会话、全部记忆、其他 Child 中间结果和无关 Tool Schema。
15. Tool Registry 如何共享、每个 Child 的 effective tool view 如何取 Task Contract、Registry 默认值与 Policy 限制的安全交集；deadline 和 budget 怎样受 Parent 剩余量约束。明确求职调研路径中主 Agent 不接收 Search/Browser/raw RAG 完整 Schema，网页子 Agent 只接收 Search/Browser，简历子 Agent 只接收授权 RAG；其他场景需要同一工具时通过场景级 Tool View 单独开放。
16. Child 写入隔离、Coordinator 合并、版本号/锁/幂等策略怎样避免多个 Run 并发修改同一计划、投递状态或 Artifact。
17. Result Envelope 的 Schema：status、output、evidence、missing、conflicts、usage、child_run_id；主 Agent 怎样只吸收该 Envelope 和 Trace 引用。
18. 完整 Child Messages、Tool 原始结果、隐藏推理和日志怎样留在 Trace Store，并执行脱敏、保留和按需查看。
19. 并发上限、超时、取消传播、幂等 key、重试次数、背压和孤儿任务清理。
20. 部分失败、迟到结果、重复结果、冲突来源、Schema 不合法和预算耗尽的处理。
21. 合并怎样先做确定性 Schema/来源/证据校验，再进行有限语义综合。
22. Tool 权限仍经过 Pre-Tool-Call Gate；父 Agent 的权限不能自动扩大到子 Agent。
23. 默认只有 Coordinator 可以委派；怎样从 Subagent 的 callable tools 中移除 `delegate_task`，防止无界递归。
24. Trace Context 怎样贯穿 eval_run_id、parent_run_id、child_task_id、child_run_id、turn_id、model_request_id、tool_call_id、policy_decision_id 和 approval_id。
25. 单 Agent 与 Multi-Agent 的固定对比指标：Task Success、wall-clock、Token、成本、来源完整性、失败复杂度。
26. Trust Center/运行详情怎样展示父子树、并发状态、预算、取消、部分结果和合并证据。
27. 固定 Fixture、单元、集成、端到端与真实 Search/Browser Smoke 测试矩阵，其中必须包含不同 RunContext 对象的身份检查和跨 Run 状态污染检查。

优先复用现有框架；不要把 Coordinator 设计成一个拥有全部上下文和全部权限的超级 Agent。输出设计后停止，等待我确认。
---END---
