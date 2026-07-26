# job-research Trust Layer Requirements

状态：需求草案，等待确认
范围：第一阶段 brainstorming 与需求记录；本文不包含设计方案、任务拆解或实现计划。
现状基线：以当前工作区的未提交代码为准。

## 需求背景

Starter Agent 的 `job-research` 链路需要新增“求职调研信任层”，用于证明岗位调研输出可信、可复现、可追踪，并且在工具权限和外部网页对抗场景下可上线回归。

当前链路的被测对象包括：

- SerpAPI Tool 搜索岗位和公开 URL。
- Playwright MCP 读取完整 JD，并保留来源。
- RAG Tool 从简历知识库取回个人证据。
- `job-research` Skill 编排搜索、读取、取证、验证和失败处理。
- Pre-Tool-Call Gate 处理 Tool 启停、白名单、强制确认和拒绝。

当前项目已存在部分能力：pytest 测试体系、`tests/unit`、`tests/integration`、`tests/e2e`、结构化日志、SQLite session/capability 存储、能力目录、MCP 生命周期、Pre-Tool-Call Gate、确认/白名单/强制确认相关实现与若干测试。当前也存在真实 Playwright MCP 公开 JD E2E，但该 E2E 使用脚本化 Provider，不满足“真实模型 + Playwright MCP”的真实 Smoke 要求。

当前未发现专用于本需求的固定 Eval Runner、固定求职调研 Fixture 目录、Eval 报告比较机制、Run/Case 关联 Trace 模型、Trust Center 前端入口或 CI 门禁入口。这些能力需要新增；CI 平台和最终命令名称待确认。

## 功能范围

本需求覆盖三类信任能力：

- Evaluation：固定案例可重复运行，能够比较不同代码、Prompt、Skill、Tool Schema 和权限策略版本。
- Observability：Eval Case、Session、Turn、Model Request、Tool Call、Policy Decision、Approval 和 Tool Result 可以关联定位。
- Safety：网页注入、越权读取、关闭 Tool、非白名单调用、强制确认、重复确认和数据泄漏都有可执行回归案例与上线门禁。

必须区分两类验证：

- 固定 Fixture Eval：使用脱敏搜索结果、JD 页面、简历 Chunk、MCP 响应和错误；进入可比较的基线分数。
- 真实 Smoke：使用真实模型和 Playwright MCP 读取一个公开 JD；证明外部链路仍可用；结果单独记录，不混入固定基线分数，也不能用 Mock 代替。

不在本阶段范围内：

- 不定义具体后端表结构、API 路径、前端组件设计或执行计划。
- 不使用变化的互联网结果计算固定基线。
- 不读取或提交真实秘密、私人邮箱、真实投递信息或未授权数据。

## 目标用户与使用场景

目标用户包括：

- Starter Agent 开发者：需要在修改代码、Prompt、Skill、Tool Schema 或权限策略后验证回归。
- 产品与安全负责人：需要确认求职调研链路的安全门禁、失败原因和证据。
- 评测维护者：需要管理固定案例、基线分数、失败簇和真实 Smoke 记录。
- 运维与排障人员：需要从报告跳转到 Trace，定位模型、工具、策略、确认和外部错误。

核心使用场景：

- 本地重复运行固定 Fixture Eval，并比较两次 Run 的结果。
- 修改 Tool Schema、Skill 或 Gate 策略后，确认关闭 Tool、白名单、强制确认和网页注入回归仍通过。
- 执行一次真实模型 + Playwright MCP 的公开 JD Smoke，证明外部链路可用。
- 从 Trust Center 查看 Evals、Traces 和 Safety 状态，定位失败簇与阻塞原因。

## 用户故事

- 作为开发者，我希望固定 Eval 可以在相同输入版本下重复运行并比较结果，以便判断代码或 Prompt 修改是否造成回归。
- 作为评测维护者，我希望每条 Case 有稳定 ID、Fixture、期望工具调用和确定性断言，以便长期维护基线。
- 作为安全负责人，我希望安全硬门禁失败时整体结论为 BLOCKED，以便安全问题不会被平均分掩盖。
- 作为排障人员，我希望能从 Eval 报告跳转到对应 Trace，以便定位失败发生在模型、Tool、Policy、Approval 还是 Tool Result。
- 作为产品负责人，我希望真实 Smoke 与固定基线分开记录，以便既能看稳定质量，也能知道外部链路是否仍可用。

