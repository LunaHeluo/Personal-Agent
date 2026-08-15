# 求职调研委派系统最终验收报告

- 验收日期：2026-08-14
- 验收范围：已确认的需求、设计与 Task1–Task22
- 代码验收结论：**有条件通过**
- 发布结论：**Multi-Agent 不满足默认启用门槛，必须保持关闭**

## 1. 结论摘要

1. Parent/Child 持久 Run、契约委派、Registry、同一 `AgentRuntime`、五维预算、SQLite 租约 Worker、取消/恢复、结果校验合并、Trace/Artifact、Run API、前端任务卡和 11 个固定 Fixture 已形成闭环。
2. 非外部测试共收集 1358 项。最终全量运行中 1357 项通过、1 项暴露 Parent 人工恢复状态迁移错误；最小修复后该节点及 Coordinator 恢复节点均通过。综合证据覆盖全部 1358 项。
3. 唯一外部 E2E 与 Task21 Smoke 均在 Playwright MCP 初始化阶段阻塞；错误为 `playwright_server_not_ready`/`initialize_timeout`。该结果单独记录为 `blocked`，没有伪装成通过。
4. 当前没有持久化且有效的 passed release decision，也没有一对真实 baseline/candidate 报告证明质量提升、成本和 P95 门槛。因此默认路由继续 fail-closed，普通 Chat 不进入委派或旧网页 Workflow。
5. 工作区仍为未提交状态；本报告不等同于发布批准或 Git 提交。

## 2. Requirement → Implementation → Test/证据矩阵

| 验收域 | 主要实现 | 主要测试/证据 | 结论 |
|---|---|---|---|
| Coordinator 与真实 Child Run | `delegation/coordinator.py`、`service.py`、`tools.py`、`store.py` | `test_delegate_task.py`、`test_delegate_task_gate.py`、`test_coordinator_suspend_resume.py` | 通过；Tool 外观只创建持久 Child Run |
| Registry 与两个 Specialist | `delegation/registry.py`、`config/specialists/` | `test_specialist_registry.py`、Profile/Web Specialist tests | 通过；快照、版本、启停、最小 Tool 权限均有覆盖 |
| 单 Runtime 与 RunContext 隔离 | `agent/runtime.py`、`delegation/context.py` | `test_delegation_runtime.py`、`test_run_context_isolation.py` | 通过；仅一个 `AgentRuntime._run_loop` |
| Tool View 与 Gate 无旁路 | `delegation/tool_view.py`、`capabilities/gate.py` | exposure、snapshot、gate-no-bypass tests | 通过；Child 无 `delegate_task`，父权限不自动扩大 |
| Web Specialist | `delegation/specialists/job_web_researcher.py`、`job_web_error_policy.py` | researcher、handoff、artifact、network guard tests | 固定测试通过；真实 Playwright 环境 blocked |
| Profile Specialist 与授权 RAG | `profile_evidence_analyst.py`、`profile_knowledge.py` | profile scope/bindings/citations tests | 通过；未授权 scope 在 RAG 副作用前拒绝 |
| 五维预算、租约、超时、取消 | `delegation/budget.py`、`dispatcher.py`、`worker.py`、`store.py` | budget、dispatcher、worker、recovery tests | 通过；Token/费用/时间/模型/Tool 调用均受限 |
| Result Envelope、Validator、Merger | `delegation/results.py`、`models.py`、`store.py` | envelope、validator、merger、acceptance tests | 通过；受控 Envelope、来源/证据授权、CAS 合并 |
| Trace、Artifact、Trust | `trust/trace.py`、`session_store.py`、RunStore event bridge | trace、artifact governance、retention tests | 通过；父/任务/子/Tool/Policy/Approval 可关联，公开面脱敏 |
| Run API、取消/恢复、SSE、回填 | `interfaces/runs_api.py`、`delegation/backfill.py`、`src/web/index.html` | delegation API、backfill、UI contract tests | 通过；状态来自数据库，终态停止 SSE |
| 旧 Workflow 迁移 | `application.py`、`interfaces/api.py`、`single_agent_baseline.py` | migration、legacy policy、orchestration tests | 通过；生产旧入口 `legacy_path_forbidden`，历史断言仅显式冻结 baseline |
| Fixture、Eval 与 Release Gate | `evals/job-research-cases.yaml`、`trust/fixture_runtime.py`、`release_gate.py` | 11 场景 fixture、candidate gate、CLI tests | 机制通过；当前无有效 release decision |

## 3. 固定必验场景

Task19 固定矩阵覆盖：双成功、一个 Child 失败、Child 超时、Parent 取消、重复回调、非法 Envelope、来源冲突、权限拒绝、预算耗尽、Single-Agent 更优、唯一 Web Subagent 路由。

每个 case 由独立 fixture state 驱动，保留稳定 case hash、outcome/trace evidence hash，并显式证明 `network=false`、`browser=false`、`provider=false`。深行为分别由 Store、Runtime、Gate、Validator、Merger、Web handoff 测试支撑。

