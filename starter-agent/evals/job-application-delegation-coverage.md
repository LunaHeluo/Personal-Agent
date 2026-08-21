# 求职任务委派固定评测覆盖矩阵

## 结果摘要

- 案例文件：`evals/job-application-delegation-cases.yaml`
- 脱敏 Fixture：`evals/job-research/fixtures/delegation_scenarios.json`
- Manifest ID：`delegation-scenarios-redacted-v1`
- 委派案例：41
- 委派确定性断言：227
- 合并 baseline：121 cases
- Trace：500
- Release Gate：`passed`
- 外部网络、Browser、Provider 调用：0
- Judge：固定运行中关闭；Rubric 只标注表达质量，不参与权限、Schema、状态、预算或合并判断

## 覆盖矩阵

| 覆盖域 | 案例数 | 代表案例 | 确定性证据 |
|---|---:|---|---|
| 简单任务与单 Agent 路由 | 2 | `delegation-stable-single-url-direct`、`delegation-single-agent-better` | `delegation_count=0`、单次 Tool、固定延迟/Token/成本 |
| 双 Specialist 与部分失败 | 3 | `delegation-dual-success`、`delegation-child-failure-partial`、`delegation-child-timeout` | Child 终态、accepted count、Merge 次数、deadline、partial |
| 多页面循环与网页异常治理 | 8 | 三 JD 循环、加载失败、404、空正文、重定向、结构变化、重复页、访问限制 | Tool 顺序、重试上限、候选推进、去重、不绕过、waiting/partial |
| Artifact 隔离与迁移单轨 | 5 | 原始页面隔离、唯一 Web Child、旧路径关闭、兼容 Adapter、回滚默认关闭 | Parent 字段白名单、一次 `delegate_task`、旧调用次数和重复副作用均为 0 |
| Tool/Context/Runtime 隔离 | 7 | Tool Schema 分区、无证据诚实缺口、真实 Child、RunContext/并发隔离、Registry 交集、引用加载 | 独立模型请求、共享 Runtime、不同 Context、污染计数 0、最小引用 |
| 生命周期、权限与结果完整性 | 9 | 取消传播、重复/迟到回调、非法 Envelope、source/chunk 冲突、契约外 Tool、受控投影、并发写、递归拒绝 | 取消后调用 0、只合并一次、Schema/Gate、冲突保留、版本/幂等 |
| Specialist Registry 与 Coordinator 权限 | 4 | 不存在、关闭、版本不兼容、覆盖 Prompt/Tool/预算 | Child 未创建、稳定错误码、安全交集/拒绝 |
| Parent/Child 预算与性能对照 | 3 | Child 耗尽、Parent 耗尽、固定单/多 Agent 对比 | 停止后 Tool 0、部分结果、固定时间/Token/成本/质量 |

## Task Contract 与 Specialist 对齐

案例字段基于当前真实 `TaskContract`：

- `task_id`、`parent_run_id`、`specialist_id`
- `goal`、`inputs`、`constraints`
- `requested_allowed_tools`
- `requested_deadline`、`requested_budget`
- `failure_behavior`、`idempotency_key`、`contract_version`

两个 Specialist 的工具边界来自当前版本化 Registry：

- `job_web_researcher`：SerpAPI 与经 Gate 管理的 Playwright Search/Browser 能力，不含 RAG 或 `delegate_task`。
- `profile_evidence_analyst`：仅含授权 `retrieve_resume_evidence`，不含 Search/Browser 或 `delegate_task`。

Result Envelope 按真实 Schema 验证 `status/output/evidence/missing/conflicts/errors/usage/child_run_id/task_id/trace_ref/idempotency_key`；Parent 投影不复制 Child Messages、原始 HTML、Browser Snapshot 或原始 Tool Result。

## 运行命令

Schema、Manifest 与脱敏检查：

```powershell
.venv\Scripts\python.exe -c "import yaml,pathlib; from starter_agent.trust.models import EvalCase; from starter_agent.trust.fixtures import JobResearchFixtureLoader; root=pathlib.Path('.'); payload=yaml.safe_load((root/'evals/job-application-delegation-cases.yaml').read_text(encoding='utf-8')); cases=[EvalCase(**item) for item in payload['cases']]; manifest=JobResearchFixtureLoader(root/'evals/job-research/fixtures').load_manifest(); print(len(cases), manifest.by_id('delegation-scenarios-redacted-v1').content_hash)"
```

评测与 Runner 回归：

```powershell
.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q `
  tests/unit/test_delegation_fixture_suite.py `
  tests/unit/test_trust_fixtures.py `
  tests/unit/test_job_research_baseline_runner.py `
  tests/unit/test_trust_rules.py
```

完整固定 baseline：

```powershell
.venv\Scripts\python.exe -m starter_agent.main trust fixture-baseline `
  --run-id delegation-fixture-v1-20260815 `
  --database-url sqlite:///artifacts/delegation-eval/delegation-eval.sqlite `
  --report-dir artifacts/delegation-eval
```

报告：`artifacts/delegation-eval/delegation-fixture-v1-20260815.json`

## 未覆盖风险

1. 固定 Fixture 是脱敏、确定性的终态和时序观察，不替代真实 Provider、SerpAPI、Playwright、RAG 或动态站点 Smoke。
2. 单/多 Agent 时间、Token、成本与质量数字是固定对照基线，只用于检测版本回归，不是生产容量或财务预测。
3. Fixture Adapter 不执行真实 Child Worker；真实 Runtime/Store/Gate/Context 边界由 `evidence_test_ids` 指向的单元和集成测试补充证明。
4. Judge 在固定运行中关闭；未来启用时只能评价合并说明和对比说明的表达质量，不能覆盖确定性失败。
5. Provider 限流、站点验证码变化、浏览器版本漂移和大规模 Worker 并发仍需单独的受控 Smoke/压力测试。
