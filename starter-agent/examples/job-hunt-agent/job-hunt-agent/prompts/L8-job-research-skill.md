# L8 · Job Research Skill

用途：在 MCP Client 与 Browser Tool 已可用后，生成或修订 `job-research` Skill。

---BEGIN---
你是我的 Agent Skill 设计协作伙伴。请使用中文工作。

请先阅读：

- `docs/agent.md`
- `docs/capability_catalog.md`
- `job-research-requirements.md`
- `job-research-design.md`
- 现有 Skill 目录与加载规则
- SerpAPI、Browser MCP 和 RAG 检索的真实 Tool Schema

Browser MCP Server 名称为 `playwright`，由 `npx @playwright/mcp@latest` 启动。必须从已运行 Server 的真实能力发现结果读取 Tool Name、Description 和 Input Schema，不得根据经验臆造 Playwright MCP Tool 名称。

生成或修订 `job-research` Skill。使用项目现有 Skill 目录和文件命名约定，不要臆造路径。

Skill 必须包含：

- 只写岗位调研任务特有的程序性知识，不复制 `docs/agent.md` 中的全局身份、目标、Non-goal 和安全边界。
- 准确的 name 与 description，说清何时触发、何时不触发。
- Preconditions：目标岗位、城市或关键词，以及简历知识库的可用状态。
- Workflow：
  1. 使用 SerpAPI 找到候选岗位和公开 URL。
  2. 向用户展示候选项，在多个结果时请用户选择。
  3. 使用 allowlist 内的 Browser MCP Tool 读取选中 JD，保留来源 URL。
  4. 提取职责、必备要求、加分项、地点与关键限制。
  5. 使用 RAG 检索简历证据；没有证据时标记缺口，不补写经历。
  6. 生成带 JD URL 和简历 Chunk 引用的匹配分析。

- Validation：检查 JD 来源、字段完整性、简历引用、未验证信息和 Tool Trace。
- Failure Handling：Server 不可用、页面不允许访问、内容被裁剪、RAG 无证据、多个岗位无法确定时的处理。
- Output Format：岗位摘要、必备要求、匹配证据、能力缺口、待确认事项、来源与 Tool Trace。
-生成的skill产物放在src/starter_agent/skills目录里面
-生成的skill产物必须符合以下结构：文件夹为skill名称。然后里面是一个SKILL.md,skill.md必须符合以下结构：
``---
name: job-research
description: 什么时候用，可以拿来做什么
Preconditions
xxx
Workflow
xxx
``
安全边界：

- Skill 的步骤不得覆盖或弱化 `docs/agent.md` 与 System Prompt 的全局边界；发生冲突时停止执行并请求人工确认。
- 不自动投递、登录、发邮件、填表或提交任何申请。
- 不绕过站点限制，不爬取未授权内容。
- 不夸大或补写简历经历。
- 不隐藏 Tool 失败、裁剪或无证据状态。
- Skill 只能提出 Tool Call，不能绕过统一 Pre-Tool-Call Gate。白名单外调用必须等待聊天确认；强制人工确认动作即使已在白名单中也必须每次确认。
- Tool 处于关闭状态时，不得假设完整 Schema 可用或尝试调用；应说明能力未启用，并请求用户在能力管理页面启用或选择降级方案。

生成后，说明写入路径、触发示例、不触发示例和最小验收步骤。
---END---
