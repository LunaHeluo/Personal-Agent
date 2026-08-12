# 求职调研有边界任务委派实施任务计划

> 仅在用户明确确认本计划后按 Task 编号顺序实施。实施时优先使用测试驱动开发；每完成一个 Task，单独报告变更文件、测试命令与结果、Trace/截图/报告证据及剩余风险。

## 目标与架构约束

**目标：** 在现有求职 Agent 中实现持久化、可取消、可追踪、最小权限的 Parent/Child Run 委派能力，并仅在固定评测证明收益成立后默认启用 Multi-Agent 求职调研路径。

**架构：** 使用 SQLite Run Store、数据库租约 Dispatcher/Worker Pool、模型辅助且可挂起的 Coordinator。Parent 与 Child 复用同一 `AgentRuntime / AgentLoop`，每次执行创建独立 `RunContext`；所有 Tool Call 继续经过现有 Pre-Tool-Call Gate。

**技术栈：** Python 3.11+、FastAPI、Pydantic、SQLAlchemy/SQLite、asyncio、现有 Unified Tool Registry、Playwright MCP、RAG、Trust/Eval、pytest。

全局约束：

1. 不创建第二套 Agent Loop、权限 Gate、Trace Store、预算系统或 Eval Runner。
2. 不覆盖工作区中用户已有未提交修改；每个 Task 开始前检查相关文件 diff。
3. 新功能先写失败测试，再写最小实现，并运行目标测试和受影响回归。
4. Coordinator 不能获得 Search、Browser、raw RAG 的完整 Tool Schema。
5. Child 不得获得 `delegate_task`，首版递归委派深度固定为 0。
6. 原始 HTML、Snapshot、Child 对话和 Tool 原始结果不得进入主 Chat Context。
7. Legacy 回滚开关默认关闭，仅 operator 可启用；有效期为 14 天或连续两个发布窗口，以先到者为准。
8. 固定 Fixture 与真实 Search/Browser Smoke 分开运行和报告。
9. 只有固定基线满足质量、安全、成本和延迟门槛后，Multi-Agent 路由才可默认启用。

## 计划文件映射

计划新增或重点修改的边界如下；最终以 Task1 审计结果和实施时最新工作区为准：

- `src/starter_agent/delegation/models.py`：Task Contract、Run、Envelope、预算和状态转换模型。
- `src/starter_agent/delegation/store.py`：SQLite Run Store、租约、乐观锁、Outbox 和幂等持久化。
- `src/starter_agent/delegation/budget.py`：五维预算预留、结算、价格版本和硬限制。
- `src/starter_agent/delegation/registry.py`：Specialist 定义、快照、启停、reload 和能力匹配。
- `src/starter_agent/delegation/context.py`：Child Context Assembly、引用加载和 `RunContext` 创建。
- `src/starter_agent/delegation/tool_view.py`：场景级 Effective Tool View 与 Schema 过滤。
- `src/starter_agent/delegation/service.py`：Parent/Child 创建、`delegate_task` 服务和取消/恢复。
- `src/starter_agent/delegation/dispatcher.py`：领取、租约、心跳、背压和孤儿回收。
- `src/starter_agent/delegation/worker.py`：Worker Pool 和共享 Runtime 执行适配。
- `src/starter_agent/delegation/coordinator.py`：Parent 挂起、唤醒、结果收集和阶段驱动。
- `src/starter_agent/delegation/results.py`：Result Validator、确定性 Merger 和 Merge Report。
- `src/starter_agent/delegation/backfill.py`：Chat 幂等回填 Outbox consumer。
- `src/starter_agent/delegation/specialists/job_web_researcher.py`：网页推进循环。
- `src/starter_agent/delegation/specialists/profile_evidence_analyst.py`：授权简历证据分析。
- `config/specialists/*.yaml`、`config/prompts/specialists/*.md`：两个 Specialist 的版本化定义和 System Prompt。
- `src/starter_agent/agent/runtime.py`、`agent/context.py`、`tools/base.py`：共享 Runtime 与 Run-scoped Context/ToolContext。
- `src/starter_agent/interfaces/runs_api.py`、`interfaces/api.py`、`application.py`、`bootstrap.py`：Run API、路由和服务组装。
- `src/starter_agent/trust/models.py`、`trust/store.py`、`trust/trace.py`、`interfaces/trust_api.py`：父子 Trace 和查询。
- `src/web/index.html`：任务卡、父子运行详情、取消与恢复。
- `tests/unit/`、`tests/integration/`、`tests/e2e/`、`evals/job-research/`：分层回归、Fixture 和 Smoke。

---

## Task1：冻结真实仓库审计与迁移清单

### 任务目标

建立实施前可复核的真实调用链、输出契约、共享状态和测试依赖清单，避免在未知入口上新增双轨路径。

### 子任务

1. 审计 `AgentRuntime`、Context/Summary、现有 Todo/plan 能力、Runtime/Context 预算和 Tool Result Guard。
2. 审计 Unified Tool Registry、MCP Manager、RAG、Pre-Tool-Call Gate、Confirmation、Execution Permit 和 ToolContext。
3. 审计 Trust/Eval/Trace、日志脱敏、Artifact、Session Store、API 与前端状态来源。
4. 枚举所有直接抓取或解析 JD 的入口：Router 分支、`_chat_with_public_job_search_fallback()`、Application Service、`JobResearchOrchestrator`、`PlaywrightJobPageReader`、`JobPageFallback`、`SafeWebFetcher`、Extractor、Bootstrap、Trust Smoke 和 Fixture Runtime。
5. 为每个入口记录调用方、输入、输出契约、Tool/网络副作用、持久化副作用和测试调用方。
6. 记录当前工作区未提交修改与本功能重叠的文件，不改写用户已有内容。
7. 生成 `docs/job-application-delegation-implementation-audit.md`，并用测试或 `rg` 证据固定生产调用链。