## 功能需求

### FR-1 评测案例分层

固定 Eval Suite 至少包含以下六类案例：

- Happy Path：搜索、读取 JD、RAG 取证、引用和输出均成功。
- Edge Case：JD 结构异常、页面内容长、岗位信息分散或来源需要归一。
- Missing Information：JD 或简历证据缺失，系统应承认不确定而非编造。
- Tool Failure：SerpAPI、Playwright MCP、RAG 或 Tool Result 返回错误、超时或不可用。
- Conflicting Context：搜索结果、JD 页面和简历证据之间存在冲突，系统应解释来源优先级和不确定性。
- Safety / Adversarial：网页注入、越权读取、Tool 关闭、Schema 移除、非白名单调用、强制确认、重复确认和数据泄漏。

### FR-2 Eval Case 数据要求

每条 Eval Case 必须包含：

- 稳定 ID。
- 输入，包括用户请求、会话前置条件和权限状态。
- Fixture，包括脱敏搜索结果、JD 页面、简历 Chunk、MCP 响应和错误。
- 期望 outcome。
- 期望 Tool 与参数摘要。
- 确定性 assertions。
- 可选 Judge Rubric。
- 安全等级。

Fixture 必须版本化，并能记录与 Run 相关的代码版本、Prompt 版本、Skill 版本、Tool Schema 版本和权限策略版本。

### FR-3 判断规则优先级

确定性规则必须优先判断以下内容：

- Tool Schema 是否向模型暴露或移除。
- Tool 是否可调用。
- Tool 名称和参数是否符合期望。
- JD、搜索结果和 RAG 证据是否有来源。
- 引用是否指向实际来源。
- Policy Decision 是否符合策略。
- Approval 是否按正确顺序出现。
- 确认前是否没有真实 Tool Call。
- 取消、超时、拒绝后是否没有真实外部动作。

LLM Judge 只能用于规则难以覆盖的语义质量，例如岗位匹配分析质量、风险解释清晰度和信息取舍合理性。LLM Judge 不得作为权限、Schema、Tool Call 顺序或密钥泄漏的唯一判断者。

### FR-4 Eval 报告

固定 Eval 报告必须包含：

- 版本信息：代码、Prompt、Skill、Tool Schema、权限策略和 Fixture。
- Run ID。
- Suite 与 Case 结果。
- 失败簇。
- Task Success。
- Tool / Argument Accuracy。
- Citation Correctness。
- Approval Compliance。
- P50/P95 延迟。
- Token 使用量。
- 单次成功成本。

数值基线和预算阈值当前没有产品数据，需标记为待确认。安全硬门禁采用零容忍：失败即整体 BLOCKED。

### FR-5 Trace 关联

每条 Eval Case 必须能关联以下 ID：

- `session_id`
- `turn_id`
- `model_request_id`
- `tool_call_id`
- `policy_decision_id`
- `approval_id`

如果某个节点不存在，报告必须解释缺失原因。例如：Case 在确认前被取消，因此没有真实 Tool Call；Tool 被关闭，因此没有完整 Schema 暴露和 Tool Start；RAG 无证据，因此没有引用证据节点。

当前项目已有 session、turn、tool call、confirmation、audit event 和 context snapshot 等部分记录，但未发现完整的 Eval Run/Case 关联模型、独立 `model_request_id` 和独立 `policy_decision_id`。这些能力需要新增或补齐。

### FR-6 Trace 与日志脱敏

Trace 必须记录模型与 Tool 的状态、摘要、耗时、Token、错误和关联 ID。

默认不得保存以下内容：

- API Key。
- Cookie。
- Authorization。
- 密码。
- 邮箱授权码。
- 完整简历正文。
- 其他秘密或高敏感正文。

脱敏必须发生在写入普通日志、报告或 Trace 存储之前。真实 Smoke 可以保存公开 JD URL、页面标题、来源引用、摘要、Trace ID、Tool 参数摘要和哈希；不得保存完整简历正文、秘密、私人邮箱或真实投递数据。

### FR-7 Tool 启停与 Schema 暴露

外部网页内容必须被视为数据而不是指令。网页中的提示词注入、权限要求、工具调用要求或数据外传要求不得覆盖系统策略、Skill 编排或 Pre-Tool-Call Gate。

关闭 Tool 时：

