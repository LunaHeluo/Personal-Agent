# L11 · 求职任务执行编排需求

用途：生成 `job-application-orchestration-requirements.md`，只做需求澄清。

---BEGIN---
你是我的 Agent 功能开发协作伙伴。请使用中文工作。

我要在现有求职 Agent 上增加「执行编排」能力，让系统根据任务复杂度、风险、可用能力和预算，在以下路径中做出可解释选择：

- Direct：无需 Tool 的简单解释或确认。
- Workflow：步骤固定、规则明确的任务。
- Tool Loop：需要外部 Tool，但不必预先生成完整计划。
- Plan / Delegation：复杂开放任务，需要校验计划并可能调用第 10 阶段的任务委派。
- Human Review：投递、发送邮件、修改外部数据等高风险动作。

必须复用现有 Agent Runtime、Workflow、Tool/MCP/RAG、Pre-Tool-Call Gate、Context/Token/Plan、Multi-Agent Delegation、Eval、Trace 与 Safety Gate。

先审查真实仓库，再提出最多 5 个必要问题，优先确认现有入口分类、Plan/Todo、前台/后台任务、并发上限、Subagent 委派、结果汇合、预算、重试、人工确认、Trace 与前端调试能力。第一阶段只生成 `job-application-orchestration-requirements.md`，包含：

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

- Router 输出 route、confidence、reason、required_capabilities、risk_level 和 fallback，不直接执行 Tool。
- 低置信度、输入缺失或高风险时询问用户或进入 Human Review，不能硬猜。
- 简单任务不生成 Plan、不启动 Multi-Agent。
- Planner 只处理复杂任务，Step 包含 goal、inputs、capabilities、done_when、risk、budget，并在执行前校验。
- Plan Validator 检查权限、Tool/MCP 启用状态、依赖、循环、预算和不可逆动作。
- Verifier 使用 Schema、业务规则、来源、引用和产品 Rubric 返回具体失败项。
- Recovery 只针对失败项进行最多 1–2 次修复；禁止无限 Reflection 和全文无差别重写。
- Budget 在运行时限制 steps、tokens、cost、wall-clock 与 tool_calls；超限时停止并返回已完成、未完成和恢复方式。
- Model Router 根据任务复杂度与风险选择模型，保留可解释依据；高风险依靠验证和确认，不靠盲目换大模型。
- Context 管理继续复用摘要、裁剪、记忆和 Todo；必须保留 Goal、安全策略、当前 Plan 和预算状态。
- 使用显式执行状态保存 route、plan、current_step、outputs/artifact_refs、budget、pending_action、revision_count、background_task 和 child_runs。
- 区分前台与后台任务。后台任务创建后立即返回 task_id，并具有 queued、running、waiting、partial、completed、failed、cancelled 或 interrupted 等明确状态；不要求实现步骤级 Checkpoint 或跨重启原节点恢复。
- Planner 生成依赖 DAG；只有输入独立、没有共享写冲突、具备统一 Result Envelope 且预算/限流允许的步骤才能并行。
- Subagent 并行使用 Parent Run / Child Run 模型。每个 Child 只接收最小任务包、允许 Tool、预算、deadline 和输出契约，不共享完整主对话。
- Task Manager 负责启动、并发限制、deadline、取消、有限重试、状态更新和结果引用；禁止主 Agent 通过反复模型调用轮询 Child 是否完成。
- Child Runtime 通过结构化事件通知 Task Manager，至少包含 child_started、child_progress、child_completed、child_failed、child_cancelled 和 child_timed_out。
- Parent 只在 Join Policy 满足或需要决策时继续。至少支持 all_required、partial_allowed、first_success 和 deadline_reached，并明确失败、缺失与部分结果怎样进入 Merge/Verify。
- 每轮 Trace 记录 Route、Plan、Validation、Parent/Child Run、Task Event、Join Decision、Verify、Recovery、Budget 与 Model Decision。
- 在现有运行详情中查看编排决策、Plan 依赖、后台任务、Child 状态、汇合策略、失败项、修复次数和预算，不使用静态状态。
- 明确区分离线 Evaluation 与运行时 Verifier：前者比较版本和决定发布，后者只决定当前 Run 进入 END、Recovery、Human Review 或 Stop。
- 对比自研 Runtime、LangChain、LangGraph 与 OpenAI Agents SDK 的适用层次和迁移代价；允许结论为“不迁移”，不得为了学习框架重写已经稳定的模块。
- 在框架选型记录中说明 Checkpoint 与 Interrupt 的作用、未来采用条件及其与 Summary/Memory 的区别；当前迭代不实现 Checkpoint 存储、跨重启恢复或 LangGraph Runtime。

至少覆盖简单问答、固定求职周报、读取单个 JD、后台批量调研、三个独立 JD 并行读取、JD 与简历证据并行搜集、汇合后排序、Child 超时/失败/取消、部分结果、发送求职邮件、低置信度、Tool 关闭、计划循环、引用缺失和预算耗尽。

不要实现第二套 Runtime、Plan、Context、Gate 或 Delegation。不要实现 Checkpoint；只在设计说明中保留概念与未来扩展边界。输出需求后停止，等待我确认。
---END---