### 依赖关系

- 前置依赖：已确认的需求和设计文档。
- 后续依赖：Task2 至 Task22 均依赖本 Task 的入口与文件清单。

### 验收标准

- 审计文档覆盖 Runtime、任务/Todo、预算、Trace、Gate、Eval、API、前端和全部 JD 网页路径。
- 每个旧入口都有调用方、输出契约、迁移结论和测试位置。
- `rg` 结果与审计清单一一对应；未发现未归类的生产网页抓取入口。
- 运行 `uv run pytest tests/unit/test_job_research_audit.py -q` 通过，并保存基线输出。

### 预估复杂度

中等：跨模块只读审计，风险主要是遗漏隐式调用方。

## Task2：实现委派领域契约与状态机

### 任务目标

定义 Task Contract、Parent/Child Run、Budget Allocation、Result Envelope、Merge Report 和合法状态迁移，形成后续模块的稳定类型边界。

### 子任务

1. 新增 `delegation/models.py`，定义不可变 Pydantic 模型、稳定 ID、版本字段和时间字段。
2. 定义 `ParentRun`、`ChildTask`、`ChildRun`、`TaskContract`、`BudgetAllocation`、`ResultEnvelope`、`MergeReport`、`RunSpec` 和 `RunOutcome`。
3. 定义非终态、`waiting_children`、`waiting_for_user`、`cancelling` 和全部终态。
4. 实现合法转换表、终态不可逆、expected version 和迟到结果拒绝规则。
5. 为 Contract/Envelope 增加版本、canonical hash、幂等键和 JSON Schema 导出。
6. 编写 `tests/unit/test_delegation_models.py`，覆盖合法/非法转换、字段上限、终态保护和 Hash 稳定性。

### 依赖关系

- 前置依赖：Task1。
- 后续依赖：Task3 至 Task22。

### 验收标准

- 所有设计字段可由类型表达，额外字段默认拒绝。
- 非法状态转换和终态覆盖返回稳定错误码。
- 相同 Contract 产生相同 Hash；不同 payload 使用同一幂等键可被识别为冲突。
- `uv run pytest tests/unit/test_delegation_models.py -q` 通过。

### 预估复杂度

中等：模型数量多，但无外部副作用。

## Task3：实现 SQLite Run Store、预算账本与 Outbox

### 任务目标

为业务 Parent/Child Run 提供持久化、乐观锁、五维预算、租约和幂等 Outbox，不混用 Trust Eval Run 表。

### 子任务

1. 新增 `delegation/store.py`，建立 Parent、Child Task、Child Run、Budget Allocation、Run Event、Artifact Link、Merge Report 和 Outbox 表。
2. 实现 `create_parent()`、`create_child_task_and_run()`、`transition()`、`get_run_tree()`、`append_event()` 和分页查询。
3. 使用短事务和 expected version 实现并发更新；SQLite 模式下配置 WAL/忙等待并保留现有数据库初始化方式。
4. 新增 `delegation/budget.py`，实现 Token、费用、墙钟、模型调用和工具调用的 requested/reserved/consumed/released。
5. 实现 Parent 剩余量原子预留、Child 结算、未用额度释放和 unknown 费用拒绝。
6. 实现 Outbox 唯一键、相同 payload 幂等和不同 payload 冲突。
7. 编写 `tests/unit/test_delegation_store.py` 与 `test_delegation_budget.py`，覆盖并发竞争、回滚和持久化恢复。

### 依赖关系

- 前置依赖：Task2。
- 后续依赖：Task7、Task8、Task9、Task14、Task16、Task17。

### 验收标准

- 两个并发事务不能同时消耗同一 Parent 预算或领取同一版本。
- 五维任一预算不足时 Child 创建原子失败，不留下孤儿 Task 或预留。
- 相同 Outbox 事件只保存一次；payload 冲突返回明确错误。
- 重建 Store 后可以恢复 Run 树、预算和事件。
- `uv run pytest tests/unit/test_delegation_store.py tests/unit/test_delegation_budget.py -q` 通过。

### 预估复杂度

高：涉及数据库一致性、并发和预算账本。

## Task4：实现 Specialist Registry 与两个版本化定义

### 任务目标

建立可校验、可刷新、可启停、可快照的 Specialist Registry，并定义两个求职 Specialist 的 Prompt、Schema 和最小权限。

### 子任务

1. 新增 `delegation/registry.py`，解析版本控制的 YAML/JSON Specialist 定义和 Prompt 引用。
2. 定义稳定 ID、版本、Prompt Hash、能力标签、输入/输出 Schema、允许 Tool、默认/最大预算、并发、步骤、超时、重试和 failure behavior。
3. 新增 `config/specialists/job_web_researcher.yaml`、`profile_evidence_analyst.yaml`。
4. 新增两个独立 System Prompt，明确不可信网页、简历证据不可补写和禁止递归委派。
5. 实现全量校验后原子 reload、旧快照保留、数据库停用覆盖和运行时不可变快照。
6. 实现能力匹配及 `specialist_not_found`、`specialist_disabled`、`specialist_dependency_unavailable` 等错误。
7. 编写 `tests/unit/test_specialist_registry.py`，覆盖版本、reload、停用、依赖和 Prompt/Schema Hash。

