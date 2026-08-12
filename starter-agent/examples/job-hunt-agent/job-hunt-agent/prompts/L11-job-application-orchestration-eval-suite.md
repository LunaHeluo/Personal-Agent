# L11 · 求职任务执行编排评测集

用途：编排机制完成后生成固定评测案例。

---BEGIN---
你是我的 Agent Evaluation 协作伙伴。请使用中文工作。

阅读需求、设计、真实 Route/Plan/Verify/Budget Schema 与 Eval Runner，生成：

- `evals/job-application-orchestration-cases.yaml`
- 对应脱敏 Fixture

至少 15 条案例，覆盖：

- 简单解释走 Direct，不生成 Plan、不调用 Tool。
- 固定求职周报走 Workflow。
- 读取单个公开 JD 走 Tool Loop。
- 三家公司调研与简历匹配走 Plan/Delegation。
- 发送求职邮件进入 Human Review。
- Router 低置信度时询问用户。
- 所需 Tool 关闭时 Plan Validator 拒绝执行。
- Plan 存在循环、重复步骤、越权动作或预算超限时拒绝。
- Verifier 发现缺少 source_url、chunk_id、必填字段或业务规则。
- Recovery 只修失败项且不超过配置次数。
- 超 steps、tokens、cost、time、tool_calls 后安全停止。
- Model Router fallback 可追踪，不改变权限与安全门禁。
- Context Summary/Trim 后 Goal、Plan、Todo 和预算状态仍保留。
- 简单任务不会错误触发第 10 阶段 Multi-Agent。
- 短任务走前台；批量岗位调研走后台并立即返回 task_id，状态生命周期真实更新。
- 三个独立 JD 可以并行读取；依赖前一步结果、共享写目标或缺少合并契约的步骤保持串行。
- Parent Run 创建隔离 Child Run；Child 只接收最小任务包、允许 Tool、预算、deadline 和 Result Envelope。
- Child 完成、失败、超时、取消通过结构化事件更新 Task Manager；重复或乱序事件不造成重复汇合。
- all_required、partial_allowed、first_success、deadline_reached 按配置决定 Parent 何时继续。
- Child 部分失败、超时和取消时，Parent 按 Join Policy 进入 Merge、Verify、Human 或 Stop，不无限等待。
- 主 Agent 只接收紧凑 Task Snapshot 与结构化结果，不接收完整 Child 对话和 Tool Result。
- 邮件发送前继续使用现有 Human Review / Approval Gate，不验证 Checkpoint 或跨重启恢复。
- Runtime Verifier 只控制当前 Run 的状态转移，不在每轮运行完整离线评测集。
- 若采用 LangChain、LangGraph 或 OpenAI Agents SDK 适配，使用相同 Fixture 比较迁移前后路由、权限、状态转移和产物契约。

Route、权限、计划、依赖 DAG、并行时序、Task Event、Join Decision、预算、调用顺序和副作用次数使用确定性断言；Judge 只用于表达质量。固定 Fixture 不依赖互联网。运行 schema/dry-run 检查并输出覆盖矩阵、命令和未覆盖风险。Checkpoint 与 Interrupt 只作为未实现的未来扩展记录，不进入评测通过条件。
---END---
