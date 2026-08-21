# 求职任务执行编排验收报告

## 1. 验收结论

**Release Gate：PARTIAL**

验收日期：2026-08-15（Asia/Shanghai）

固定评测、编排组件测试、委派边界、安全门禁、API 投影和前端静态契约均通过；但不能发布为完整的“执行编排”能力，原因如下：

1. 生产聊天入口尚未调用 `ExecutionRouter`、`OrchestrationController`、`Planner`、`PlanValidator`、`UnifiedExecutor`、`RuntimeVerifier`、`BoundedRecovery`、`ModelRouter` 或 `OrchestrationTraceProjector`。`Application.chat()` 仍直接调用既有 `AgentRuntime.run()`。
2. `BackgroundTaskService` 已在 Bootstrap 注册，查询/取消 API 也存在，但生产代码没有调用 `BackgroundTaskService.create()`；无法用真实聊天请求证明批量调研立即返回 `task_id`。
3. 真实模型、SerpAPI、Playwright 与个人求职上下文的外发授权不足，真实 Smoke 在执行前被审批层拒绝，未启动任何外部调用。
4. 当前 `trust real-smoke` 实现只启动一个 Web Child，`resume_evidence` 固定为空；即使获得外部授权，也不能单独满足“真实 Search + Browser + RAG、并行 Child Run 与 Join”的验收条件。

关键路由、生产入口计划校验、后台任务创建、真实并行 Child/Join、运行时有限恢复、运行时编排预算和真实 Smoke 没有全部通过，因此不允许判定 `PASS`。Checkpoint、Interrupt 和跨重启原节点恢复不属于本轮门禁，未影响结论。

## 2. 审查范围与方法

独立审查了以下基线：

- [需求](job-application-orchestration-requirements.md)
- [设计](job-application-orchestration-design.md)
- [任务计划](job-application-orchestration-task.md)
- [实现审计](job-application-orchestration-implementation-audit.md)
- [框架决策](agent-runtime-framework-decision.md)
- `backend/src/starter_agent/orchestration/`
- `backend/src/starter_agent/agent/runtime.py`
- `backend/src/starter_agent/application.py`
- `backend/src/starter_agent/bootstrap.py`
- `backend/src/starter_agent/delegation/`
- `backend/src/starter_agent/interfaces/runs_api.py`
- `backend/src/starter_agent/interfaces/tasks_api.py`
- `frontend/web/index.html`
- `backend/src/starter_agent/trust/`
- `evals/job-application-orchestration-cases.yaml`
- 编排、委派、安全、Context、Trace、API 和 UI 测试

验收同时使用了代码调用关系审计、固定 Fixture、JUnit、Release Gate 和外部 Smoke 尝试。离线 Fixture 的 Trace 只作为确定性评测证据，不冒充生产入口 Trace。

## 3. 执行结果

### 3.1 固定评测

- Run ID：`orchestration-acceptance-20260815`
- 合并案例：91
- 编排专用案例：46
- 断言：304
- Trace：325
- Release Gate：`passed`
- Blocking reasons：0
- 报告：[orchestration-acceptance-20260815.json](../artifacts/orchestration-acceptance-current/orchestration-acceptance-20260815.json)
- 数据库：`artifacts/orchestration-acceptance-current/eval.sqlite`

固定集不访问互联网，可重复运行。Route、计划拒绝、DAG、并行条件、Task Event、四种 Join Policy、Verifier、有限 Recovery、五类预算停止、Model fallback、Context 保留、Human Review 和框架不迁移边界均有确定性断言。

### 3.2 编排与关键集成

- 结果：133 passed，0 failed，0 errors，0 skipped
- 耗时：24.958 秒
- 报告：[orchestration-integration.xml](../artifacts/orchestration-acceptance-current/orchestration-integration.xml)

覆盖 `tests/unit/orchestration` 以及求职调研入口、Delegation Runtime、Tool View、Run API 和 Delegate Gate 集成测试。

### 3.3 全量非 external 回归

- 结果：1470 passed，0 failed，0 errors，0 skipped
- 耗时：371.991 秒
- 报告：[full-nonexternal.xml](../artifacts/orchestration-acceptance-current/full-nonexternal.xml)

该集合包含既有第 9 阶段日志/Trace 脱敏、Context/Token、Summary/Memory、MCP Result Guard、Pre-Tool-Call Gate、Approval 屏障和安全门禁，以及第 10 阶段 Parent/Child、Dispatcher/Worker、Tool 隔离、预算、deadline、取消、有限重试、Result Envelope、合并和运行详情回归。