### 依赖关系

- 前置依赖：Task1、Task2。
- 后续依赖：Task6、Task7、Task10、Task13。

### 验收标准

- Coordinator 只能解析已注册、启用且依赖健康的 Specialist。
- reload 失败不污染当前快照；运行中的 Child 使用创建时版本。
- Web Specialist 定义中没有 RAG/委派；Profile Specialist 定义中没有 Search/Browser/委派。
- `uv run pytest tests/unit/test_specialist_registry.py -q` 通过。

### 预估复杂度

中高：涉及配置、缓存一致性和安全定义。

## Task5：整理共享 AgentRuntime 与独立 RunContext

### 任务目标

让 Parent 和 Child 使用同一个 Runtime/Loop，并把所有单次运行可变状态移动到新建的 `RunContext`。

### 子任务

1. 在 `agent/runtime.py` 定义或接入统一 `run(spec, context)` 入口，保留旧 Chat 调用的兼容 Adapter。
2. 在 `delegation/context.py` 定义 `RunContext`：messages、working memory、todo/plan、effective tool view、预算、取消、summary/trim 和 output buffer。
3. 将当前 `AgentRuntime` 实例级 Run 专属 budget、knowledge scope、knowledge base 移入 Spec/Context。
4. 将模型/工具调用计数、重复调用检测、usage 和停止条件从隐式局部状态整理为可检查点化的 Run 状态。
5. 扩展 `ToolContext` 携带 Parent/Task/Child/Trace ID 和授权 scope，避免从 Runtime 单例读取。
6. 保持 Model Client、Tool 实现、Gate/Executor、Registry、Trace/Artifact/RAG 为共享基础设施。
7. 编写 `tests/unit/test_run_context_isolation.py` 与 `tests/integration/test_delegation_runtime.py`。

### 依赖关系

- 前置依赖：Task2、Task3。
- 后续依赖：Task6 至 Task16。

### 验收标准

- Parent 与两个 Child 的 `RunContext` 对象身份均不同。
- 并发修改 messages、memory、todo/plan、Tool View、预算、取消、summary/trim 和 output buffer 不发生跨 Run 可见。
- Parent/Child 都进入同一 Runtime Loop，没有 `SubagentLoop` 或复制 Agent 对象。
- 普通 Chat 与既有 Runtime 测试继续通过。
- `uv run pytest tests/unit/test_run_context_isolation.py tests/integration/test_delegation_runtime.py tests/unit/test_runtime_revision.py -q` 通过。

### 预估复杂度

很高：核心 Runtime 重构，回归面最大。

## Task6：实现 Child Context Builder、引用加载与场景 Tool View

### 任务目标

按字段所有权组装最小上下文，并计算 Tool、deadline 和预算的安全交集，防止主 Agent 或 Child 获得无关 Schema 和数据。

### 子任务

1. 在 `delegation/context.py` 实现 Coordinator、Registry、Runtime、Context Builder 四方字段合并和固定优先级。
2. 实现 `artifact_id`、`knowledge_scope`、`document_id`、`chunk_id` 和 `source_url/content_hash` 引用加载。
3. 对引用执行 principal、run、knowledge scope、Artifact 类型和保留期权限校验。
4. 新增 `delegation/tool_view.py`，从共享 Unified Tool Registry 生成只读过滤视图。
5. 计算 Registry 允许集 ∩ Contract 请求集 ∩ 场景集 ∩ Policy ∩ 依赖健康的 Tool 安全集合。
6. 计算 requested、Registry、Policy 和 Parent 剩余量的 deadline/五维预算交集。
7. 确保主 Agent 无 Search/Browser/raw RAG Schema；Web Child 仅 Search/Browser；Profile Child 仅授权 RAG；所有 Child 无 `delegate_task`。
8. 编写 `tests/unit/test_child_context_builder.py`、`test_effective_tool_view.py` 和 Tool exposure 集成测试。

### 依赖关系

- 前置依赖：Task3、Task4、Task5。
- 后续依赖：Task7、Task9、Task10、Task13、Task16。

### 验收标准

- 冲突字段不能扩大 Prompt、Tool、scope、deadline 或预算。
- Context 不包含完整主 Chat、全部记忆、其他 Child 中间结果或无关 Tool Schema。
- Model request snapshot 证明三个角色只看到各自 Tool View。
- 未授权 Artifact/Chunk 请求被拒绝并产生 Policy/Trace 证据。
- 目标单元与 `tests/integration/test_model_request_tool_exposure.py` 通过。

### 预估复杂度

高：权限交集、上下文治理和已有 Registry 兼容。

## Task7：实现 Coordinator 专用 `delegate_task`

### 任务目标

提供可表现为 Tool Call、但实际创建持久化真实 Child Run 的内部委派入口。

### 子任务

