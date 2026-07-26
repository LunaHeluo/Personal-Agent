# MCP 与 job-research Skill 验收报告

验收时间：`2026-07-26T19:58:38+08:00`

最终结论：**PARTIAL**

本结论基于代码、配置、测试和真实外部调用，不采用文档自述、Mock
Server、硬编码网页内容或模型口述作为真实链路证据。真实 Playwright MCP
端到端链路已经通过，但本轮仍缺少一条把真实 SerpAPI 候选、用户选择、
Browser 读取和 RAG 串联起来的连续 Skill Trace，也没有完成全部管理操作的
真实 UI 点击记录和像素级窄屏检查，因此不能判定为 PASS。

## 验收范围与方法

- 阅读并对照：`docs/job-research-requirements.md`、
  `docs/job-research-design.md`、`docs/job-research-task.md`、
  `docs/capability_catalog.md` 和 `job-research` Skill。
- 审查 MCP 配置加载、Client/Manager、能力发现、单 Server 刷新、Registry、
  Context 快照、Pre-Tool-Call Gate、确认状态机、结果裁剪、Trace、能力 API
  和生产 HTML。
- 运行 101 项 MCP/Gate/确认/UI/Skill 聚焦测试。
- 运行真实 `npx @playwright/mcp@latest` external E2E。
- 通过真实 `AgentRuntime -> PreToolCallGate -> UnifiedToolExecutor` 路径调用
  `search_jobs_serpapi`。
- 运行完整 pytest 回归并执行高置信凭据扫描。

## 通过项

### 1. 配置、进程与真实能力发现

- Starter Agent 从 `config/config.yaml` 的 `mcp.config_path` 解析并加载
  `config/mcp.json`。
- 实际配置保持为：

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest"]
    }
  }
}
```

- 真实运行信息：

| 字段 | 实测值 |
| --- | --- |
| transport | `stdio` |
| Node | `v22.14.0` |
| npx | `10.9.2` |
| Server name | `Playwright` |
| Server version | `1.62.0-alpha-1783623505000` |
| MCP protocol | `2025-11-25` |
| 连接/健康状态 | `ready` / `healthy` |
| stderr 摘要 | 空字符串 |
| 运行中 exit code | `null` |
| 关闭后状态 | `closed` |
| 关闭后 exit code | `null`，当前 SDK 链路没有返回数值退出码 |

- 真实发现快照：`24` Tools、`0` Resources、`0` Prompts，快照 hash：
  `892b496f1d4dd14d4c106287a630d64d4ed8a9a09b0f70b0857199db0ac5e15c`。
- 新发现 Tool 默认 `enabled=false`、`review_state=unreviewed`，没有发生全量
  暴露或全量 allowlist。
- 最小 JD Tool 定义与 Server 返回一致：

| Tool | Description | Input Schema | Schema hash |
| --- | --- | --- | --- |
| `browser_navigate` | `Navigate to a URL` | object；必填字符串 `url`；`additionalProperties=false` | `2165538e098634780eec628947d795a2619b4d2e3cef0e36d3084ac46abb94f7` |
| `browser_snapshot` | `Capture accessibility snapshot of the current page, this is better than screenshot` | object；可选 `target:string`、`filename:string`、`depth:number`、`boxes:boolean`；`additionalProperties=false` | `36ee5bbb5798a52e26015635e1f6015b8f4b62f44119d53ad2516837667fcd61` |

### 2. 单 Server 刷新、启停与 Context 隔离

- `test_refresh_is_per_server_and_leases_pin_client_generation` 等测试证明刷新
  只替换目标 Server 的 Client generation，运行中调用绑定原 generation。
- 刷新失败保留上一份活动快照、标记 stale 并记录错误；并发刷新返回稳定
  冲突状态。
- 真实 E2E 完成 `discover -> refresh`，快照从 version 1 更新到 version 2，
  其他能力没有被全局刷新。
- 真实 Runtime Context 调试快照证明：关闭 `browser_snapshot` 后，下一次
  Provider 请求中该 Tool 的完整定义消失；重新启用并批准后才恢复。证据来自
  Provider 实际收到的 tools 数组，不只依据前端开关。
- 关闭项只保留轻量名称、Server、启用/可调用状态；完整 Description 和
  Input Schema 不进入 callable model tools。

### 3. Gate、allowlist 与人工确认

- 所有真实 Browser 调用均经 `PreToolCallGate` 和
  `UnifiedToolExecutor`，MCP Manager 注册的 invoker 不直接对模型暴露。
- 真实 JD E2E 中，`browser_navigate` 和 `browser_snapshot` 在确认前均无
  `tool.invoked` 审计；一次性确认后各执行一次。
- 聚焦测试覆盖：
  - allowlist 内普通调用自动执行；
  - 非 allowlist 调用产生确认记录；
  - “仅本次执行”只能消费一次；
  - “执行并加入白名单”只生成匹配 Server、Tool、Schema、动作和目标范围的规则；
  - always-confirm 不能降级成自动 allowlist；
  - 取消、超时、刷新恢复、重复提交和并发消费不会执行或重复执行；
  - 禁用、Schema 不匹配、域名越界和敏感外发数据被拒绝。
- 生产 HTML 的确认卡显示 Server、Tool、参数摘要、风险、数据去向、过期时间、
  Audit 和 Trace，并将三种选择提交到后端确认 API。

### 4. 真实 Playwright JD、RAG 与入库

- external E2E 真实访问：
  `https://jobs.lever.co/payugpo/49975338-7270-422e-a3c1-e2375394cef4`。
