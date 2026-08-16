# 求职任务委派功能验收报告

- 验收日期：2026-08-15
- 验收对象：当前工作区实现、41 条委派 Fixture、真实 Runtime/Store/Gate/Trace/API/前端契约及公开 JD Smoke
- 最终 Release Gate：**PARTIAL**
- 判定原因：本地关键契约、固定评测和真实公开链路均获得有效证据，但修复后最后一次独立 Smoke（R6）受外部页面导航波动阻塞；R5 的产品链路成功，CLI 报告序列化曾失败。按“真实 Smoke 完整通过才可 PASS”的门槛，不上调为 PASS。

## 1. 执行结果摘要

1. 委派关键测试：316 passed，0 failed，46.879s。
2. 修复回归：87、102、53、56、14、22 项分别全部通过；首次 87 项运行中的 1 个失败已定位、修复并重跑通过。
3. 固定 Runner：121 cases，462 assertions，500 Trace；其中委派 41 cases、227 条委派确定性断言；Release Gate=`passed`。
4. 真实公开 Smoke：R5 已生成真实 Parent/Child、6 轮 Child 模型调用、Search、Navigate、Wait、Snapshot、受控 Envelope、Merge 与最终模型调用，`legacy_path_used=false`，且无投递/邮件；报告层因旧字段名崩溃。修复后 R6 在实时页面导航阶段没有产出岗位，报告为 `blocked`。
5. 验收中修复 7 个真实问题：`browser_wait_for` 目标无关读取误拒、空 `final_url` 权限误判、MCP Snapshot 外层契约错投影、Parent Schema 投影越界、partial 已验证岗位丢失、Child partial 状态未向 Parent 传播，以及 Smoke Merge 字段名过期。

## 2. 24 项验收矩阵