1. 在 `delegation/service.py` 实现 `DelegationService.delegate_task(parent_run_id, specialist_id, task_contract)`。
2. 新增内部 Tool Adapter，输入只允许 Specialist ID 和 Coordinator 所有字段。
3. 从 Registry 注入 Prompt、Schema、版本和最大限制，从 Runtime 注入 ID、Trace、Policy、最终预算/deadline 和幂等键。
4. 在单事务中创建 Child Task、Child Run、预算预留、状态事件和 Outbox 唤醒。
5. Tool 只返回 `DelegationReceipt`，不得直接执行 Specialist 或返回静态业务结果。
6. Gate 同时校验 caller 必须是 Coordinator，Child 请求该 Tool 时在 Tool View 和 Gate 两层拒绝。
7. 编写 `tests/unit/test_delegate_task.py` 和 `tests/integration/test_delegate_task_gate.py`。

### 依赖关系

- 前置依赖：Task3、Task4、Task6。
- 后续依赖：Task8、Task9、Task15。

### 验收标准

- 成功调用后数据库存在真实 Child Task/Run、独立预算和 Registry 快照。
- 返回值只含 receipt/ID/初始运行信息，不含伪造 Specialist 结果。
- 重复幂等调用不创建第二个逻辑 Task；不同 payload 冲突。
- Child 和普通 Agent 均无法调用 `delegate_task`。
- 目标测试及 `tests/integration/test_gate_no_bypass.py` 通过。

### 预估复杂度

高：跨 Registry、Store、Budget、Gate 的事务边界。

## Task8：实现有界 Dispatcher、数据库租约 Worker Pool 与取消传播

### 任务目标

实现可恢复、不空等、具有背压和孤儿清理的后台执行层。

### 子任务

1. 新增 `delegation/dispatcher.py`，实现 queued Run 原子领取、lease owner/token、heartbeat 和 expected version。
2. 新增 `delegation/worker.py`，实现全局与 Specialist 并发 Semaphore、任务循环和优雅停机。
3. 实现 Parent `waiting_children/waiting_for_user` 释放 Worker，Child 终态按条件唤醒 Parent。
4. 实现队列高水位、硬容量、`run_queue_overloaded` 和公平领取顺序。
5. 实现 Model 前、Gate 前、Tool 后和循环步边界的持久化取消版本检查。
6. 实现 deadline、Tool/Model timeout、有限重试、指数退避和 attempt 记录。
7. 实现租约过期 Reaper、孤儿 Run 重排或终止和迟到 Worker 提交拒绝。
8. 编写 `tests/unit/test_dispatcher.py`、`test_worker_pool.py` 与 `tests/integration/test_worker_recovery.py`。

### 依赖关系

- 前置依赖：Task3、Task5、Task7。
- 后续依赖：Task9 至 Task22。

### 验收标准

- 并发 Worker 只能有一个获得同一 Run 租约。
- Parent 等待 Child 时不占 Worker；重启后过期租约可恢复。
- 取消后不创建/领取新 Child，运行中 Child 在安全边界停止。
- 超时、重试、背压和孤儿清理均有持久化事件与稳定错误码。
- 目标单元/集成测试通过，无长事务包裹模型或网络调用。

### 预估复杂度

很高：异步并发、数据库租约、取消和恢复。

## Task9：实现可挂起 Coordinator 与 Parent 阶段驱动

### 任务目标

让模型辅助 Coordinator 使用同一 Runtime 创建任务、挂起、被 Child 唤醒，并只用受控结果完成校验和合并。

### 子任务

1. 新增 `delegation/coordinator.py`，定义 planning、waiting_children、validating、merging 和 terminal phase。
2. Coordinator 首轮 Tool View 仅含 `delegate_task`、受控确认和结果操作。
3. 处理同一模型响应中的多个委派后，在下一次模型调用前返回 `RunOutcome.suspended(waiting_children)`。
4. Child 完成后按依赖条件唤醒 Parent，并通过 Context Builder 注入 Envelope/Trace 引用。
5. 支持 Web 结果到达后再创建 Profile Task 的两阶段依赖，不复制 Web Child 中间 Context。
6. 限制 Coordinator 不得自行执行 Web/RAG 任务或补写失败字段。
7. 编写 `tests/unit/test_coordinator.py` 和 `tests/integration/test_coordinator_suspend_resume.py`。

### 依赖关系

- 前置依赖：Task5、Task6、Task7、Task8。
- 后续依赖：Task13、Task14、Task15、Task17。

### 验收标准

- 委派后 Parent 持久化并释放 Worker；不存在内存调用栈等待 Child。
- 唤醒时 Parent 只接收合法 Envelope 和 Trace 引用。
- Coordinator 模型请求不含 Search、Browser 或 raw RAG Schema。
- Child 失败时 Coordinator 只按 failure behavior 产生 partial/failed，不补事实。
- 目标单元/集成测试通过。

### 预估复杂度

很高：Runtime 挂起/恢复、依赖驱动和模型行为边界。

## Task10：实现 `job_web_researcher` 网页推进循环

### 任务目标

实现 Search 后可跨页面观察、导航、提取、校验、翻页和停止的真实 Child Agent Loop。

### 子任务

1. 新增 `delegation/specialists/job_web_researcher.py`，实现 Candidates → Open → Wait → Locate → Expand/Detail → Extract → Completeness → Next/Stop。
2. 复用 `search_jobs_serpapi`、Playwright MCP、候选排序、公司归属和 JD 校验的无状态逻辑。
3. 支持页面观察后选择展开、进入详情页、返回候选或下一页，不让主 Agent参与导航。
4. 记录 page_count、step_count、候选 attempt、requested/final URL 和停止原因。
5. 强制 `max_pages=10`、`max_steps=30`、每页默认 35 秒，并应用 Contract/Policy/Parent 更小上限。
6. 实现规范化 URL、最终 URL、内容 Hash 和岗位字段签名去重。
7. 输出标准化 jobs、missing、errors、visited 和 Artifact refs。
8. 编写 `tests/unit/test_job_web_researcher.py` 和脚本化多页面 Fixture。