- 页面内容来自 Playwright MCP 的真实 `browser_navigate` 和
  `browser_snapshot` Tool Result。
- 真实结构化结果包含岗位标题、公司 `PayU GPO`、地点、职责和要求；来源 URL
  与 Artifact 保持一致。
- 本次 E2E 快照：`playwright-snapshot-2`，version `2`，24 Tools。
- Browser Artifact：
  - content hash：
    `8e8032ecb4ebd75d9705c42054ac581ab3ff53d414c6b324ce16a2d9d8eaf628`
  - Trace：
    `trace:c4088b90-9a56-47c2-acc6-624eab2684f8:484982c8-398a-4e9a-87ab-1deadfb9f416:task16-runtime-snapshot`
  - 简历引用：`task16-public-resume-fixture.md@v1#L5-L5`
- `JobResearchOrchestrator.analyze` 的真实执行 Trace 顺序为：导航、页面快照、
  `retrieve_resume_evidence`；匹配项带 `source_ref`，缺口不伪造证据。
- JD 在确认前无法入库；确认后创建 `job_description` 文档并能从知识库检索到
  版本化引用。

### 5. 真实 SerpAPI

- 通过项目真实 Runtime、Gate 和 Executor 调用 `search_jobs_serpapi`，退出码 0。
- 参数：`machine learning engineer`、`Shanghai`、limit 3。
- 返回 3 条公开来源，engine 为 `google`，检索时间：
  `2026-07-26T11:39:55.966045+00:00`。
- Trace：
  `trace:8d34b4a2-9f2e-4033-b642-96d92fced9ce:8c979c5c-ca08-4d2a-baeb-112b5d3d2c51:acceptance-serpapi-search`。
- Audit：`audit-3de54f89e09b4c1eb922deef2d18f717`；实际 invoker 次数为 1。
- 输出和日志只包含凭据 profile/env 名称，不包含 API Key 内容。

### 6. 失败与降级

- 真实不可用场景使用不存在的本地 command 启动 Manager，得到
  `connection_state=failed`、`health_state=unhealthy` 和真实 transport 错误；
  没有活动快照、Browser Tool Result、匹配结论或 JD 入库。
