# L11 · 求职任务执行编排设计

用途：需求确认后生成 `job-application-orchestration-design.md`。

---BEGIN---
你是我的 Agent 工程设计协作伙伴。请使用中文工作。

前提：`job-application-orchestration-requirements.md` 已确认。请审查现有 Runtime、Workflow、Tool Loop、Plan/Todo、Context、Budget、Delegation、Gate、Eval、Trace、状态持久化、API 与前端。

生成 `job-application-orchestration-design.md`，并同步生成 `docs/agent-runtime-framework-decision.md`。设计文档包含需求理解与设计目标、技术选型、总体架构、模块/组件、数据模型、API / 服务接口、状态流转与交互、错误处理、性能与安全、测试策略、风险与待确认事项。

设计必须说明：

1. Router、Planner、Plan Validator、Task Manager、Executor、Verifier、Bounded Recovery、Budget Manager、Model Router 与现有 Runtime 的调用顺序和责任边界。
2. 使用显式状态图描述 State、Node、条件 Edge 和终止条件；不要把所有组件串成每次必经的一条大链。
3. 将现有 Router、Planner、Task Manager、Executor、Verifier、Recovery 映射为 Node，将 RunContext 映射为 State，将 Gate/Budget/Join Policy 映射为 Edge Condition；人工确认继续复用现有 Approval Gate。
4. Route Decision、Plan、Plan Step、Background Task、Parent Run、Child Run、Task Event、Join Decision、Validation Result、Verify Result、Recovery Attempt、Budget Snapshot、Model Decision 与 Pending Action 的字段和状态。
5. Direct、Workflow、Tool Loop、Plan/Delegation、Human Review 的进入条件、退出条件、降级和回退。
6. Router 低置信度、输入缺失、冲突规则和高风险优先级。
7. Planner 的结构化输出、依赖 DAG、done_when、预算分配及执行前校验。
8. Verifier 中确定性规则与可选 Judge 的边界；权限、Schema、来源、引用和预算不能只交给 Judge。说明它与离线 Evaluation 的输入、触发时机和动作差异。
9. Recovery 只修具体失败项，最多 1–2 次；仍失败时停止、降级或交人工。
10. Planner 怎样输出依赖 DAG，并根据输入依赖、共享写冲突、Result Envelope、预算、Provider/站点限流决定串行或并行。
11. 前台任务与后台任务的边界；后台任务怎样返回 task_id，并管理 queued、running、waiting、partial、completed、failed、cancelled 和 interrupted。
12. Parent Run 怎样 fan-out 为多个隔离 Child Run；每个 Child 的最小任务包、Tool 视图、预算、deadline、Result Envelope 和 Context 边界。
13. Child Runtime 怎样通过结构化事件通知 Task Manager；事件去重、乱序、迟到和重复完成怎样处理，禁止使用模型轮询状态。
14. all_required、partial_allowed、first_success、deadline_reached 等 Join Policy 的满足条件；Parent 何时继续 Merge/Verify，Child 失败、超时和取消时怎样处理。
15. 并发限制、每 Child 与 Parent 总预算、限流、backpressure、取消传播、有限重试和部分结果治理。
16. Budget Manager 怎样在每一步和 fan-out 前预检、每一步和 Child 结束后记账，并成为状态转移条件。
17. Model Router 的能力、成本、延迟、风险策略和 fallback；不得硬编码不存在的模型。
18. Context Summary/Trim、长期记忆、Todo、当前 Plan、Task Snapshot 与 Child Result 怎样分工；主 Agent 不接收完整 Child Context。
19. 第 10 阶段 Delegation 只作为一种执行路径，Router 不应把简单任务送入 Multi-Agent。
20. Trace 怎样关联 route_decision_id、plan_id、step_id、parent_run_id、child_run_id、task_event_id、join_decision_id、verify_id、recovery_id、budget_snapshot_id、model_decision_id 和现有 Run/Turn/Tool。
21. 运行详情怎样展示路由原因、Plan 依赖、后台任务、Child 状态、Join Policy、验证失败、修复次数、预算进度和停止原因，同时保留完整聊天展示。
22. 固定 Fixture、单元、集成、端到端、并行时序、事件幂等、超时/取消、部分失败、回归和真实求职 Smoke 测试矩阵。
23. Checkpoint 与 Interrupt 只作为框架知识和未来扩展说明：解释用途、采用信号及与 Summary/Memory 的区别，不为当前迭代设计实现任务或验收门禁。

`docs/agent-runtime-framework-decision.md` 必须比较：

- 自研 Runtime：控制力、维护成本和当前复用程度。
- LangChain：高层 Agent API、模型/Tool/Middleware 集成价值与边界。
- LangGraph：显式 State/Node/Edge、Checkpoint、Interrupt 和长期运行任务价值；Checkpoint/Interrupt 当前迭代只记录采用条件，不实现。
- OpenAI Agents SDK：Agent Loop、Tools、Handoffs、Guardrails、Sessions 与 Tracing 的价值和边界。
- 状态持久化、人工确认、可观测性、供应商绑定、迁移成本和现有测试契约。
- 最终选择、证据、保留模块、适配层和回滚方式；允许选择“不迁移”。

优先复用现有组件；不要把模式做成始终同时运行的链路。若建议引入框架，只允许先做最小适配或 Spike，并用同一 Fixture 证明边界未丢失，不得直接全面重写。输出设计后停止，等待我确认。
---END---