### 依赖关系

- 前置依赖：Task4、Task5、Task6、Task8。
- 后续依赖：Task11、Task12、Task15、Task19、Task21。

### 验收标准

- 测试证明 Child 进行多轮 Model/Tool/Observation，而非一次普通函数包装。
- 达到目标、页面/步骤、预算、deadline 或取消条件时确定停止。
- 重复页面不重复计入 jobs 或消费后续提取步骤。
- 主 Agent Trace 中没有 Browser 导航过程。
- `uv run pytest tests/unit/test_job_web_researcher.py -q` 通过。

### 预估复杂度

很高：动态导航状态机、Tool Loop 和确定性停止。

## Task11：实现网页异常分类与人工接管

### 任务目标

为可恢复网页错误提供有限重试/降级，为登录、验证码、权限和拒绝访问提供安全暂停或 partial。

### 子任务

1. 定义加载失败、连接错误、404/410、重定向、渲染超时、选择器失效、空正文、重复页的分类和稳定错误码。
2. 实现每类最多 2 次有限重试、等待档位、换候选和不可恢复连续失败上限。
3. 重定向每跳重新经过现有网络范围 Gate，保留 requested/final URL。
4. 登录、验证码、权限、robots 和站点拒绝进入 `waiting_for_user` 或按契约返回 partial；禁止绕过。
5. 实现 Run API resume 所需的安全检查点和人工处理超时。
6. 编写 `tests/unit/test_job_web_error_policy.py` 和 `tests/integration/test_job_web_handoff.py`。

### 依赖关系

- 前置依赖：Task8、Task10。
- 后续依赖：Task17、Task18、Task19、Task21。

### 验收标准

- 可恢复错误只按上限重试，不形成无限导航或重复计费。
- 404 不重试原 URL；重复页不再次抓取。
- 登录/验证码/权限/拒绝访问没有绕过 Tool Call，且产生明确阻塞/partial 证据。
- resume 只能恢复对应 Parent/Child 和安全检查点。
- 目标单元/集成测试通过。

### 预估复杂度

高：错误语义、状态机和人工交互边界。

## Task12：实现网页 Context 治理与受限 Artifact

### 任务目标

确保原始网页材料留在 Child Trace/Artifact，单页按预算裁剪/总结，Parent 只吸收标准化结果。

### 子任务

1. 扩展现有 Tool Artifact 写入，关联 Parent/Task/Child/Tool/Policy/Approval 和访问级别。
2. 实现导航、Cookie banner、重复 DOM、脚本样式和无关区域去除。
3. 按目标 JD 字段抽取单页内容，并复用 Tool Result Guard 执行 Token/字符预算。
4. 对超限正文生成字段感知摘要，保留 content hash、source URL 和原 Artifact 引用。
5. 防止相同 DOM/中间页重复注入 Child messages。
6. 确保 Parent messages、Chat、普通日志和公开 Trace payload 中没有原始 HTML/Snapshot。
7. 实现 Artifact 保留期、元数据查询和授权按需查看。
8. 编写 `tests/unit/test_web_context_governance.py` 和敏感内容泄漏集成测试。

### 依赖关系

- 前置依赖：Task6、Task10、Task11。
- 后续依赖：Task14、Task16、Task18、Task19。

### 验收标准

- Parent 只接收标准化 JD、source、missing、errors、usage 和 Trace/Artifact 引用。
- 大页面被有界裁剪或总结，来源字段不丢失。
- 原始 HTML/Snapshot 只存在于受限 Artifact，并执行现有脱敏。
- 泄漏回归和 `tests/unit/test_tool_result_guard.py` 通过。

### 预估复杂度

高：内容治理、Artifact 权限和上下文预算。

## Task13：实现 `profile_evidence_analyst`

### 任务目标

实现只从授权简历知识库读取证据、不能补写经历的 Profile Child。

### 子任务

1. 新增 `delegation/specialists/profile_evidence_analyst.py`，读取标准化岗位要求和授权 knowledge scope/chunk refs。
2. 通过 Child Context Builder 加载最小岗位字段和必要简历 Chunk，不加载完整网页或主 Chat。
3. Tool View 仅开放 `retrieve_resume_evidence` 或等价授权 RAG。
4. 输出 requirement ref、match status、evidence、chunk_id、强度、missing 和 conflicts。
5. 正向匹配没有授权 chunk_id 时返回校验失败；岗位要求不得转换成用户经历。
6. 将 Web Envelope 到具体 Profile Task 的依赖交由 Coordinator/引用传递。
7. 编写 `tests/unit/test_profile_evidence_analyst.py` 和 RAG scope 集成测试。

### 依赖关系

- 前置依赖：Task4、Task6、Task9、Task10。
- 后续依赖：Task14、Task19、Task20。

### 验收标准

- Profile Child 的 Provider request 不含 Search/Browser/委派 Schema。
- 每个正向匹配有授权 `chunk_id`；无证据时明确 missing。
- 未授权 knowledge scope 被拒绝并关联 Policy/Trace。
- 现有 RAG refusal/citation/security 测试和新测试通过。