- 自动化测试分别覆盖 Server 不可用、Tool 缺失、Schema 无效、页面拒绝、
  Browser 超时、结果裁剪和来源保留。
- 超长结果先脱敏再裁剪，并保留 `truncated`、原始长度/hash、`raw_source_ref`
  和来源 URL。
- 外部能力失败不会阻断普通核心对话，且不会生成虚假 Tool Result。

### 7. Skill 行为

- `job-research` Skill 位于
  `src/starter_agent/skills/job-research/SKILL.md`，Loader 已使用该真实目录。
- 正确岗位调研请求可被 selector 选中；通用求职建议、单纯翻译或润色不会误触发。
- Preconditions 要求岗位/城市/关键词和简历知识库状态；输入不足时先询问。
- 多候选时要求用户选择，不自行读取任意候选。
- Skill 明确保留 JD URL、简历 Chunk、未验证字段、裁剪与 Tool Trace，并禁止
  自动投递、登录、上传、发送和绕过站点限制。

### 8. 安全、文档和回归

- `.env` 未被 Git 跟踪。
- 对 `docs`、`src`、`tests`、`config` 的高置信凭据模式扫描无命中。
- `git diff --check` 无空白错误；仅有 Git 的 LF/CRLF 提示。
- `docs/capability_catalog.md` 已从过期的 `not_discovered` 更新为本次真实发现
  证据，并明确静态文档不能授权或永久放行 Tool。
- 101 项聚焦测试全部通过。
- external E2E：`2 passed in 38.83s`。
- 能力目录定向回归全部通过。
- 最终完整 pytest 回归到 100%，退出码 0；只有既有
  Starlette/httpx 弃用警告。

## 失败项

以下是验收证据未满足项，不代表真实 Playwright MCP 页面读取失败：

1. **没有一条连续的真实 job-research Trace 同时串联 SerpAPI、候选选择、
   Browser 和 RAG。** SerpAPI 真实调用和 Browser/RAG 真实 E2E 分属两次运行；
   Browser E2E 使用固定公开 Lever URL，并非本次 SerpAPI 返回候选之一。
2. **能力管理真实 UI 操作证据不完整。** 真实 Playwright 已打开生产 HTML，
   看到了真实 `playwright` Server 和 `browser_snapshot` Tool，但没有逐项点击并
   留存连接、断开、Server/Tool 启停、健康检查、单 Server 刷新、Skill 重新加载、
   allowlist 扩大和失败回滚的 UI/API/Trace 组合记录。
3. **真实窄屏视觉验收未完成。** CSS 合约和窄屏规则测试通过，但没有保存真实
   浏览器在桌面/窄屏下的 bounding-box 或截图证据，不能仅凭 CSS 文本断言无重叠。
4. **子进程数值退出码不可观察。** Manager 能完成初始化并干净进入 `closed`，
   stderr 为空，但当前 SDK 传输层在运行中和关闭后都返回 `exit_code=null`，未满足
   “记录实际数值退出码”的最强解释。

## 未执行项及原因

- 未使用真实云模型自主选择 SerpAPI 候选。Schema 移除/恢复使用真实 Runtime
  Context 调试快照和 scripted Provider；验收条款允许 Context 调试快照，但这不能
  证明任意云模型会稳定遵循候选选择交互。
- 未访问登录页、上传文件或提交申请。这些动作属于禁止或强制确认边界，不应为
  验收而对真实招聘站点产生副作用；其不可绕过行为由 Gate/确认矩阵测试验证。
- 未对真实站点制造超时、拒绝访问或超长页面故障，以免绕过限制或进行不必要的
  外部压力；这些状态通过集成测试的受控错误结果验证。
- 未提交 Git commit；本轮请求只要求生成验收文档。

## 可重现命令或用户操作路径

Windows 受限环境可能需要允许 pytest 在沙箱外创建临时目录，否则 pytest 的
`tmp_path` 会出现 `WinError 5`，这不是产品测试失败。

