# 求职 Agent 执行编排实现审计

## 1. 审计结论

- 审计日期：2026-08-14
- 对应任务：`job-application-orchestration-task.md` Task1
- 结论：现有仓库已具备执行编排所需的大部分底座，应在现有自研 Runtime 上增量实现，不迁移 LangChain、LangGraph 或 OpenAI Agents SDK。
- 权威边界：保留唯一 `AgentRuntime + RunContext + SQLiteRunStore + PreToolCallGate/UnifiedToolExecutor + Delegation + Trust Eval/Trace`。
- 当前缺口：缺少通用 Execution State/Route/Plan DAG/Join Policy/Runtime Verifier/Bounded Recovery/Model Decision 的统一 Schema 与 Controller；现有能力主要服务聊天、求职调研和 Delegation 专用路径。
- 实施约束：不创建第二 Runtime、Context、Plan、Budget、Gate、Delegation、Eval、Trace 或 Orchestration 数据库。

仓库在审计前已有大量未提交的第 9/10 阶段实现和配置变更。它们均视为用户现有工作，不回退、不覆盖；后续修改必须以增量 patch 完成，并在每个任务后检查差异范围。

## 2. 真实组件映射

| 能力 | 当前权威实现 | 当前契约/测试 | 编排复用方式 | 禁止事项 |
|---|---|---|---|---|
| Agent Runtime / Tool Loop | `backend/src/starter_agent/agent/runtime.py::AgentRuntime` | tool-free turn、Tool Loop、取消、重复调用、Token/Tool/时间限制 | 由 Executor Adapter 使用 `RunSpec + RunContext` 调用 | 不新增框架 Agent Loop |
| 应用入口 | `backend/src/starter_agent/application.py::ApplicationService`、`backend/src/starter_agent/interfaces/api.py` | Chat、求职路由、后台 Worker 生命周期、API 集成测试 | 增加 Orchestration Facade，保持旧入口可由开关回退 | 不在 API 中复制业务状态机 |
| 固定 Workflow | `backend/src/starter_agent/skills/job_research.py` 及现有确定性应用服务 | 求职调研、RAG、邮件等分层测试 | 作为 `workflow` execution adapter 调用 | 不将 Workflow 改造成第二 Runtime |
| Tool/MCP/RAG | `UnifiedToolRegistry`、MCP Manager/Adapter、Knowledge Application Service | Tool 暴露快照、MCP 生命周期、RAG/Evidence 测试 | Router/Validator 只读取能力快照；执行仍走唯一 Tool Executor | Router/Planner 不直接调用 Tool |
| Plan/Todo | `RunContext.todo_plan`、领域 query plan | Context 隔离与上下文测试 | 新通用 Plan 是 `RunContext` 的版本化编排分区，Todo 只投影操作进度 | 不建立平行 Todo/Plan 真相 |
| Context | `agent/context.py::ContextBuilder`、`delegation/context.py::RunContext` | Summary/Trim、Memory、Token、Child Context 隔离 | 扩展编排控制字段；Child 继续使用最小 Context Builder | 不把完整 Child 对话回填 Parent |
| Budget | Runtime 限制、`delegation/models.py::BudgetLimits`、`delegation/budget.py` 与 Store allocation ledger | 五维 Parent/Child 预留、结算、超限测试 | 在同一模型/账本增加 `steps` 和 Snapshot Facade | 不创建第二预算账本 |
| Delegation | `DelegationService`、`Coordinator`、`Dispatcher`、`WorkerPool`、`ChildRuntimeExecutor` | Parent/Child、租约、取消、重试、隔离、Result Envelope | Task Manager/Delegation Adapter 复用，作为复杂 Plan 的一种执行路径 | 简单任务不得进入 Multi-Agent |
| 状态持久化 | `delegation/store.py::SQLiteRunStore`、`infrastructure/session_store.py` | CAS version、Run Event、Artifact、Merge、Outbox、恢复测试 | 活动 State 在 RunContext；等待/后台快照投影到现有 Parent payload/表 | 不新增 Orchestration DB，不以 SSE 为真相 |
| Gate / Approval | `capabilities/gate.py::PreToolCallGate`、`UnifiedToolExecutor`、Confirmation Broker、Email Approval | no-bypass、confirmation barrier、邮件 exactly-once | Gate/Approval 作为条件 Edge，Pending Action 绑定现有 approval identity/hash | 不创建第二审批流程，不允许 Judge 放宽 Gate |
| Result 校验/修复 | `delegation/results.py::ResultValidator`、`ResultAcceptanceService`、确定性 Merger、`StructuredResultRepair.repair_once` | Schema、来源、合并、一次修复测试 | 扩展为通用 Runtime Verifier 插件与 Bounded Recovery | 不无限 Reflection，不全量重写 |
| Model Provider | `providers/registry.py::ProviderRegistry`、Settings 中的 Provider/Model 配置 | allowlist、Provider error、结构化输出测试 | Model Router 只从已配置 Registry 构造候选和 fallback | 不硬编码模型名、价格或秘密 |
| Eval / Release Gate | `trust/runner.py`、`trust/rules.py`、`trust/release_gate.py`、Fixture baseline | 固定 Fixture、Judge、Release/Safety Gate 测试 | 保持离线版本比较与发布判断；新增编排 Fixture | 不让离线 Eval 驱动当前 Run |
| Trace | `trust/trace.py::TrustTraceRecorder`、Delegation Event Bridge、Capability Audit Bridge | 脱敏、关联、Delegation Trace 测试 | 新增 Orchestration Trace Bridge 和稳定关联 ID | Trace 不反写业务状态，不记录完整 Child Context |
| API / SSE | `interfaces/runs_api.py`、`interfaces/api.py` | Run tree、事件游标、取消/恢复、API 测试 | 扩展现有 runs API/ViewModel | 不创建静态生产状态端点 |
| 前端运行详情 | `frontend/web/index.html` 中现有 Delegation 卡片、Run Detail、SSE 重连 | UI contract 与 API contract 测试 | 增加编排折叠区，继续显示完整聊天 | 不用 mock 代替真实 Run Store 状态 |

