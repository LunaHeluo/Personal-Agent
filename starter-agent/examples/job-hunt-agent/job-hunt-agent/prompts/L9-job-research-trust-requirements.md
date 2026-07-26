# L9 · 求职调研信任层需求文档

用途：为求职 Agent 的 Evaluation、Observability 与 Safety 能力生成 `job-research-trust-requirements.md`。本提示词只做 brainstorming 和需求文档，不做设计、不生成任务计划、不修改代码。

---BEGIN---
你是我的 Agent 功能开发协作伙伴。请使用中文工作。

现在要为 Starter Agent 新增「求职调研信任层」，被测对象是现有 `job-research` 链路：

- SerpAPI Tool 搜索岗位和公开 URL。
- Playwright MCP 读取完整 JD，并保留来源。
- RAG Tool 从简历知识库取回个人证据。
- `job-research` Skill 编排搜索、读取、取证、验证和失败处理。
- Pre-Tool-Call Gate 处理 Tool 启停、白名单、强制确认和拒绝。

信任层必须同时解决：

1. Evaluation：固定案例可重复运行，能够比较不同代码、Prompt、Skill、Tool Schema 和权限策略版本。
2. Observability：Eval Case、Session、Turn、Model Request、Tool Call、Policy Decision、Approval 和 Tool Result 可以关联定位。
3. Safety：网页注入、越权读取、关闭 Tool、非白名单调用、强制确认、重复确认和数据泄漏都有可执行回归案例与上线门禁。

必须区分两类验证：

- 固定 Fixture Eval：使用脱敏搜索结果、JD 页面、简历 Chunk、MCP 响应和错误，进入可比较的基线分数。
- 真实 Smoke：使用真实模型和 Playwright MCP 读取一个公开 JD，证明外部链路仍可用；结果单独记录，不混入固定基线分数，也不能用 Mock 代替。

第一阶段只做 brainstorming 和 `job-research-trust-requirements.md`，不要写设计，不要生成任务计划，不要修改任何文件。

请先检查项目现状，再向我提出最多 5 个必要问题，优先确认：

1. 当前 Eval Runner、测试目录、Fixture 格式、测试命令和 CI 入口；不存在时明确需要新增。
2. 当前 Trace / Log 的 ID、事件、存储、脱敏和前端查看能力。
3. 第 8 阶段 MCP Server / Tool 启停、轻量能力目录、完整 Schema 暴露和 Pre-Tool-Call Gate 的真实实现状态。
4. 核心指标、护栏指标、预算阈值和安全硬门禁；没有产品数据时先标记待确认，不虚构基线。
5. 可用于固定评测的脱敏求职 Fixture、真实公开 JD Smoke URL，以及允许保存的证据范围。

然后生成 `job-research-trust-requirements.md`，必须包含：

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

- 评测案例分层：Happy Path、Edge Case、Missing Information、Tool Failure、Conflicting Context、Safety / Adversarial。
- 案例包含稳定 ID、输入、Fixture、期望 outcome、期望 Tool / 参数、确定性 assertions、可选 Judge Rubric 和安全等级。
- 确定规则优先判断 Schema、Tool、参数、来源、引用、Policy Decision、Approval 顺序和是否真实执行；LLM Judge 只用于规则难以覆盖的语义质量。
- 报告包含版本、Run ID、案例结果、失败簇、Task Success、Tool / Argument Accuracy、Citation Correctness、Approval Compliance、P50/P95、Token 和单次成功成本。
- 每条 Eval Case 能关联 session_id、turn_id、model_request_id、tool_call_id、policy_decision_id 和 approval_id；缺失节点也要能解释。
- Trace 记录模型与 Tool 的状态和摘要，不默认保存 API Key、Cookie、Authorization、密码、邮箱授权码、完整简历正文或其他秘密。
- 安全案例验证：外部网页是数据不是指令；关闭 Tool 只在轻量能力目录保留名称，不向模型注入完整 Description / Input Schema，也不可调用。
- 白名单内且未命中强制确认的调用可以自动执行；非白名单调用先出现聊天确认卡；确认前没有真实 Tool Call。
- 确认卡的仅本次执行、执行并加入白名单、取消、超时和重复点击都有回归；强制确认动作不能被白名单绕过。
- 支持失败聚类并从报告跳转到对应 Trace；修复一个失败簇后必须重跑全部回归，而不是只跑单条。
- 提供统一「Trust Center」前端入口，至少包含 `Evals`、`Traces` 与 `Safety` 页签，状态来自真实后端。
- `Evals` 展示 Suite、Run、版本、指标、Case、失败簇与报告；支持运行固定评测、查看详情和比较两次 Run。
- `Traces` 支持按 Run / Case / Session / Turn / Tool 过滤，展示模型、Tool、Policy、Approval、错误、Token 和耗时关联链路。
- `Safety` 展示策略、红队案例、门禁状态、阻塞原因和证据；不能通过前端修改展示结果伪造 PASS。

验收标准至少覆盖：

- 固定 Fixture 在本地重复运行两次，输入版本不变时结果可比较；随机模型评分必须记录模型、Rubric 和原始分数。
- 至少 12 条求职评测案例，覆盖六类分层；其中至少包含 Tool 关闭、Schema 移除、MCP 不可用、RAG 无证据、非白名单确认、强制确认和网页注入。
- 能从真实模型请求或 Context 调试快照证明关闭 Tool 的完整 Schema 已移除，重新启用后才恢复。
- 能从 Trace 证明非白名单调用在确认前没有 Tool Start；取消、超时和强制确认拒绝后没有真实外部动作。
- 至少运行一次真实 Playwright MCP 公开 JD Smoke，保留来源和 Trace，并与固定基线报告分开。
- 日志与报告不含秘密或完整敏感正文；脱敏前数据不得先写入普通日志。
- 安全硬门禁失败时整体结论为 BLOCKED，不得被普通案例平均分抵消。
- 前端三个页签调用真实后端；刷新后状态一致，失败时展示明确错误，不使用静态成功数据。

约束：

- 不要臆造项目中不存在的命令、路径、Trace 字段、Tool 名称或前端接口；需要新增时在需求中明确标记。
- 不要用变化的互联网结果计算固定基线，也不要用 Mock 或 PPT 模拟结果代替真实 Smoke。
- 不要把 LLM Judge 当作权限、Schema、Tool Call 顺序或密钥泄漏的唯一判断者。
- 不要为评测读取或提交真实秘密、私人邮箱、真实投递信息或未授权数据。
- 不要把“模型回复拒绝了”直接当作安全通过；必须检查 Policy、Approval 和真实 Tool Trace。

输出 `job-research-trust-requirements.md` 后停止，等待我确认需求。
---END---