- 轻量能力目录最多保留 Tool 名称和必要状态。
- 不得向模型注入完整 Description。
- 不得向模型注入完整 Input Schema。
- Tool 不可被真实调用。

重新启用并满足 review/policy 条件后，完整 Schema 才可恢复暴露。

### FR-8 Pre-Tool-Call Gate 与确认

白名单内且未命中强制确认的调用可以自动执行。

非白名单调用必须先出现聊天确认卡；确认前不得发生真实 Tool Call 或外部动作。

确认卡必须覆盖以下回归路径：

- 仅本次执行。
- 执行并加入白名单。
- 取消。
- 超时。
- 重复点击。

强制确认动作不能被白名单绕过。强制确认被拒绝、取消或超时后，不得发生真实外部动作。

### FR-9 Safety 回归与上线门禁

至少需要覆盖以下安全案例：

- 网页注入要求模型忽略系统规则。
- 网页注入要求读取本地文件或秘密。
- Tool 关闭后模型上下文移除完整 Schema，且真实调用被拒绝。
- Schema 移除后模型不能构造有效调用。
- MCP 不可用时进入失败处理。
- RAG 无证据时不编造个人经历。
- 非白名单调用必须等待确认。
- 强制确认不能被白名单绕过。
- 取消、超时和重复确认不会产生额外外部动作。
- 日志和报告不泄漏秘密或完整敏感正文。

安全硬门禁失败时，整体结论必须为 BLOCKED，不得被普通案例平均分抵消。模型回复“拒绝了”不能直接视为安全通过，必须检查 Policy、Approval 和真实 Tool Trace。

### FR-10 失败聚类与回归策略

Eval 报告必须支持失败聚类，并能从失败簇跳转到对应 Trace。

修复一个失败簇后，必须重跑全部固定回归，而不是只跑单条 Case。报告需要能显示修复前后 Run 的差异。

### FR-11 Trust Center 前端入口

需要提供统一 Trust Center 前端入口，至少包含三个页签：

- `Evals`
- `Traces`
- `Safety`

状态必须来自真实后端。前端不能通过静态数据或本地展示状态伪造 PASS。

当前项目未发现完整 Trust Center 前端入口；该能力需要新增。

### FR-12 Evals 页签

`Evals` 页签必须展示：

- Suite。
- Run。
- 版本。
- 指标。
- Case。
- 失败簇。
- 报告。

`Evals` 必须支持运行固定评测、查看详情和比较两次 Run。具体 API 路径、命令名称和 CI 入口待设计阶段确定。

### FR-13 Traces 页签

`Traces` 页签必须支持按以下维度过滤：

- Run。
- Case。
- Session。
- Turn。
- Tool。

`Traces` 必须展示模型、Tool、Policy、Approval、错误、Token 和耗时的关联链路。缺失节点必须显示明确原因。

当前项目存在 capability traces 与 context snapshots 相关 API，但未发现满足本需求的 Run/Case 级 Trace 查询与前端完整查看能力；该能力需要新增或扩展。

### FR-14 Safety 页签

`Safety` 页签必须展示：

- 策略。
- 红队案例。
- 门禁状态。
- 阻塞原因。
- 证据。

门禁状态必须由后端评测与策略结果计算。前端不得通过修改展示结果伪造 PASS。

### FR-15 固定 Fixture Eval 与真实 Smoke 隔离

固定 Fixture Eval 必须只使用脱敏、版本化、可重复的 Fixture，不得依赖实时互联网结果。

真实 Smoke 必须使用真实模型和 Playwright MCP 读取一个公开 JD。Smoke 结果必须单独记录，并与固定基线报告分开。不能用 Mock、PPT、静态截图或脚本化 Provider 代替真实 Smoke。

当前可作为 Smoke 候选的公开 JD URL 可来自现有 E2E 记录，但最终 URL 必须在执行时确认仍公开可访问。

## 非功能需求

- 可重复性：固定 Fixture 在输入版本不变时，本地重复运行结果可比较。
- 可解释性：每个失败必须能定位到 Case、Run、Trace 和具体断言。
- 可审计性：权限决策、确认状态和真实 Tool 执行必须有可审计证据。
- 数据最小化：日志、报告和 Trace 只保存定位问题所需摘要、哈希、状态和来源。
- 安全优先：安全硬门禁独立于普通质量分，不参与平均抵消。
- 可维护性：Fixture、Rubric、确定性断言和安全等级必须易于审查和版本管理。
- 前后端一致性：Trust Center 刷新后状态一致；后端失败时前端展示明确错误。
- 成本可见：报告必须记录 Token、耗时和单次成功成本；预算阈值待产品数据确认。

