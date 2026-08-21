# MCP 与 job-research Skill 验收报告

验收时间：`2026-07-27T02:10:00+08:00`

最终结论：**PARTIAL**

本次验收真实跑通了 Playwright MCP 端到端公开 JD 读取链路，并定位、修复了“询问岗位时不会自动爬取 JD”的主要产品行为问题。仍未判定为 PASS 的原因是：本轮没有完成真实浏览器 UI 的逐项人工操作截图/窄屏视觉证据，也没有用真实云模型自主完成“SerpAPI 搜索 → 自动多 JD 抓取 → RAG 匹配”的连续线上会话；因此按验收口径保留 PARTIAL。

## 读取范围

用户要求读取的 `job-research-requirements.md`、`job-research-design.md`、`job-research-task.md` 在仓库根目录不存在；实际文件位于：

- `docs/job-research-requirements.md`
- `docs/job-research-design.md`
- `docs/job-research-task.md`

已阅读并审查：

- `docs/job-research-requirements.md`
- `docs/job-research-design.md`
- `docs/job-research-task.md`
- `docs/capability_catalog.md`
- `backend/src/starter_agent/skills/job-research/SKILL.md`
- `config/mcp.json`
- `config/config.yaml`
- `config/prompts/system.md`
- MCP Client / Manager / Discovery / Tool Adapter
- Pre-Tool-Call Gate、allowlist、确认状态机、Tool Trace 相关测试
- 能力管理前端与 API 合同测试

## 询问岗位时无法爬取 JD 的原因与修复

根因是策略层阻止了自动抓取：

- `config/prompts/system.md` 原规则写着：只有用户明确选择搜索结果或提供岗位 URL 后，才调用 `search_job_description`。
- `backend/src/starter_agent/skills/job-research/SKILL.md` 原工作流写着：存在多个候选岗位时停止并请求用户选择。
- 因此当用户问“深圳还有其他网上的岗位吗”时，Agent 合规行为是只搜索/回答候选摘要，而不是继续读取 JD。
- 同时 `job-research` Skill 设计目标是 Playwright MCP 读取 JD；而普通聊天链路中已有内置 `search_job_description` 抓取器。MCP 工具只有在已连接、已发现、已启用、已审查并通过 Gate 后才会进入模型 callable tools，不能仅靠文档声明。

本次已修改：

- `config/prompts/system.md`：岗位搜索后可自动读取最多 3 个公开 JD，不再等待用户额外粘贴 URL。
- `backend/src/starter_agent/skills/job-research/SKILL.md`：多个候选时默认抓取前 3 个公开详情页作为预览；最终匹配、入库或深度分析仍需用户选择。
- `tests/unit/test_job_research_auto_jd_contract.py`：新增合同测试，防止规则回退到“必须用户先选再抓 JD”。

安全边界保持不变：

- 只读取搜索结果返回的公开 HTTP(S) 岗位 URL。
- 不猜测、不构造 URL。
- 不登录、不上传、不提交、不投递。
- 自动抓取的 JD 不自动入库。
- 网页内容只能作为岗位/JD/公司公开信息，不能当作用户个人经历证据。
- 简历匹配仍必须引用知识库 Chunk；无证据必须标记缺口。

## 通过项

### 1. MCP 配置与发现

`config/config.yaml` 中 `mcp.config_path` 指向 `config/mcp.json`。实际配置为：

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": [
        "@playwright/mcp@latest"
      ]
    }
  }
}
```

真实 external E2E 运行结果：

- 命令：`.\\.venv\\Scripts\\python.exe -m pytest tests/e2e/test_playwright_job_research.py -m external -vv -s -p no:cacheprovider --basetemp=.tmp-pytest-playwright-external-elevated`
- 结果：`2 passed in 94.51s`
- 运行时：`Playwright`
- 包版本：`1.62.0-alpha-1783623505000`
- MCP protocol：`2025-11-25`
- Node：`v22.14.0`
- npx：`10.9.2`
- 发现结果：`24 Tools, 0 Resources, 0 Prompts`
- Snapshot：`playwright-snapshot-2`
- Snapshot hash：`892b496f1d4dd14d4c106287a630d64d4ed8a9a09b0f70b0857199db0ac5e15c`
- 进程 stderr：测试输出未出现敏感 stderr；SDK 仍未暴露数值型 exit code。

最小 JD 工具证据：

- `browser_navigate`
  - schema hash：`2165538e098634780eec628947d795a2619b4d2e3cef0e36d3084ac46abb94f7`
- `browser_snapshot`
  - 真实用于读取公开 JD。

### 2. 最小权限、Gate 与确认

相关回归通过：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/test_job_research_auto_jd_contract.py `
  tests/unit/test_job_research_skill.py `
  tests/integration/test_search_job_description_flow.py `
  tests/integration/test_job_research_orchestration.py `
  tests/integration/test_job_research_degradation.py `
  tests/integration/test_model_request_tool_exposure.py `
  tests/integration/test_tool_confirmation_matrix.py `
  tests/unit/test_mcp_config.py `
  tests/unit/test_mcp_client_lifecycle.py `
  tests/unit/test_mcp_health.py `
  tests/unit/test_mcp_tool_adapter.py `
  tests/unit/test_browser_scope_policy.py `
  tests/unit/test_pre_tool_call_gate.py `
  -q -p no:cacheprovider --basetemp=.tmp-pytest-mcp-skill-related-elevated