唯一警告是 FastAPI `TestClient` 的 Starlette/httpx 兼容层弃用警告，不影响本次断言。

### 3.4 测试基础设施异常及重跑

沙箱内 pytest 使用 `mode=0700` 创建 Windows 临时目录后，沙箱身份无法枚举目录，首次运行出现：

```text
PermissionError: [WinError 5] 拒绝访问 pytest basetemp
```

原始 JUnit：[background-tasks.xml](../artifacts/orchestration-acceptance-current/background-tasks.xml)

随后在沙箱外使用同一虚拟环境、同一源码和同一测试集合重跑，133 条关键测试及 1470 条全量非 external 测试全部通过。该问题未通过修改产品代码规避，也未计入产品失败。

### 3.5 真实外部 Smoke

尝试命令使用真实配置：

- Provider/Model：`zhipu/glm-4.7`
- SerpAPI：已从 `SERPAPI_API_KEY` 解析，仅检查存在性，未输出密钥
- Browser：Playwright MCP 已配置
- 外部写动作：禁止

审批层在执行前拒绝，原始原因：

```text
该 Smoke 会把可能包含简历/个人求职上下文的数据发送至外部
zhipu/glm-4.7，并访问 SerpAPI/Playwright；用户授权了真实 Smoke，
但未明确授权该具体敏感载荷发送至这些具体目的地。
```

- 外部模型调用：0
- Search 调用：0
- Browser 调用：0
- 邮件发送：0
- 求职投递：0
- 结构化证据：[real-smoke-authorization-blocked.json](../artifacts/orchestration-acceptance-current/real-smoke-authorization-blocked.json)

最小用户动作：明确授权 `zhipu/glm-4.7`、SerpAPI 与 Playwright 接收本次 Smoke 提示以及可能绑定的简历/个人求职上下文，然后以新 `run_id` 重跑。仅完成该授权仍不足以判 `PASS`；还需先让 Smoke 创建至少两个输入独立的真实 Child（例如公开 JD 检索与脱敏简历/RAG 证据检索），使用真实 Join Policy 汇合并验证。

## 4. 逐项验收矩阵