## 3. 新组件到现有边界的落点

| 新组件 | 形式 | 调用/写入边界 |
|---|---|---|
| Orchestration Controller | 新增轻量控制器 | 驱动显式 Node/条件 Edge；读取/patch `RunContext`，持久等待态到 `SQLiteRunStore` |
| Execution Router | 新增纯决策策略 | 读取输入、能力、风险和预算快照；只返回 Route Decision，不调用 Tool |
| Model Router | 新增纯决策策略 | 读取 Provider Registry/Settings；返回 Model Decision，不自行建立 Provider |
| Planner | 新增结构化策略 | 仅 `plan_delegation` 调用；输出 DAG，不执行 Step |
| Plan Validator | 新增确定性校验器 | 复用 Tool/Specialist Registry、Gate policy view、Budget Facade；执行前 fail closed |
| Task Manager | 现有 Delegation 的 Facade | 复用 Service/Dispatcher/Worker/Store/lease/event，不进行模型推理或模型轮询 |
| Executor | Adapter 集合 | Direct/Workflow/AgentRuntime/Delegation Adapter 之一；所有 Tool 仍经过 Gate |
| Runtime Verifier | 插件式确定性验证器 | 复用 ResultValidator/Merger；Judge 仅补语义 Rubric |
| Bounded Recovery | `repair_once` 的通用适配 | 只接收 Verify failure IDs 和相关片段，最多 1–2 次 |
| Budget Manager | 现有 ledger 的 Facade | 增加 steps、Snapshot、fan-out 预检和幂等记账 |
| Trace Bridge | 现有 Trust Trace 投影适配 | Run Event 是业务事件，Trace 只做脱敏观察 |

## 4. 当前状态与需求差距

1. `RunStatus` 当前使用 `created/queued/running/waiting_children/waiting_for_user/cancelling/succeeded/partial/failed/timed_out/budget_exhausted/cancelled`；需求中的 Background Task 公共状态需要建立显式兼容映射，并新增 `interrupted` 语义，不能直接破坏既有 Delegation 状态。
2. `BudgetLimits` 当前有 tokens、cost_microunits、wall_clock_ms、model_calls、tool_calls；缺少 steps。扩展必须同步 Pydantic 模型、SQLite allocation 行、聚合/结算和 UI 格式化。
3. `RunContext` 已保存 working memory、Todo、summary/trim、Artifact、预算、取消和 trace context；缺少通用 orchestration state。应增加版本化嵌套对象，避免继续堆放无约束 dict。
4. 现有 `interfaces/api.py::_classify_chat_request()` 是求职/RAG 专用入口分类，不等于通用 Execution Router。迁移时应保留其领域识别作为规则输入或 Workflow Adapter，不能同时保留两套相互竞争的最终路由。
5. 现有 Coordinator/Dispatcher 能处理 Parent/Child 与失败，但没有需求规定的通用 Plan DAG、并行资格解释和四类 Join Policy。
6. 现有 Result Validator 和一次 Schema repair 是良好复用点，但尚未形成跨路径 Verify Result 与 failure-targeted Recovery 契约。
7. 现有运行详情可展示 Delegation Parent/Child、预算和 Merge，但还没有 Route/Plan/Join/Verify/Recovery/Model Decision 的动态投影。
8. 当前求职 Delegation 路由已由持久 Release Decision fail closed；通用编排应使用独立功能开关逐级启用，不得隐式放开已有发布门。

## 5. Checkpoint 与 Interrupt 边界

仓库已有 `RunContext.to_checkpoint()/from_checkpoint()`、Coordinator checkpoint、Child checkpoint 和 handoff checkpoint 等命名。这些是第 10 阶段为暂停、审批、委派批次幂等和 Worker 续接保存的受控快照，属于必须保留的现有能力。