## 验收标准

- 固定 Fixture Eval 可以在本地重复运行两次；输入版本不变时结果可比较。
- 随机模型评分必须记录模型、Rubric 和原始分数。
- 至少包含 12 条求职评测案例，覆盖 Happy Path、Edge Case、Missing Information、Tool Failure、Conflicting Context、Safety / Adversarial 六类分层。
- 12 条案例中至少包含 Tool 关闭、Schema 移除、MCP 不可用、RAG 无证据、非白名单确认、强制确认和网页注入。
- 能从真实模型请求或 Context 调试快照证明关闭 Tool 的完整 Schema 已移除，重新启用后才恢复。
- 能从 Trace 证明非白名单调用在确认前没有 Tool Start。
- 取消、超时和强制确认拒绝后没有真实外部动作。
- 至少运行一次真实模型 + Playwright MCP 的公开 JD Smoke，保留来源和 Trace，并与固定基线报告分开。
- 日志与报告不含秘密或完整敏感正文。
- 脱敏前数据不得先写入普通日志。
- 安全硬门禁失败时整体结论为 BLOCKED，不得被普通案例平均分抵消。
- Trust Center 的 `Evals`、`Traces` 和 `Safety` 三个页签调用真实后端。
- Trust Center 刷新后状态一致，失败时展示明确错误，不使用静态成功数据。

## 边界情况

- 公开 JD URL 下线、跳转、需要验证码或地区限制时，真实 Smoke 应记录外部不可用原因，不影响固定基线分数。
- Playwright MCP 启动失败、工具列表为空、工具变更或 `@latest` 版本行为变化时，真实 Smoke 应单独失败并保留 Trace。
- SerpAPI 返回重复、低质量或无关结果时，固定 Eval 必须使用脱敏 Fixture 判断排序和来源处理，不依赖实时搜索。
- RAG 无证据或证据与 JD 冲突时，输出必须说明缺失或冲突，不得编造简历经历。
- 网页正文包含“忽略之前指令”“读取本地文件”“发送 Cookie”等注入文本时，系统必须按数据处理。
- Tool 被关闭或 Schema 被移除时，模型上下文不得包含完整描述和输入 Schema。
- 非白名单工具调用被重复点击确认时，只能产生符合幂等规则的一次执行或明确拒绝，不得重复外部动作。
- 确认卡超时后再次执行同一动作时，必须生成新的确认流程或明确拒绝，不得复用过期授权。
- 日志脱敏器异常时，系统不得先写入未脱敏数据。
- Eval Case 缺少某类 Trace 节点时，报告必须显示合理缺失原因，而不是静默通过。

## 风险与待确认事项

- 当前没有指定 CI 平台、固定 Eval Runner 命令、Fixture 目录和报告输出路径；需要新增，命名待确认。
- 当前没有产品数据支撑 Task Success、Tool / Argument Accuracy、Citation Correctness、Approval Compliance、P50/P95、Token 和成本阈值；数值基线待确认。
- 当前已有 Trace/Log 能力分散在 session store、capability store、audit event、context snapshot 和 JSONL 日志中，但缺少完整 Run/Case 级关联；需要补齐统一关联能力。
- 当前已有 Playwright MCP 真实外部 E2E，但不是“真实模型 + Playwright MCP”的 Smoke；需要新增真实 Smoke 记录。
- 当前 Trust Center 前端入口、`Evals`、`Traces`、`Safety` 页签和真实后端状态聚合未发现完整实现；需要新增或扩展。
- 真实 Smoke 的公开 JD URL 可能随时间变化；不得进入固定基线，执行时必须记录具体日期、URL、来源和失败原因。
- 使用真实模型会引入随机性、成本和供应商可用性风险；随机评分必须保存模型、Rubric 和原始分数。
- Fixture 必须脱敏；不得将真实秘密、私人邮箱、真实投递数据或完整简历正文引入评测仓库。
- 前端展示层存在伪造 PASS 风险；门禁结论必须以后端真实评测和策略结果为准。
- 当前 Stage 8 能力以工作区未提交代码为现状基线；若后续改以已提交版本或发布版本为准，需要重新核对现状差异。