| # | 状态 | 验收结果 | 主要证据 |
|---|---|---|---|
| 1 | **失败** | 五类 Route 在 Router 测试和固定 Trace 中齐全，但真实 `chat` 输入不经过执行 Router，无法提供五条生产 Trace。 | `test_router.py`、Fixture 报告；`application.py::chat` 调用审计 |
| 2 | **部分通过** | Router Schema、原因、置信度、fallback、能力和风险规则通过；低置信度/高风险测试均 fail closed。未接生产入口。 | `test_router.py`、`orchestration/router.py` |
| 3 | **部分通过** | Direct 无 Plan/Child、Planner 限复杂 Route、Validator 检查权限/Tool/依赖/预算均通过；未形成真实入口的端到端链。 | `test_models.py`、`test_planner_validator.py`、`test_executor_controller.py` |
| 4 | **部分通过** | 离线 Eval 与 Runtime Verifier 类、输入和动作边界明确；Verifier 返回具体失败项，Recovery 定向且最多 1–2 次。Verifier/Recovery 未接生产 Run。 | `test_verifier.py`、`test_recovery.py`、`test_fixture_suite.py` |
| 5 | **部分通过** | steps/tokens/cost/wall-clock/tool_calls/model_calls 组件记账、Parent/Child 预留和安全停止通过；主聊天入口未使用编排 Budget Manager。 | `test_budget.py`、`test_delegation_runtime.py` |
| 6 | **部分通过** | Model Router 只选已配置模型、有限 fallback、无秘密、可生成 Decision；生产入口未调用或投影 Model Decision。 | `test_model_router.py`；全仓调用关系审计 |
| 7 | **部分通过** | State/Node/条件 Edge 与设计一致，测试证明非强制大链；生产 `chat` 绕过该图。 | `test_state_graph.py`、`test_executor_controller.py`、`orchestration/graph.py` |
| 8 | **失败** | 后台生命周期、持久化和 API 查询/取消组件通过；生产无 `BackgroundTaskService.create()` 调用，无法证明批量请求立即返回真实 `task_id`。 | `test_background_tasks.py`、`tasks_api.py`、全仓调用关系审计 |
| 9 | **部分通过** | DAG 对输入依赖、共享写、Envelope、预算、限流和 backpressure 的判断均通过；尚未由真实 Planner Run 驱动。 | `test_dag_scheduler.py` |
| 10 | **部分通过** | fan-out 可向现有 Store 写入两个隔离 Child，最小 Context、Tool 收窄、预算、deadline、Envelope 均有断言；生产编排未调用该服务。 | `test_fanout.py`、`test_delegation_tool_exposure.py` |
| 11 | **部分通过** | 六类结构化事件、幂等、乱序缓冲、迟到终态、并发限制均通过；只证明组件和既有 Delegation Runtime，未证明新编排入口。 | `test_task_manager.py`、`test_delegation_trace.py` |
| 12 | **部分通过** | `all_required`、`partial_allowed`、`first_success`、`deadline_reached` 均通过，未发现模型轮询；生产编排 Parent 尚未使用这些 Join Decision。 | `test_join.py`、源码中无模型轮询调用 |
| 13 | **通过** | Parent 投影只接收 Task Snapshot、result ref 与结构化结果；Child 对话、scratchpad 和原始 Tool Result 不进入 Parent/API。 | `test_context.py`、`test_fanout.py`、`test_delegation_ui_contract.py` |
| 14 | **通过** | Summary/Trim 与 checkpoint round-trip 测试保留 Goal、安全策略、Plan、Todo、Task Snapshot 和预算。 | `test_context.py`、`test_run_context_isolation.py` |
| 15 | **部分通过** | Trace Schema 和关联审计覆盖 Route/Plan/Step/Parent/Child/Event/Join/Tool/Verify/Recovery/Budget/Model；TraceProjector 未注册到生产入口。 | `test_trace.py`、`orchestration/trace.py`、全仓调用关系审计 |
| 16 | **通过** | 框架决策包含真实仓库映射、成本、回滚和“不迁移”结论；Checkpoint/Interrupt 仅记录未来采用信号。 | `agent-runtime-framework-decision.md` |
| 17 | **通过** | 46 条编排固定案例可重复运行；合并 91 案例 Release Gate 通过；1470 条非 external 安全与委派回归通过。 | Fixture、Eval JSON、JUnit |
| 18 | **未执行/阻塞** | 外部授权不足，真实模型/Search/Browser/RAG 未启动；且当前 Smoke 只有一个 Web Child，无法证明并行 Child + Join。未发送邮件或投递。 | `real-smoke-authorization-blocked.json`、`trust/smoke.py` |
| 19 | **部分通过** | Run API/SSE/UI 从后端 Store 投影真实状态并隐藏原始 Context；由于生产入口不产生编排 State，无法验证真实后台 Run 的端到端一致性。 | `test_run_detail.py`、`test_delegation_api.py`、`test_delegation_ui_contract.py` |

## 5. 已通过项

1. 严格、版本化的 Route、Plan、Plan Step、Task、Event、Join、Verify、Recovery、Budget、Model Decision、Pending Action 和 Execution State Schema。
2. Router 风险优先级、低置信度澄清、Tool 关闭 fail closed。
3. Plan 循环、重复/缺失依赖、越权能力、预算超限和高风险动作拒绝。
4. DAG 串并行条件、Parent/Child 最小任务包、Tool View 收窄和 Result Envelope。
5. Child Event 幂等、乱序、迟到、超时、失败、取消和有限重试组件逻辑。
6. 四种 Join Policy、确定性 Verifier、Judge 边界和 Bounded Recovery。
7. 六维预算组件及既有 Delegation Runtime 的真实记账/停止。
8. Context 隔离、Summary/Trim 保留、Trace 关联和脱敏。
9. 现有 Approval Gate 的发送前阻断、参数变更后旧批准失效和恢复前预算重检。
10. 固定 Eval、安全回归、委派回归、后端读模型和前端无静态占位契约。

## 6. 失败项

### F1：生产入口未接执行编排控制面

全仓调用关系显示，核心类只出现在 `backend/src/starter_agent/orchestration/` 定义、单元测试和离线 Fixture Adapter 中。`Application.chat()` 直接执行既有 `AgentRuntime.run()`，没有建立 `Route -> 条件 Edge -> Planner/Executor/Human Review` 的生产状态转移。

修复要求：增加现有 Runtime 前的编排服务入口，复用现有 Runtime/Workflow/Gate/Delegation；为每次真实 Run 持久化 Execution State 和 Trace。不得复制第二 Runtime。

### F2：后台任务没有生产创建入口