本迭代禁止新增的是：

- 通用编排图的步骤级 Checkpoint Store；
- 任意 Node 的跨进程/跨版本透明恢复；
- LangGraph Runtime、LangGraph Checkpointer 或基于 Interrupt 的第二人工确认系统；
- 将 Summary 或长期 Memory 当作可恢复执行快照。

进程中断时，无法由现有任务级 lease/幂等契约安全处理的任务应明确标记 `interrupted` 或 `failed`。只有未来出现需要跨重启恢复长图、精确重放节点且当前 Run Store/Task Event 无法满足的实际信号时，才重新评估通用 Checkpoint/Interrupt。

## 6. 持久化与兼容策略

1. `SQLiteRunStore` 继续作为后台 Run/Task/Event/Artifact/Merge/Outbox 的业务真相；Session Store 继续负责聊天、Context Summary 和 Memory。
2. 优先采用现有表的版本化 payload/JSON 投影；必须新增查询索引或强约束字段时，使用现有 SQLAlchemy `create_all` 加小型幂等 additive migration 模式。
3. 新枚举的存储采用向后兼容读取映射；旧客户端仍能读取既有 Parent/Child 状态，新客户端从 ViewModel 获取 Background Task 公共状态。
4. 所有写入继续使用 existing version/CAS 和幂等键；事件、预算、Join、Recovery 重放不能重复产生副作用。
5. 不删除、不重命名既有表/字段，不在本阶段回收旧求职路径；回滚通过功能开关停止新编排入口，已创建任务由现有 Task Manager 收尾或标记中断。

## 7. 功能开关与回滚入口

建议按以下顺序增加配置化开关，默认关闭，且开关只控制路由可达性，不绕过安全组件：

1. `orchestration.enabled`：通用 Router/State Controller 总开关。
2. `orchestration.plan_enabled`：Planner/Validator 路径。
3. `orchestration.background_enabled`：Background Task API 与调度。
4. `orchestration.delegation_enabled`：Plan 中 Child execution；同时必须满足现有 Delegation Release Decision。
5. `orchestration.judge_enabled`：可选语义 Judge；关闭时确定性 Verifier 仍完整运行。

回滚时关闭对应入口，不回滚已批准或已执行的外部动作，不删除持久事件；旧 Chat/Workflow/Tool Loop 继续走原入口。

## 8. 基线测试证据

执行命令：

```powershell
.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q `
  tests/unit/test_runtime_tool_free_turn.py `
  tests/unit/test_context_tokens.py `
  tests/unit/test_long_term_memory.py `
  tests/unit/test_pre_tool_call_gate.py `
  tests/integration/test_gate_no_bypass.py `
  tests/unit/test_delegation_budget.py `
  tests/unit/test_run_context_isolation.py `
  tests/unit/test_delegation_store.py `
  tests/integration/test_delegation_runtime.py `
  tests/unit/test_trust_trace.py `
  tests/unit/test_trust_runner.py `
  tests/integration/test_api.py
```

结果：116 collected，116 passed；存在 1 条既有 Starlette/httpx 兼容性弃用 warning，无测试失败。

沙箱内首次运行因 Windows 系统临时目录 ACL 导致 pytest fixture setup 失败；在获批的非沙箱测试进程中使用同一工作树和同一命令后全部通过。该问题属于测试执行环境权限，不属于产品失败。

覆盖范围：

- Direct/tool-free Runtime 与 API；
- Context Token、Summary/Memory；
- Pre-Tool-Call Gate 与 MCP no-bypass；
- Parent/Child Budget、RunContext 隔离、SQLite Run Store、Delegation Runtime；
- Trust Runner 与 Trace。

真实外部 Search/Browser Smoke 不在 Task1 执行，按计划留到 Task20，并与固定基线分开报告。

## 9. Task2 输入与受影响范围

Task2 应先新增独立、无副作用的 orchestration Schema/transition 模块及单元测试，再接入 `RunContext`。预计后续受影响范围：

- `backend/src/starter_agent/orchestration/`：新增 Schema、Controller、策略和 Adapter；
- `backend/src/starter_agent/delegation/context.py`：增加版本化 orchestration state 所有权；
- `backend/src/starter_agent/delegation/models.py`、`budget.py`、`store.py`：预算和持久化兼容扩展；
- `backend/src/starter_agent/application.py`、`bootstrap.py`、`interfaces/api.py`、`interfaces/runs_api.py`：装配和 API 接入；
- `backend/src/starter_agent/trust/`：Trace/Eval 投影；
- `frontend/web/index.html`：真实运行详情；
- `tests/unit/orchestration/`、`tests/integration/`、`tests/e2e/`、`evals/orchestration/`：分层验证。

任何实现如果需要改变上述权威边界、引入框架 Runtime 或实现通用 Checkpoint，必须停止并重新评审设计，而不能作为普通 Task 内变更继续推进。