```powershell
# 聚焦治理回归
uv --cache-dir .uv-cache run --isolated --frozen --offline --extra dev `
  python -m pytest `
  tests/unit/test_mcp_config.py `
  tests/integration/test_mcp_refresh_isolation.py `
  tests/integration/test_model_request_tool_exposure.py `
  tests/integration/test_tool_confirmation_matrix.py `
  tests/integration/test_job_research_degradation.py `
  tests/integration/test_job_research_orchestration.py `
  tests/unit/test_job_research_skill.py -q

# 真实 Playwright MCP E2E
uv --cache-dir .uv-cache run --isolated --frozen --offline --extra dev `
  python -m pytest tests/e2e/test_playwright_job_research.py `
  -m external -vv -s

# 完整回归
uv --cache-dir .uv-cache run --isolated --frozen --offline --extra dev `
  python -m pytest -q

# 启动真实应用后手工复核
uv run agent serve
```

手工路径：打开 `http://127.0.0.1:8000`，进入“能力管理” → `MCP Servers`，
选择 `playwright`；检查版本、状态、发现快照和 Schema。再切换 `Skills` 查看
`job-research`，最后在对话中发起岗位调研并观察确认卡、Tool Trace、JD URL 和
简历 Chunk 引用。

## 证据文件路径

- `config/mcp.json`
- `src/starter_agent/mcp/client.py`
- `src/starter_agent/mcp/manager.py`
- `src/starter_agent/mcp/discovery.py`
- `src/starter_agent/capabilities/gate.py`
- `src/starter_agent/capabilities/confirmations.py`
- `src/starter_agent/agent/runtime.py`
- `src/starter_agent/skills/job-research/SKILL.md`
- `src/web/index.html`
- `tests/e2e/test_playwright_job_research.py`
- `tests/integration/test_mcp_refresh_isolation.py`
- `tests/integration/test_model_request_tool_exposure.py`
- `tests/integration/test_tool_confirmation_matrix.py`
- `tests/integration/test_job_research_degradation.py`
- `tests/integration/test_job_research_orchestration.py`
- `tests/integration/test_capability_ui_api_contract.py`
- `tests/unit/test_capability_ui_contract.py`
- `tests/unit/test_job_research_skill.py`
- `docs/capability_catalog.md`
- `docs/job-research-acceptance.md`
- 本报告：`docs/mcp-skill-acceptance.md`

每次 external E2E 的 SQLite Store、Artifact 和确认记录位于 pytest 隔离临时目录，
测试结束后清理；本报告只保留非敏感 hash、Trace/Audit 引用和公开 URL。

## 剩余风险

- `@latest` 会随 npm 发布变化，当前运行版本和 Schema hash 不能代表未来运行；
  每次刷新必须重新发现、审查和生成 Context 快照，后续应在验证后考虑固定版本。
- 真实招聘页面可能下线、改版或增加访问限制；Lever URL 不是永久测试资产。
- 当前真实 E2E 使用 scripted Provider 驱动确定性 Tool Call，未覆盖真实云模型在
  多候选跨轮交互中的稳定性。
- 真实 UI 全操作和窄屏视觉证据缺失，是本次不能判为 PASS 的主要原因。
- SDK 未暴露数值进程退出码；若运维要求精确退出码，需要扩充 transport/process
  可观测性，同时保持 stderr 脱敏和有界存储。

## 最终结论

**PARTIAL**

真实 Playwright MCP 的配置加载、stdio 进程、initialize、能力发现、刷新、Gate、
确认、公开 JD Tool Result、来源 URL、RAG 引用、确认入库、UI 状态读取和不可用
降级均已真实跑通；完整回归通过。但连续三能力 Skill Trace、全部真实 UI 管理操作
以及真实窄屏视觉证据尚未完成，故不满足 PASS 的全部门槛。
