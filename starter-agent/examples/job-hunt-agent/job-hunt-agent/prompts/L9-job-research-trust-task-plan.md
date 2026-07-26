# L9 · 求职调研信任层任务计划

用途：设计确认后生成 `job-research-trust-task.md`，待用户确认计划后再实现。

---BEGIN---
你是我的 Agent 工程实现协作伙伴。请使用中文工作。

前提：

- `job-research-trust-requirements.md` 已确认。
- `job-research-trust-design.md` 已确认。
- 现在只生成 `job-research-trust-task.md`，不要立即修改代码。等我确认计划后，再按顺序执行。

`job-research-trust-task.md` 必须由有序 Task1 / Task2 / Task3 ... 组成。每个 Task 必须包含：

- 任务目标
- 子任务
- 依赖关系
- 验收标准
- 预估复杂度

不要生成“状态”字段，不要写静态任务状态文本。执行进度由运行时任务机制单独记录。

任务计划至少覆盖：

1. 审计现有 job-research、Tool / MCP / RAG、Pre-Tool-Call Gate、Trace、Log、Token 与前端能力，记录真实入口和缺口。
2. 建立 Suite、Case、Fixture、Run、Result、Assertion、Metric、Failure Cluster 和 Release Gate 数据模型及版本迁移。
3. 实现固定 Fixture 装载与 case 隔离，准备脱敏搜索结果、JD、简历 Chunk、Tool Error、Policy 和 Injection Fixture。
4. 实现 Eval Runner 的超时、取消、并发、重试、状态流转和可重现配置。
5. 实现规则评测器：Schema、Tool、参数、source_url、Chunk 引用、Tool 启停与 Schema 暴露、Policy / Approval 顺序和无真实越权调用。
6. 实现程序指标：任务成功、Tool / Argument Accuracy、Citation、Approval Compliance、P50/P95、Token 和单次成功成本。
7. 实现可选 LLM Judge，要求 Rubric、模型版本、原始评分、理由、golden 样例和人工抽查；Judge 不决定硬权限和安全门禁。
8. 贯通 Eval 与 Trace Context，记录 Case → Session → Turn → Model / Tool / Policy / Approval 的关联与错误。
9. 实现结构化日志与写入前脱敏；添加假 Token 泄漏回归，确认秘密未进入日志、报告和 UI。
10. 实现 Tool 关闭仅暴露 Name、启用后暴露完整 Schema 的 Context 快照与回归测试。
11. 实现白名单自动执行、非白名单确认卡、仅本次执行、加入白名单、取消、超时、重复提交和强制确认不可绕过的安全案例。
12. 实现网页 / PDF / 邮件 / Tool Result Prompt Injection Fixture 与无 secret read、无外发 Tool Call 的断言。
13. 实现失败聚类、根因记录、前后 Run 比较和 Release Gate；安全硬失败使最终结论 BLOCKED。
14. 实现 Trust Center 后端 API，提供 Suite、Run、Case、Trace、Safety Policy、Gate 和证据查询与操作。
15. 实现 `Evals`、`Traces`、`Safety` 三个前端页签，覆盖加载、空、运行中、失败、比较、过滤、跳转和窄屏状态。
16. 生成 `evals/job-research-cases.yaml` 与 `evals/job-research-safety-cases.yaml`，至少 12 条并覆盖六层分组和第 8 阶段权限回归。
17. 运行固定基线两次，保留版本、Run ID、结果、失败簇、成本与 Trace；修复一个失败簇后重跑全部回归并比较。
18. 运行真实模型与 Playwright MCP 的公开 JD Smoke，保留 source_url 与 Trace，单独报告且不混入固定基线。
19. 生成 `docs/job-research-trust-acceptance.md` 所需证据，并检查仓库、日志、报告与截图无真实秘密或个人敏感正文。
20. 对失败持续诊断和修复；不得停在代码完成、组件测试、Mock 全绿、页面能打开或模型口述能力。

每个 Task 必须可以独立执行与验收，并按依赖顺序排列。

输出 `job-research-trust-task.md` 后停止，等待我确认计划。

当我明确说“确认计划，开始执行”后，再按 Task 顺序小步实现。每完成一个 Task，汇报修改文件、运行测试、验收结果与剩余风险。
---END---
