# L11 · 求职任务执行编排最终验收

用途：实现完成后生成 `docs/job-application-orchestration-acceptance.md`。

---BEGIN---
你是我的 Agent 功能验收协作伙伴。请使用中文工作。

独立审查需求、设计、任务、实现、评测集、Trace、日志、运行详情和测试，执行真实验收并生成 `docs/job-application-orchestration-acceptance.md`。

必须验证：

1. Direct、Workflow、Tool Loop、Plan/Delegation、Human Review 五条路径均有真实输入和 Trace。
2. Router 输出结构化、可解释；低置信度和高风险不会硬跑。
3. 简单任务无 Plan、无 Multi-Agent；复杂任务的 Plan 执行前通过权限、Tool、依赖和预算校验。
4. 离线 Evaluation 与 Runtime Verifier 的入口、输入和动作分离；Verifier 返回具体失败项，Recovery 只修失败项并严格限制次数。
5. steps、tokens、cost、time、tool_calls 在运行时真实记账，超限立即安全停止并报告完成/未完成内容。
6. Model Router 与 fallback 可配置、可追踪，不绕过 Gate、Verifier 和预算。
7. 显式 State、Node 和条件 Edge 与设计一致；Direct、Workflow、Tool Loop、Plan 和 Human Review 不是每次全部执行。
8. 短任务走前台；后台批量调研立即返回真实 task_id，queued/running/waiting/partial/completed/failed/cancelled/interrupted 状态来自后端。
9. Planner 的依赖 DAG 能正确区分串行与并行；存在输入依赖、共享写冲突、合并契约缺失、预算或限流不足时不会并行。
10. Parent Run 能 fan-out 多个隔离 Child Run；每个 Child 的 Context、Tool、预算、deadline 和 Result Envelope 符合任务契约。
11. Child 通过结构化事件通知 Task Manager；事件重复、乱序、迟到、失败、超时和取消不会造成重复执行或重复汇合。
12. all_required、partial_allowed、first_success、deadline_reached Join Policy 真实控制 Parent 继续时机；主 Agent 不通过模型轮询 Child 状态。
13. Parent 只接收紧凑 Task Snapshot、result_ref 和结构化 Child Result；完整 Child 对话与大体积 Tool Result 不进入主 Context。
14. Summary/Trim 后 Goal、安全策略、Plan、Todo、Task Snapshot 和预算状态不丢失。
15. Trace 能关联 Route、Plan Step、Parent/Child Run、Task Event、Join Decision、Tool、Delegation、Verify、Recovery、Budget 和 Model Decision。
16. `docs/agent-runtime-framework-decision.md` 有真实仓库证据，能解释自研 Runtime、LangChain、LangGraph、OpenAI Agents SDK 的选择，并说明 Checkpoint/Interrupt 的未来价值；当前迭代不要求实现 Checkpoint 或跨重启恢复。
17. 至少 15 条固定案例可重复运行，并回归第 9 阶段安全门禁和第 10 阶段委派边界。
18. 使用真实模型、Search、Browser、RAG 完成一次包含并行 Child Run 与汇合的求职调研 Smoke；涉及发送时停在现有 Human Review，不真实投递。
19. 运行详情连接真实后端，后台任务、Child 状态、Join、错误、取消、停止和预算状态一致。

失败时继续定位、修复并重跑；外部阻塞记录原始错误和最小用户动作。输出通过项、失败项、未执行项、证据路径、剩余风险和 Release Gate：PASS / PARTIAL / BLOCKED。关键路由、计划校验、后台任务、并行判断、Child 事件、Join Policy、有限恢复、预算、安全回归与真实 Smoke 全部通过才允许 PASS。Checkpoint/Interrupt 不属于当前迭代的 PASS 条件。
---END---