```

结果：`77 passed`

覆盖证据包括：

- Tool 关闭后完整 schema 不进入 callable model tools。
- 重新启用并通过审查后，下一轮模型请求才恢复 schema。
- allowlist 内普通只读调用可自动执行。
- 非 allowlist 调用确认前无真实 Tool invocation。
- 仅本次执行、加入 allowlist、取消、超时、重复点击、并发消费均不会误执行或重复执行。
- 强制确认不能被 allowlist 绕过。
- 域名越界、禁用项、敏感 URL query、非 HTTP(S) 与敏感外发数据被拒绝。

### 3. 真实公开 JD 读取

真实 external E2E 访问的公开 JD：

`https://jobs.lever.co/payugpo/49975338-7270-422e-a3c1-e2375394cef4`

工具链路：

- `mcp__playwright__browser_navigate`
- `mcp__playwright__browser_snapshot`

运行输出证据：

- session_id：`f2735a94-a777-4217-828c-989ac86b08f7`
- turn_id：`3a34e843-2e3d-4bcd-8d97-751a9759a69f`
- artifact_ref：`tool:mcp__playwright__browser_snapshot:3a34e843-2e3d-4bcd-8d97-751a9759a69f:task16-runtime-snapshot`
- trace_ref：`trace:f2735a94-a777-4217-828c-989ac86b08f7:3a34e843-2e3d-4bcd-8d97-751a9759a69f:task16-runtime-snapshot`
- content_sha256：`8e8032ecb4ebd75d9705c42054ac581ab3ff53d414c6b324ce16a2d9d8eaf628`
- resume_source_ref：`task16-public-resume-fixture.md@v1#L5-L5`
- jd_source_ref：`job-ce1ff0fb212ccd4dc06d4da7.md@v1#L11-L18`

该证据证明 Browser 结果来自 Playwright MCP 的真实 Tool Result，不是 Mock、硬编码页面或模型口述。

### 4. 失败与降级

真实不可用降级测试通过：

```json
{
  "server_id": "unavailable",
  "connection_state": "failed",
  "health_state": "unhealthy",
  "error_code": "transport_error",
  "last_error": "[WinError 2] 系统找不到指定的文件。"
}
```

自动化测试还覆盖：

- Server 不可用。
- Tool 缺失。
- Schema 无效。
- 页面拒绝访问。
- Browser 超时。
- Tool Result 裁剪。
- 外部能力不可用时核心对话继续，且不伪造 Tool Result。

### 5. Skill 行为

通过 `tests/unit/test_job_research_skill.py` 与新增合同测试验证：

- 正确的岗位调研请求会触发 `job-research` Skill。
- 通用求职建议、单纯翻译/润色不会误触发。
- 输入不足时先追问。
- 新规则下，岗位搜索结果已有公开 URL 时，可自动抓取最多 3 个 JD 预览；最终匹配/入库仍需用户选择。

### 6. 能力管理页面

代码与合同测试覆盖：

- 存在统一“能力管理”入口。
- 支持 `MCP Servers` 与 `Skills` 页签。
- 能展示 Server、Tool、Schema、allowlist、健康状态、最近错误。
- 能展示 Skill 名称、描述、来源、文件位置、依赖与健康状态。
- 对话页能渲染待确认 Tool Call，并把用户选择提交到后端 Gate 状态机。

## 失败项

没有发现本次修改导致的自动化测试失败。

但验收口径下仍有未满足项：

1. 未保存真实浏览器 UI 逐项操作截图或窄屏视觉证据。
2. 未运行真实云模型自主完成“SerpAPI 搜索 → 自动抓多个 JD → RAG 匹配”的完整连续会话。
3. SDK 仍未提供数值型 MCP 子进程 exit code；当前只能记录运行态、关闭态、stderr 摘要和 transport 状态。

## 未执行项及原因

- 未执行真实登录、上传、提交、发送等强制确认动作：这些动作属于禁止或高风险边界，不应为了验收对真实招聘站点产生副作用。
- 未对真实招聘站点制造超时/拒绝访问/超长页面压力：使用受控测试覆盖，避免不必要外部压力。
- 未完成完整人工 UI 点击验收：本轮重点是定位并修复“询问岗位时不爬 JD”的行为，以及真实 Playwright MCP 链路验证。

## 可重现命令或用户操作路径