Bootstrap 注册了 `BackgroundTaskService`，`/v1/tasks/{task_id}` 支持查询/事件/取消，但没有生产调用者创建任务。当前只能由测试直接调用服务。

修复要求：复杂/批量 Route 经 Validator 和预算预检后调用现有服务创建 Parent/Task，立即把真实 `task_id` 返回 Chat/API，并由 Store 驱动状态。

### F3：运行时 Verifier、Recovery、Budget、Model Decision 和 Trace 未贯通

这些组件均通过独立测试，但没有从生产入口到 Run Store 的调用链，因此无法证明当前 Run 会根据 Verify/Budget/Model Decision 转移。

修复要求：按设计把组件作为条件节点接到现有 Runtime Adapter，增加至少一条真实输入集成测试，断言调用顺序、状态转移和关联 ID。

### F4：真实 Smoke Harness 不满足并行 RAG/Join 契约

当前实现只创建一个 Web Child，随后 Parent 合并单一结果；`resume_evidence` 是空列表。它不能证明两个输入独立 Child 的并行执行、RAG 证据、Join Policy 或部分失败治理。

修复要求：Smoke 使用脱敏测试简历/RAG Fixture 或明确获批的个人上下文，创建至少 Web Research 与 Profile/RAG Evidence 两个隔离 Child；记录 `child_started/completed`、真实重叠时序、Join Decision、Merge、Verify 和预算。

## 7. 未执行项

1. 真实 `zhipu/glm-4.7` 模型调用。
2. 真实 SerpAPI 岗位搜索。
3. 真实 Playwright MCP 岗位读取。
4. 真实 RAG 简历证据检索与并行 Child 汇合。
5. 真实运行详情页面的人工浏览器验收；API 与 HTML 契约测试已执行，但缺少生产编排 Run 数据。
6. 任何真实邮件发送或求职投递；按验收要求保持为 0。

## 8. 剩余风险

1. 组件测试和 Fixture 可能掩盖生产 wiring 缺失；在入口贯通前，Release Gate 分数不能代表用户可见功能可用。
2. Citation Correctness 当前为 `0.8181818182`，虽满足现有固定 Gate，但发布前应确认阈值与产品 Rubric 是否足够严格。
3. 真实 Provider、SerpAPI、Playwright、站点限流、验证码和页面变化尚未验收。
4. 真实并行执行下的 Provider 限流、长尾 deadline、取消传播和 backpressure 尚未得到外部 Smoke 证明。
5. 前端字段契约已验证，但没有生产生成的复杂编排 Run 可用于刷新、SSE 重连、取消和部分结果的人工验收。
6. Starlette/httpx 弃用警告未来可能升级为兼容性问题。

## 9. 重跑命令

```powershell
.venv\Scripts\python.exe -m starter_agent.main trust fixture-baseline `
  --run-id orchestration-acceptance-20260815 `
  --database-url sqlite:///artifacts/orchestration-acceptance-current/eval.sqlite `
  --report-dir artifacts/orchestration-acceptance-current

.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q `
  tests/unit/orchestration `
  tests/integration/test_job_research_orchestration.py `
  tests/integration/test_job_research_delegation_start.py `
  tests/integration/test_delegation_runtime.py `
  tests/integration/test_delegation_tool_exposure.py `
  tests/integration/test_delegation_api.py `
  tests/integration/test_delegate_task_gate.py

.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q -m "not external"

.venv\Scripts\python.exe -m starter_agent.main trust real-smoke `
  --run-id <new-run-id> `
  --database-url sqlite:///artifacts/orchestration-acceptance-current/real-smoke.sqlite `
  --report-dir artifacts/orchestration-acceptance-current `
  --source-url <approved-public-jd-url>
```

真实 Smoke 重跑前必须同时满足：

1. 用户明确批准具体 Provider/Model、SerpAPI、Playwright 和个人/脱敏求职上下文的数据目的地；
2. Smoke Harness 已扩展为至少两个真实并行 Child，并记录 Join/Verify/预算 Trace；
3. 生产聊天入口已经接入执行编排控制面；
4. 邮件与投递仍停在现有 Approval Gate，不产生真实副作用。

## 10. Release Gate

**PARTIAL**

允许继续开发和在关闭默认功能开关的条件下做内部集成验证；不允许宣称执行编排已完成，也不允许默认发布。完成 F1–F4、获得外部数据授权、真实 Smoke 成功并重跑上述全量回归后，方可重新评估 `PASS`。