### 预估复杂度

中高：RAG 授权、证据忠实度和结构化输出。

## Task14：实现 Result Validator、确定性 Merger 与写入隔离

### 任务目标

只允许合法 Envelope 进入 Parent，确定性处理去重、缺失和冲突，并安全合并共享结果。

### 子任务

1. 新增 `delegation/results.py`，按 ID/版本、Schema、大小、权限、来源、证据、usage 和终态顺序校验。
2. 允许同一 Child 在剩余预算内最多一次结构化修复；Coordinator 不解释非法 Schema。
3. 使用 URL、content hash、岗位字段和 chunk_id 确定性去重。
4. 保留冲突来源、missing、被拒 Envelope 和理由，不静默覆盖。
5. 先计算排序特征与确定性顺序，再进行一次只读已验证事实的有限语义综合。
6. 生成持久化 Merge Report、输入/输出 Hash 和结果版本。
7. 共享岗位/投递数据先写候选区，通过 expected version、唯一键和幂等提交；并发冲突返回 `merge_conflict`。
8. 编写 `tests/unit/test_result_validator.py`、`test_result_merger.py` 和并发写入集成测试。

### 依赖关系

- 前置依赖：Task3、Task9、Task12、Task13。
- 后续依赖：Task15、Task16、Task17、Task19。

### 验收标准

- 非法、越权、迟到或预算不一致 Envelope 不进入 Parent Context。
- 同一输入和版本产生相同去重、冲突和排序结果。
- 语义综合不能修改来源事实、移除冲突或填充 missing。
- 并发共享写入不发生 last-write-wins 或重复记录。
- 目标单元/集成测试通过。

### 预估复杂度

高：证据校验、确定性合并和并发写隔离。

## Task15：迁移旧网页 Workflow 并建立唯一 Subagent 主路径

### 任务目标

将多页面/动态 JD 调研统一迁移到 `delegate_task(job_web_researcher, task_contract)`，移除生产双轨并保留有时限的兼容边界。

### 子任务

1. 根据 Task1 清单修改 Router 的 `JOB_RESEARCH` 分支，创建 Parent Run 而不是调用旧固定 Workflow。
2. 从正常 API/Application 路径移除 `_chat_with_public_job_search_fallback()` 和跨工具 `JobResearchOrchestrator` 调用。
3. 将候选排序、公司归属、JD 校验和答案格式收敛为无状态组件供 Web Child/Validator/Merger 复用。
4. 将 `PlaywrightJobPageReader`、`JobPageFallback`、`SafeWebFetcher`、Extractor 收敛为 Web Child 内部能力或明确单页 Tool。
5. 实现只读兼容 Adapter：读取新 Run/Envelope 转换旧输出，不触发第二次 Search/Browser。
6. 新增默认关闭、operator-only 的 Legacy 开关、到期时间和删除证据；正常失败不得自动回退。
7. 记录 route、legacy_path_used、Parent/Task/Child ID、Contract/Tool View Hash。
8. 更新旧单元/集成/E2E 调用方，冻结旧 Orchestrator 仅作单 Agent baseline。

### 依赖关系

- 前置依赖：Task7、Task9、Task10、Task14。
- 后续依赖：Task16 至 Task22。

### 验收标准

- 多页面/动态请求只有一条生产路径并创建真实 Web Child Run。
- 默认配置下 Router/API/前端不能调用旧 Workflow。
- Child 失败不会触发 Legacy Search/抓取；无重复计费、Artifact 或业务写入。
- 兼容输出契约测试通过，且 Adapter 的 Tool 调用数为 0。
- Legacy 开关默认 false，仅 operator 可用，并携带 14 天/两个发布窗口截止信息。

### 预估复杂度

很高：核心业务迁移、兼容和大量既有测试调整。

## Task16：贯通 Trace、预算、Gate 与 Child 留存

### 任务目标

让父子 Run、Model、Tool、Policy、Approval、预算和迁移路由在现有 Trace/日志体系中可关联且无安全旁路。

### 子任务

1. 扩展 `TraceContext` 和 `TrustTraceEvent`，加入 parent_run_id、child_task_id，并复用 child_run_id。
2. 将 eval_run_id、case_id、session/turn、model_request、tool_call、policy_decision、approval ID 贯通 Worker/Runtime/ToolContext。
3. 记录 Registry/Contract/Tool View Hash、状态转换、租约、预算预留/消费/释放、取消、Validator、Merger 和回填事件。
4. 确保 Child Tool Call 继续通过现有 Gate/Permit/Confirmation，Parent Approval 不自动传给 Child。
5. 将完整 Child messages、Tool 原始结果和网页材料留在受限 Store，只向 Trust Trace 写脱敏摘要和引用。
6. 记录 route、legacy_path_used 和唯一 Subagent 调用证据。
7. 扩展 Trust Store/API 过滤 parent_run_id、child_task_id、child_run_id。
8. 编写 `tests/unit/test_delegation_trace.py` 和 Gate/脱敏集成测试。

### 依赖关系

- 前置依赖：Task3、Task6、Task8、Task12、Task14、Task15。
- 后续依赖：Task17 至 Task22。

### 验收标准

- 可从 Parent 追到 Child、Model、Tool、Policy、Approval、Artifact 和预算事件。
- 未授权 Tool 没有 Tool Start/Invoke，且 Trace 有 Gate 拒绝证据。
- 日志和公开 Trace 不含完整简历、HTML、Cookie、验证码或隐藏推理。
- Trace 查询和现有 Trust 回归通过。