| # | 结论 | 验证结果与证据 |
|---:|---|---|
| 1 | 通过 | 固定案例证明稳定单 URL/简单任务 `delegation_count=0`；复杂调研进入 `delegated_job_research`。生产入口测试证明只创建一棵 Parent/Child 树。 |
| 2 | 通过 | `delegate_task` 仅 Coordinator Tool View 可见；Gate 对 Specialist 确定性返回 `delegate_task_coordinator_only`；Service 原子创建持久 Child Task/Run。 |
| 3 | 通过 | 集成测试记录 Child 独立 System Prompt、模型请求、Context、Tool Schema、预算及多轮模型/Tool Loop；R5 为真实 Provider 的 6 轮 Child 调用，不是 Mock 包装。 |
| 4 | 通过 | Parent/Child 均调用同一 `AgentRuntime`/`_run_loop`；每次由 `ChildContextBuilder` 新建 `RunContext`，未发现复制 Parent Agent 或第二套 Loop。 |
| 5 | 通过 | 对象身份与并发污染测试覆盖 messages、working memory、todo/plan、Tool View、budget、cancellation、summary/trim、output buffer；Sibling 与 Parent 均不共享可变对象。 |
| 6 | 通过 | Child Context 仅由 Contract、Registry、Runtime 边界和已授权引用组成；跨 principal/project/sibling 引用在读取前拒绝；真实 Model Context Snapshot 只列 Specialist Tool Schema。 |
| 7 | 通过 | Coordinator 注入 Prompt/Memory/Tool Schema 被忽略或拒绝；预算取安全交集；Child Tool View 不含 `delegate_task`。 |
| 8 | 通过 | Web Specialist 仅 Search/Playwright；Profile Specialist 仅授权 RAG；两个 Registry 定义、Prompt、Schema 和依赖独立。 |
| 9 | 通过 | 固定三 JD 测试覆盖 Search→Navigate→Wait→Snapshot→Extract→Validate→Next；R5 真实完成公开 JD 的前述链路并验证 1 个岗位；单页稳定路径不创建 Subagent。 |
| 10 | 通过 | 审计列出旧 Workflow 全入口；迁移测试证明生产 Router/API/Service/前端不调用旧多页 Workflow。 |
| 11 | 通过 | 固定与生产 Trace 均为 `legacy_path_used=false`；同一请求只创建一次 Web Child；CAS/幂等断言阻止重复搜索结果合并和重复业务写。 |
| 12 | 通过 | 单页 Tool 路径保留；兼容 Adapter 内部只走新路径；回滚开关默认关闭且不是正常降级。 |
| 13 | 通过 | Coordinator 模型仅见 `delegate_task`；Web Child 只见 Search/Browser；Profile Child 只见 RAG；契约外调用由 Pre-Tool-Call Gate 拒绝。 |
| 14 | 通过 | 404、空正文、加载失败、结构变化、重复页、重定向均有有界策略；登录/验证码/访问限制进入 waiting/partial，不绕过。 |
| 15 | 通过 | 原始 HTML/Snapshot/DOM 留在 `child_restricted` Artifact；此次修复进一步确保 Parent JD 投影移除 `raw_text/source_spans/page_type`。 |
| 16 | 通过 | Task Contract 字段完整；Result Envelope 冻结且禁额外字段；Parent 仅接收标准化 output/evidence/missing/conflicts/usage/ref。 |
| 17 | 通过 | 并发候选写使用 expected version、CAS、幂等键和 Merge Report；测试证明无 last-write-wins。 |
| 18 | 通过 | 全局/每 Specialist 并发、lease/heartbeat、deadline、取消传播、有限重试、重复/迟到回调和部分失败均有确定性测试。 |
| 19 | 通过 | 失败/超时返回 partial、missing、conflicts/errors；不生成缺失事实。验收修复了有已验证岗位时 partial 分支丢弃 jobs 的问题。 |
| 20 | 通过 | Child Tool Call 仍经统一 Gate/Permit/Network Guard；R5 Trace 有 Search 与三个 Playwright Tool 的 Gate/Tool 事件。 |
| 21 | 通过 | Trace 可关联 Parent Run、Child Task/Run、model request、Tool、Policy/Approval、Artifact、Validation 与 Merge；R5 durable IDs 已单列。 |
| 22 | 通过（固定） | 41 条委派固定案例可重复运行，包含单/多 Agent latency/token/cost/quality/failure complexity 对照；数值为固定回归基线，不代表生产容量。 |
| 23 | **部分通过** | R5 产品链路真实成功但报告序列化失败；修复后 R6 因实时导航未产出岗位而 `blocked`。尚无一份修复后、CLI `status=passed` 的独立真实 Smoke 报告。 |
| 24 | 通过 | Run API/SSE/取消/恢复/刷新与 UI contract 测试读取真实 Store 状态；终态停止 SSE，错误和 partial 来自后端，完整聊天展示未被替换。 |

## 3. 验收中发现并修复的问题

| 问题 | 根因 | 修复 | 回归证据 |
|---|---|---|---|
| `browser_wait_for` 返回 `unsafe_url` | Gate 与 Network Guard 未把它视为已导航页面上的目标无关读取 | 加入有界 wait 分类并复用已提交导航目标 | `delegation-fixes-rerun.xml` |
| partial Envelope 被判 `result_source_unauthorized` | 空 `final_url` 被规范化为 `/` | 权限扫描忽略空诊断 URL，非空未授权 URL 仍拒绝 | `delegation-fixes-rerun.xml` |
| Snapshot 已提取 JD 但 Specialist 看不到 | Runtime 传递 MCP 外层 wrapper | 投影 `structured_content` 并绑定来源、hash、artifact ref | `delegation-snapshot-fix.xml` |
| Parent Schema 拒绝结构化 JD | 投影仍含 raw text/source spans 等 Child-only 字段 | 严格白名单标准 JD 字段并补 `retrieved_at` | `delegation-contract-fix.xml` |
| 有效首个 JD 在后续失败时消失 | boundary reason 分支重建空 jobs | 所有 partial 分支保留已验证 jobs/missing | `delegation-partial-fix.xml` |
| Child partial 合并后 Parent 标为 succeeded | Merge 只把“被拒绝 Child”计为 partial | accepted Child 为 partial 时向 Parent 传播 partial | `delegation-final-merge-fix.xml` |
| Smoke 报告崩溃 | 读取旧字段 `merge.output_ref` | 改为 `merge.final_output_ref` | `delegation-smoke-report-fix.xml` |

