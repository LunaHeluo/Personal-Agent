# L11 · 求职任务执行编排计划

用途：设计确认后生成 `job-application-orchestration-task.md`。

---BEGIN---
你是我的 Agent 工程实现协作伙伴。请使用中文工作。

前提：需求和设计已确认。现在只生成 `job-application-orchestration-task.md`，不要修改代码。

任务文档使用有序 Task1 / Task2 / Task3 ...；每个 Task 包含任务目标、子任务、依赖关系、验收标准、预估复杂度。不要生成“状态”字段。

至少覆盖：

1. 审计并复用现有 Runtime、Workflow、Tool Loop、Plan/Todo、Context、Budget、Delegation、Gate、Eval 与 Trace，生成框架映射和选型记录。
2. 定义执行 State Schema，并将现有 Router、Planner、Task Manager、Executor、Verifier、Recovery 映射为 Node，将 Gate/Budget/Join Policy 映射为条件 Edge。
3. 实现 Route Decision Schema、规则优先级、低置信度和 Human Review 路径。
4. 实现结构化 Planner 与 Plan Validator。
5. 将 Direct、Workflow、Tool Loop、Plan/Delegation 接入统一 Executor 和显式状态转移。
6. 实现前台/后台任务模式、task_id 与任务生命周期；进程中断时明确标记 interrupted/failed，不实现步骤级 Checkpoint 恢复。
7. 实现依赖 DAG 与并行判断，检查输入依赖、共享写冲突、Result Envelope、并发、预算与限流。
8. 实现 Parent Run / Child Run、fan-out、隔离任务包、结构化 Child Result 和 fan-in。
9. 实现 Task Manager 的结构化事件、状态更新、事件幂等、并发限制、deadline、取消传播和有限重试；禁止模型轮询 Child 状态。
10. 实现 all_required、partial_allowed、first_success、deadline_reached Join Policy，并将 Join 结果接入 Merge/Verify/Human/Stop。
11. 实现确定性 Runtime Verifier 与可选 Judge Rubric，保持与离线 Eval Runner 的责任边界。
12. 实现最多 1–2 次、只修失败项的 Bounded Recovery。
13. 实现 steps/tokens/cost/time/tool_calls 的 Parent/Child 预算预检、记账和条件停止。
14. 实现可配置 Model Router 与 fallback，不硬编码秘密和不存在的模型。
15. 贯通 Context Summary/Trim、长期记忆、Todo、Plan、Task Snapshot 与 Child Result，明确数据所有权并隔离 Child Context。
16. 贯通 Route、Plan、Parent/Child Run、Task Event、Join、Verify、Recovery、Budget 与 Model Decision Trace。
17. 在现有运行详情中展示真实后台任务、Child 状态、Join Policy、验证失败、预算和停止原因。
18. 若选型结论需要引入框架，先完成最小适配或 Spike，并用同一 Fixture 比较前后行为；若结论为不迁移，保留证据并跳过迁移任务。Checkpoint/Interrupt 只进入选型说明，不生成实现任务。
19. 生成固定评测集，覆盖不同路由、计划拒绝、串并行判断、事件通知、汇合策略、部分失败、验证修复和预算边界。
20. 运行真实求职端到端 Smoke，并执行第 9、10 阶段关键回归。
21. 独立验收、修复失败和重跑相关全量测试。

输出计划后停止。收到“确认计划，开始执行”后再按顺序实现并报告证据。
---END---