### 预估复杂度

高：跨层 ID 传播、存储兼容和脱敏。

## Task17：实现 Run API、自动回填与服务生命周期

### 任务目标

提供后台 Run 创建、查询、事件、取消、恢复和单次 Chat 回填，并在应用生命周期中可靠启动/停止 Worker。

### 子任务

1. 新增 `interfaces/runs_api.py`：Run 详情、增量事件、取消、resume 和受限 Artifact 元数据接口。
2. 扩展 `ChatResult` 可选 parent_run_id、run_status 和 task_card，保持普通 Chat 兼容。
3. 修改 `application.py`/`bootstrap.py` 组装 Run Store、Registry、Dispatcher、Worker、Coordinator、Validator 和 Merger。
4. 在 FastAPI lifespan 中启动/停止 Worker Pool、Reaper 和 Outbox consumer。
5. 新增 `delegation/backfill.py`，用 `parent_run_id + result_version + message_kind` 幂等写 Assistant 消息。
6. 取消/失败/超时/预算耗尽只更新任务卡，不回填业务结论。
7. SSE 推送创建确认和事件游标；连接断开不取消后台 Run。
8. 编写 `tests/integration/test_delegation_api.py`、`test_chat_backfill.py` 和重启恢复测试。

### 依赖关系

- 前置依赖：Task8、Task9、Task11、Task14、Task15、Task16。
- 后续依赖：Task18 至 Task22。

### 验收标准

- Chat 快速返回持久化 Parent ID，后台任务不依赖 SSE 存活。
- 刷新/重启后 Run 状态、Child 树和事件可恢复。
- 重复 Outbox、Worker 重试和前端重连只产生一条最终消息。
- 取消和 resume 使用 expected version 与幂等键。
- 普通 Chat、知识库、邮件 API 兼容回归通过。

### 预估复杂度

很高：API 兼容、生命周期、后台服务和 Outbox。

## Task18：在现有前端展示任务卡与父子运行详情

### 任务目标

在原 Chat 和 Trust Center 展示真实后端 Parent/Child 状态、预算、取消、人工阻塞和合并证据。

### 子任务

1. 在 `src/web/index.html` 增加可恢复任务卡，显示 Parent ID、phase、Child 进度、五维预算和开始时间。
2. 接入 Run 详情和增量事件 API，以 event_seq/run_version 去重；SSE 只是加速。
3. 实现取消操作、幂等反馈和 `waiting_for_user` 的安全 resume/终止入口。
4. 扩展 Trust Center 父子树，显示 Specialist/版本、attempt、失败、missing/conflicts 和 Merge Report 摘要。
5. 不显示隐藏推理、完整 Child messages、原始 HTML/Snapshot 或未脱敏日志。
6. 页面刷新、Session 切换和 API base 变化时重新从后端加载权威状态。
7. 编写/扩展 UI contract 测试和 `tests/e2e/test_delegated_job_research.py`。

### 依赖关系

- 前置依赖：Task11、Task12、Task16、Task17。
- 后续依赖：Task19、Task22。

### 验收标准

- 刷新后任务卡和父子树与后端一致，不依赖本地计时器伪造状态。
- 取消、人工接管、partial、失败和合并证据均可查看。
- 乱序/重复事件不能覆盖较新版本。
- 页面不渲染受限 Artifact 正文或隐藏推理。
- UI contract 与 E2E 目标测试通过。

### 预估复杂度

高：单文件前端状态管理、恢复和可观测性展示。

## Task19：建立固定 Fixture 与分层委派回归套件

### 任务目标

在现有 Eval Runner/pytest 中建立稳定、可重复、与真实网络分离的委派 Fixture。

### 子任务

1. 扩展 `evals/job-research/fixtures`，加入 Parent/Child、页面序列、动态渲染、分页、404、空正文、重复页、登录/验证码、权限和拒绝访问。
2. 新增双成功、一个失败、一个超时、父取消、重复回调、非法 Schema、来源冲突、权限拒绝、预算耗尽和单 Agent 更优 Case。
3. 增加旧 Workflow 不再调用和唯一 Web Subagent 路由断言。
4. 增加主/Web/Profile 工具 Schema 隔离和 Child 无委派断言。
5. 增加兼容输出、Legacy 默认关闭/operator/期限断言。
6. 增加网页步骤/页面/超时、人工接管、Artifact 泄漏和 Parent Envelope-only 断言。
7. 增加 RunContext 身份和跨 Run 状态污染断言。
8. 将 Case 结果、Trace、Metric 和 Safety Gate 写入现有 Trust Store。

### 依赖关系

- 前置依赖：Task10 至 Task18。
- 后续依赖：Task20、Task22。

### 验收标准

- 固定 Fixture 不调用真实网络或真实 Browser/Provider。
- 所有必验场景有确定性断言和稳定 Fixture Hash。
- 旧 Workflow、工具暴露、Context 污染、重复副作用和安全绕过均有负向测试。
- `agent trust fixture-baseline --run-id <stable-id>` 可产出完整报告与 Gate。

### 预估复杂度

很高：覆盖面广，需稳定模拟并发、网页与恢复语义。

## Task20：执行单 Agent 与 Multi-Agent 固定基线比较及默认启用 Gate

### 任务目标

使用相同 Fixture 和版本配置量化比较两种路径，只在收益成立时改变默认路由。