## 4. 证据路径与命令

关键证据：

- `artifacts/delegation-acceptance-current/delegation-critical.xml`：316 passed。
- `artifacts/delegation-acceptance-current/delegation-acceptance-20260815.json`：121 cases、462 assertions、500 Trace、Gate passed。
- `evals/job-application-delegation-cases.yaml` 与 `evals/job-research/fixtures/delegation_scenarios.json`：41 条脱敏委派案例。
- `artifacts/delegation-acceptance-current/delegation-acceptance-real-20260815-r5-postmortem.json`：R5 durable Parent/Child/Envelope/Merge 证据与报告错误。
- `artifacts/delegation-acceptance-current/delegation-acceptance-real-20260815-r6.json`：修复后外部 Smoke 阻塞原始结果。
- `data/agent.db`：真实 Parent/Child/Task/Event/Artifact/Merge 与 Capability Audit 数据源。

固定 Runner：

```powershell
.venv\Scripts\python.exe -m starter_agent.main trust fixture-baseline `
  --run-id delegation-acceptance-20260815 `
  --database-url sqlite:///artifacts/delegation-acceptance-current/delegation-eval.sqlite `
  --report-dir artifacts/delegation-acceptance-current
```

真实 Smoke（公开数据、无投递）：

```powershell
.venv\Scripts\python.exe -m starter_agent.main trust real-smoke `
  --run-id delegation-acceptance-real-20260815-r6 `
  --database-url sqlite:///artifacts/delegation-acceptance-current/real-smoke-r6.sqlite `
  --report-dir artifacts/delegation-acceptance-current `
  --provider zhipu --model glm-4.7
```

## 5. 失败项、未执行项与剩余风险

### 失败/未满足

- 没有获得“修复后独立真实 Smoke CLI 返回 0 且报告 `status=passed`”的最终证据，因此第 23 项与总 Release Gate 只能为 PARTIAL。

### 未执行

- 未真实发送邮件或投递；这符合验收边界。
- 未做浏览器 UI 的人工点击巡检；API/SSE/前端契约由自动化测试验证。
- 未做生产规模并发压测；固定性能对照仅用于回归。

### 原始外部错误与最小恢复动作

- R6 原始结果：`error_code=delegated_web_child_returned_no_jobs`，`failure_stage=child_execution`。该轮 Router、真实模型与 SerpAPI 已成功，Playwright 首次导航未形成可用岗位。
- 最小动作：保持当前代码与相同公开 URL，使用新 run_id 在 Playwright/目标站点稳定时重跑一次 `trust real-smoke`；只有生成 `status=passed` 报告后，才可把 Release Gate 上调为 PASS。

### 剩余风险

1. 外部站点、Provider 输出与 Playwright 时序仍有波动，Smoke 需要独立重试策略和更清晰的阶段错误码。
2. R5/R6 暴露 Smoke 自身与生产契约容易漂移；建议将真实 Schema 字段访问纳入静态类型/契约测试。
3. 工作区存在大量未提交的用户与功能变更；本报告不代表已提交、已发布或已默认启用 Multi-Agent。

## 6. 最终判定

**Release Gate：PARTIAL**

关键契约、权限、父子隔离、Trace、失败治理、固定评测与真实公开链路均有实证；但严格发布门槛要求一份修复后完整通过的真实 Smoke 报告，当前仍缺失。禁止据此宣称生产真实 Smoke 全通过或默认启用委派。
