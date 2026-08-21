# 求职任务执行编排评测覆盖矩阵

## 固定输入与执行边界

- Case 集：`evals/job-application-orchestration-cases.yaml`，版本 `v2`。
- 脱敏 Fixture：`evals/job-research/fixtures/orchestration_scenarios.json`。
- Manifest：`evals/job-research/fixtures/manifest.yaml`，由 Loader 校验 SHA-256、字段和秘密标记。
- 所有编排 Case 均为离线确定性输入；`network_called`、`browser_called`、`provider_called` 必须为 `false`。
- Route、权限、Plan Validation、DAG 调度、Task Event、Join、预算、调用顺序和副作用次数使用确定性断言；Judge 仅用于周报表达质量与排序说明质量。
- Runtime Verifier 只产生当前 Run 的 Verify 决策；Fixture Case 明确断言 `offline_eval_calls=0`。
- 当前框架决策为不迁移，因此框架适配对比标记为 `not_applicable_no_migration`。Checkpoint 与 Interrupt 不作为通过条件。

## 覆盖矩阵

| 能力/风险 | 主要 Case | 确定性证据 |
| --- | --- | --- |
| Direct 简单解释 | `orchestration-direct-simple-qa` | 无 Plan、Tool、Child |
| 固定周报 Workflow | `orchestration-workflow-weekly-report` | 固定 workflow_id；Judge 仅看表达质量 |
| 单 JD Tool Loop | `orchestration-tool-loop-single-jd` | Route→Tool；一次读取；无 Planner/Child |
| 三家公司调研与匹配 | `orchestration-plan-three-company-match` | Plan→Child→Join→Verify；先并行后串行 |
| 邮件 Human Review | `orchestration-send-email-human-review` | Approval Gate；外部副作用 0；不要求 Checkpoint |
| 低置信度/输入缺失 | `orchestration-router-low-confidence`、`orchestration-router-input-missing` | clarification；Planner 0 |
| Tool 关闭 | `orchestration-validator-tool-disabled` | Validator stop；Child 0；副作用 0 |
| Plan 循环/重复/越权/超预算 | `orchestration-validator-*` | 具体 validation_failure 与 decision |
| Verifier 来源/引用/必填/业务规则 | `orchestration-verifier-*` | 具体 verify_failure 与状态转移 |
| Bounded Recovery | `orchestration-recovery-targeted`、`orchestration-recovery-limit` | 只修失败项；保留未失败项；最多 2 次 |
| 五维安全停止 | `orchestration-budget-*` | steps、tokens、cost、wall-clock、tool_calls 分别停止 |
| Model Router fallback | `orchestration-model-router-fallback` | 两次 Model 事件；权限与 Approval 不变 |
| Summary/Trim 保留权威状态 | `orchestration-context-trim-preserves-state` | Goal、Plan、Todo、Budget 均保留 |
| 简单任务不委派 | `orchestration-simple-no-multi-agent` | Planner/Delegation/Child 均为 0 |
| 前台/后台边界 | `orchestration-foreground-short-task`、`orchestration-background-batch-task` | task_id 与真实生命周期断言 |
| DAG 并行 | `orchestration-three-jd-parallel` | 三个独立 Child 并行，all_required 汇合 |
| DAG 保持串行 | `orchestration-serial-*` | 输入依赖、共享写冲突、缺 Result Envelope |
| Child 隔离 | `orchestration-child-isolated-package` | 最小任务包、Tool View、预算、deadline、Envelope；无完整会话 |
| Child 终态事件 | `orchestration-child-*-event` | completed/failed/timed_out/cancelled；模型轮询 0 |
| 事件幂等与乱序 | `orchestration-event-idempotency-ordering` | buffer、late ignore、merge_count=1 |
| 四类 Join Policy | `orchestration-join-*` | all_required、partial_allowed、first_success、deadline_reached |
| 部分失败治理 | `orchestration-partial-failure-human-review` | Join→Human Review；不无限等待 |
| Parent 紧凑上下文 | `orchestration-parent-compact-context` | 只接收 Task Snapshot 与结构化结果，不接收 Child 对话/原始 Tool Result |
| Runtime Verifier / Offline Eval 边界 | `orchestration-runtime-verifier-only` | 当前 Run Verify；offline_eval_calls=0 |
| 框架与未来边界 | `orchestration-framework-parity-not-applicable` | 当前无适配迁移；Checkpoint/Interrupt 不进门禁 |

## 执行命令

Schema、脱敏和 dry-run：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/orchestration/test_fixture_suite.py tests/unit/test_trust_fixtures.py tests/unit/test_job_research_eval_cases.py
```

Eval Runner、规则和可重复性回归：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/orchestration/test_fixture_suite.py tests/unit/test_job_research_baseline_runner.py tests/unit/test_trust_rules.py
```

生成完整离线报告：

```powershell
.\.venv\Scripts\python.exe -m starter_agent.main trust fixture-baseline --run-id orchestration-fixture-v2 --database-url sqlite:///artifacts/orchestration-eval/eval.sqlite --report-dir artifacts/orchestration-eval
```

## 未覆盖风险

1. 固定 Fixture 不访问互联网，不覆盖 SerpAPI、招聘站点、DNS、Playwright 进程或 Provider 的实时可用性；这些只能进入独立 Smoke，不能污染离线 baseline。
2. Judge 未调用真实模型，只冻结其职责边界；实际表达质量、偏差和模型漂移需要单独 Judge/人工抽检。
3. 当前没有 LangChain、LangGraph 或 OpenAI Agents SDK 适配，因此没有迁移前后行为对比；若未来引入适配，必须复用本套 Case/Fixture 后再开启对应门禁。
4. Checkpoint、Interrupt、步骤级恢复和跨重启原节点恢复属于未来扩展，不是当前评测通过条件。
5. Fixture 验证确定性状态与契约，不替代真实数据库高负载、Provider 限流和多进程竞态压测。