### 子任务

1. 冻结单 Agent baseline 的代码、Prompt、Skill、Tool Schema、Policy、Fixture 和模型配置版本。
2. 运行 Multi-Agent candidate，记录 Task Success、wall-clock/P95、Token、成本、来源完整性、证据忠实度和失败复杂度。
3. 扩展现有 Run Comparator/Release Gate 计算质量提升、成本倍率、延迟倍率和 Safety 回归。
4. 强制门槛：至少一项质量指标提升 10 个百分点，其余不下降；Safety 无回归；成本 ≤1.5 倍；P95 ≤2 倍。
5. Gate 未通过时保持 Multi-Agent 默认关闭并输出“单 Agent 更优/候选未达门槛”。
6. Gate 通过时才允许配置默认路由，同时保留 Legacy 开关默认关闭。
7. 保存两份 Run、比较报告、版本 Hash 和发布决策证据。

### 依赖关系

- 前置依赖：Task13、Task19。
- 后续依赖：Task21、Task22。

### 验收标准

- baseline/candidate 使用相同 Case 和可比较配置。
- 缺失 Token/费用不按 0 处理，比较报告显式标记 estimated/unknown。
- Gate 结论能自动控制默认启用配置，不依赖人工口头判断。
- 单 Agent 更优 Fixture 会阻止 Multi-Agent 默认启用。

### 预估复杂度

高：指标口径、版本可比性和发布 Gate。

## Task21：运行真实 Search/Browser Smoke

### 任务目标

验证真实 Search、Playwright MCP、动态页面、来源和父子 Trace，同时保持与固定 Gate 分离。

### 子任务

1. 更新 `trust/smoke.py` 和 CLI，使 Smoke 从真实 Parent Run 入口启动，而不是直接调用旧 Orchestrator。
2. 选择公开、可访问、无登录要求的 JD URL/查询，并记录来源与抓取时间。
3. 验证 Search → Web Child 多轮 Browser → Envelope → Merge 的完整链路。
4. 验证 route、legacy_path_used=false、Parent/Task/Child ID 和 Tool/Policy/Approval Trace。
5. 验证原始 Snapshot 只在受限 Artifact，主 Context/报告只含标准化内容。
6. 将网络波动、站点变化和 Provider usage 标记为 Smoke 结果，不混入固定基线。
7. 保存独立 `run_type=smoke` 报告和安全脱敏证据。

### 依赖关系

- 前置依赖：Task10、Task11、Task15 至 Task20。
- 后续依赖：Task22。

### 验收标准

- `agent trust real-smoke --run-id <id> --source-url <url>` 完成并产生独立报告。
- 报告包含来源、路由、父子 Trace、预算和失败/partial 说明。
- Smoke 数据不改变固定 Release Gate 分数。
- 没有登录/验证码绕过、敏感日志或 Legacy 调用证据。

### 预估复杂度

中高：真实外部依赖不稳定，但链路边界明确。

## Task22：独立验收、失败修复与全量相关回归

### 任务目标

以独立验收视角核对需求、设计和本计划，修复失败并给出可审计交付报告。

### 子任务

1. 按需求文档逐条建立验收对照表，映射实现文件、测试 Case、Trace/报告证据。
2. 运行委派全部单元、集成、E2E、Fixture、Safety Gate 和可用的真实 Smoke。
3. 运行普通 Chat、RAG、MCP、Gate、Trust、Session、邮件和前端相关回归，确认无跨功能破坏。
4. 复查生产代码中旧 Workflow 的可达性、Legacy 开关默认值/期限和重复副作用计数。
5. 执行安全复核：Tool Schema、Context 隔离、Gate 无旁路、日志/Artifact 脱敏和 Child 无递归委派。
6. 对每个失败先复现、定位根因、添加回归测试，再做最小修复并重跑受影响集合。
7. 运行全量 `uv run pytest`，记录通过/跳过/外部依赖结果。
8. 输出最终验收报告，列出变更文件、命令与结果、指标、Trace/截图/报告、已知限制和残余风险。

### 依赖关系

- 前置依赖：Task1 至 Task21 全部完成。
- 后续依赖：无；用户再次确认后才可进入发布或后续功能。

### 验收标准

- 需求和设计中的每项要求都有文件、测试和证据映射。
- 所有固定必验场景通过，Safety Gate 无新增失败。
- 全量相关回归通过；外部 Smoke 的环境性失败被独立标记且不伪装为通过。
- 报告明确 Multi-Agent 是否满足默认启用门槛。
- 无第二套 Runtime/Gate/Trace/Budget，无递归委派，无旧 Workflow 双轨。

### 预估复杂度

很高：跨系统独立验收、失败闭环和全量回归。

## 实施顺序与报告规则

严格按 `Task1 → Task2 → ... → Task22` 执行。只有前置 Task 的验收标准满足后才进入下一个 Task；若前置接口必须调整，应先更新本计划和已依赖的契约测试，再继续。

每完成一个 Task，报告必须包含：

1. 新增、修改和删除的文件。
2. 实际运行的测试命令、退出码和通过/失败数量。
3. 可复核证据：Trace ID、Eval Run、Artifact/Report 路径、API 响应或前端截图。
4. 与该 Task 验收标准逐项对应的结论。
5. 未解决风险、对后续 Task 的约束和是否需要用户决定。

本计划输出后停止。没有用户明确确认，不执行 Task1、不修改代码。