自动抓 JD 规则合同：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_job_research_auto_jd_contract.py -q -p no:cacheprovider --basetemp=.tmp-pytest-auto-jd-green2
```

MCP / Skill / Gate 相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/test_job_research_auto_jd_contract.py `
  tests/unit/test_job_research_skill.py `
  tests/integration/test_search_job_description_flow.py `
  tests/integration/test_job_research_orchestration.py `
  tests/integration/test_job_research_degradation.py `
  tests/integration/test_model_request_tool_exposure.py `
  tests/integration/test_tool_confirmation_matrix.py `
  tests/unit/test_mcp_config.py `
  tests/unit/test_mcp_client_lifecycle.py `
  tests/unit/test_mcp_health.py `
  tests/unit/test_mcp_tool_adapter.py `
  tests/unit/test_browser_scope_policy.py `
  tests/unit/test_pre_tool_call_gate.py `
  -q -p no:cacheprovider --basetemp=.tmp-pytest-mcp-skill-related-elevated
```

真实 Playwright MCP E2E：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_playwright_job_research.py -m external -vv -s -p no:cacheprovider --basetemp=.tmp-pytest-playwright-external-elevated
```

手工路径：

1. 启动 Starter Agent。
2. 打开对话页。
3. 提问：“查询深圳 AI Agent 岗位，并读取 JD”。
4. 预期：Agent 先调用 `search_jobs_serpapi`，再对返回的公开岗位 URL 调用 JD 读取工具，展示多个候选 JD 预览。
5. 用户选择目标岗位后，才进入最终匹配分析或确认入库。

## 证据文件路径

- `config/mcp.json`
- `config/config.yaml`
- `config/prompts/system.md`
- `docs/capability_catalog.md`
- `docs/job-research-requirements.md`
- `docs/job-research-design.md`
- `docs/job-research-task.md`
- `backend/src/starter_agent/skills/job-research/SKILL.md`
- `backend/src/starter_agent/mcp/client.py`
- `backend/src/starter_agent/mcp/manager.py`
- `backend/src/starter_agent/mcp/discovery.py`
- `backend/src/starter_agent/mcp/tool_adapter.py`
- `backend/src/starter_agent/capabilities/gate.py`
- `backend/src/starter_agent/capabilities/confirmations.py`
- `backend/src/starter_agent/agent/runtime.py`
- `frontend/web/index.html`
- `tests/unit/test_job_research_auto_jd_contract.py`
- `tests/e2e/test_playwright_job_research.py`
- `tests/integration/test_search_job_description_flow.py`
- `tests/integration/test_job_research_orchestration.py`
- `tests/integration/test_job_research_degradation.py`
- `tests/integration/test_model_request_tool_exposure.py`
- `tests/integration/test_tool_confirmation_matrix.py`

## 剩余风险

- `@playwright/mcp@latest` 会随 npm 发布变化；每次上线前都应重新发现 schema、刷新 snapshot 并跑 external E2E。
- 真实招聘站点可能改版、下线或增加访问限制；固定 URL 不能作为永久资产。
- 真实云模型是否稳定遵守“搜索后自动抓取最多 3 个 JD”仍需 smoke 观察；本次合同测试锁住的是 Prompt/Skill 策略，不等同于每个云模型都 100% 稳定执行。
- 当前生产普通聊天中仍存在内置 `search_job_description` 抓取器；Skill 文档目标是 Playwright MCP。两者并存时需要在后续产品决策中明确优先级或逐步迁移。
- Windows pytest 临时目录偶发 `WinError 5`，需要使用工作区 basetemp 或提权重跑，不属于业务失败。

## 最终结论

**PARTIAL**

真实 Playwright MCP 端到端链路已通过；“询问岗位时不自动爬 JD”的根因已定位并修复。未给 PASS 的原因是缺少本轮真实 UI 操作截图/窄屏证据，以及真实云模型自主完成完整 SerpAPI→多 JD→RAG 连续链路的 smoke 记录。

## 2026-07-28 JD 校验回归补充

本轮真实 E2E 证明 Playwright MCP 能读取公开 Lever JD；此前 `incomplete_job_description` 的直接根因是编排器把 `JobValidation(state="verified")` 对象误当作非空错误列表，而不是 Browser MCP 抓取失败。修复后：

- `tests/e2e/test_playwright_job_research.py -m external`：2 项通过。
- 真实 MCP 发现：Playwright `1.62.0-alpha-1783623505000`，24 Tools。
- 真实 Smoke `reliability-real-smoke-20260728-r`：`passed`。
- Smoke 明确区分显式公开探针 URL 和 SerpAPI 候选；SerpAPI 搜索仍真实执行并记录，但不会把搜索结果质量失败伪装成抓取成功。
- 已验证 JD 进入本地 RAG；外部模型只接收公开 JD 摘要，不接收简历正文。

新增证据：

- `reports/trust/reliability-20260728/reliability-real-smoke-20260728-r.json`
- `reports/trust/reliability-20260728/reliability.sqlite`

总体结论仍为 **PARTIAL**，原因仍是原验收要求中的完整真实 UI/窄屏证据与所有节点级 Trace 关联未全部完成；Playwright MCP 与 JD 提取链路本身已通过。