## 4. 实际命令与结果

### 4.1 全量与失败闭环

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .task22-full-pytest
```

- 首次全量：32 failed；其中 1 项为真实 Playwright 外部初始化超时。
- 31 个非外部失败归并为 API lifespan 兼容、Coordinator resume 幂等参数、Registry 测试根目录、预算审计事件契约和已迁移旧 Workflow 测试契约。
- 修复后仅重跑对应失败集合，避免重复执行已通过测试。

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m "not external" -p no:cacheprovider --basetemp .task22-final-pytest
```

- 收集：1358 项。
- 主运行：1357 passed、1 failed。
- 唯一失败根因：`waiting_for_user` 被直接转为 `running`，违反状态机；修复为人工恢复先 `queued`，Coordinator 恢复再 `running`。

```powershell
.\.venv\Scripts\python.exe -m pytest -q \
  tests/integration/test_delegation_api.py::test_runs_api_resume_is_idempotent_and_rejects_stale_or_changed_key \
  tests/integration/test_coordinator_suspend_resume.py::test_woken_parent_must_transition_queued_to_running_before_resume
```

- 结果：2 passed。
- 综合结果：全部 1358 个非外部测试已在最终代码上获得通过证据。

### 4.2 静态验收

- `git diff --check`：exit 0；仅 Git 的 LF→CRLF 提示，无 whitespace error。
- `rg`：`_chat_with_public_job_search_fallback` 在生产源码仅保留定义，无调用点。
- `rg`：只有一个 `AgentRuntime` 和一个 `_run_loop`；未新增第二套 Gate、Trace 或预算系统。
- Specialist 定义和 Tool View 均拒绝 Child 使用 `delegate_task`。

### 4.3 真实 Smoke

报告：[task21-real-smoke-20260814-r2.json](../reports/trust/task21-real-smoke-20260814-r2.json)

- `status=blocked`
- `failure_stage=mcp_startup`
- `error_code=playwright_server_not_ready`
- `separate_from_fixture_baseline=true`
- 未产生 Parent/Child、Search/Browser、模型完成或 Legacy 调用；相关字段保持 `null`。

CLI 对非 passed Smoke 返回非零退出码；未知程序异常会记录最小脱敏错误证据后重新抛出，不会被伪装成环境阻塞。
Smoke 的 Parent 恢复调用使用稳定幂等键；`tests/unit/test_trust_smoke.py` 结果为 9 passed。

## 5. Release Gate 与默认启用结论

量化门槛要求：Task Success、来源完整性、证据保真三项至少一项提升 10pp 且其余不下降；失败复杂度不回退；P95 不超过 2 倍；成本不超过 1.5 倍；Token/费用未知或 Safety 回退均阻断。

当前事实：

- Release Gate、CLI compare、持久 decision 与 hash-bound bootstrap 消费路径已实现并测试。
- 当前生产数据库没有有效 passed decision。
- 没有真实成对 baseline/candidate 报告证明收益门槛成立。
- `delegation_release` 缺省指针为空，默认 fail-closed。

因此：**Multi-Agent 当前不得默认启用**。这不是实现失败，而是量化发布条件尚未满足；也不能回退旧 Workflow。普通 Job Research 请求走禁用提示/普通无 Tool Chat，Legacy 仅限受控 operator baseline 且受期限约束。

## 6. 关键变更文件

- 核心委派：`src/starter_agent/delegation/`、`config/specialists/`
- 复用 Runtime/Gate/Provider：`agent/runtime.py`、`capabilities/gate.py`、`providers/`
- 生产组合/API：`application.py`、`bootstrap.py`、`interfaces/api.py`、`interfaces/runs_api.py`
- Trace/Artifact/Eval：`trust/`、`infrastructure/session_store.py`、`evals/job-research-cases.yaml`
- 前端：`src/web/index.html`
- 验证：新增与更新的 `tests/unit/test_delegation_*`、`tests/integration/test_delegation_*`、Web/Profile/Trust/迁移测试
- 真实环境证据：`reports/trust/task21-real-smoke-20260814-r2.json`

## 7. 已知限制与残余风险

1. Playwright MCP 在当前机器初始化超时，真实 Search/Browser Smoke 尚未成功；环境恢复后必须重跑，且不能改变固定 Release Gate 结果。
2. Multi-Agent 没有通过真实 baseline/candidate 量化对比，默认关闭。
3. 工作区包含大量未提交变更和若干无法访问的历史 pytest 临时目录；未执行清理、提交或发布。
4. 测试存在 Starlette `TestClient/httpx` 弃用警告，不影响本次断言，但应在依赖升级任务中处理。
5. 历史单 Agent 网页行为测试通过测试内显式冻结 baseline 适配器执行；生产 API 不引用该适配器。

## 8. 最终判定

Task22 的代码、固定场景、安全与非外部回归验收通过；真实外部 Smoke 以环境 blocked 单列。当前交付可以等待用户确认，但不能据此默认启用 Multi-Agent、宣称真实网页链成功、提交发布或恢复旧 Workflow。
