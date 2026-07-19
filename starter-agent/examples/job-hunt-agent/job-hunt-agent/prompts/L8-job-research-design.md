# L8 · 求职调研外部能力设计文档

用途：需求确认后，为 Browser MCP 与 `job-research` Skill 生成 `job-research-design.md`。本提示词只做设计，不生成任务计划，不修改代码。

---BEGIN---
你是我的 Agent 工程设计协作伙伴。请使用中文工作。

前提：

- `job-research-requirements.md` 已经确认。
- 现在只生成 `job-research-design.md`，不要生成 `job-research-task.md`，不要修改代码。

请先阅读现有仓库，确认：

- Starter Agent 的 Tool Registry、配置加载、健康检查、日志和前端状态展示方式。
- SerpAPI 搜索工具的真实 Tool Name 与 Schema。
- RAG 检索工具的真实 Tool Name 与 Schema；如果尚未实现，明确写为依赖，不得假设它已经存在。
- 项目现有 Skill 目录、解析规则和触发机制。

Browser MCP 固定采用以下接入配置，设计必须落到 Starter Agent 的真实配置加载与运行链路：

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

然后生成 `job-research-design.md`，必须包含：

- 需求理解与设计目标
- 技术选型
- 总体架构设计
- 模块/组件设计
- 数据模型
- API / 服务接口设计
- 状态流转与交互流程
- 错误处理
- 性能与安全考虑
- 测试策略
- 风险与待确认事项

设计必须说明：

1. MCP Host 如何为每个 Server 建立独立 Client 连接，如何管理初始化、健康状态、超时与关闭。
2. 如何把名为 `playwright` 的 Server 配置接入现有配置系统；通过 stdio 启动 `npx @playwright/mcp@latest`，管理子进程、stdin/stdout、stderr、退出码、初始化超时与关闭。记录运行时实际解析到的包版本，便于复现和后续固定版本。
3. 初始化后如何发现 Tools / Resources / Prompts，如何在前端或日志中展示 Server 与 Tool 状态。
4. 怎样将 Tool Name、Description、Input Schema、读写风险和外发数据范围记入 allowlist 与 `docs/capability_catalog.md`。
5. 默认只允许读取公开 JD 所需的最小 Browser Tool 集合；导航、点击、输入、上传、登录与提交动作必须分别定义风险。
6. Browser Tool Result 的长度限制、敏感字段脱敏、裁剪标记与来源 URL 保留策略。
7. `job-research` Skill 的触发条件、输入、步骤、工具依赖、验证、失败处理与输出格式。
8. 求职调研流程：岗位搜索 → 选择 URL → Browser 读取 JD → 提取要求 → RAG 取回简历证据 → 生成带来源的匹配分析。
9. Server 不可用、Tool 不在 allowlist、Schema 变更、页面拒绝访问、超时和结果过长时的状态与降级策略。
10. 正常路径、越权拒绝、不可用降级、Tool Result 裁剪和来源可追溯的测试策略。
11. 设计统一的前端「能力管理」页面，使用 `MCP Servers` 与 `Skills` 两个页签；说明路由、页面布局、组件边界、空状态、加载状态、错误状态和窄屏适配。
12. `MCP Servers` 页签必须设计 Server 列表与详情：来源、版本、transport、连接状态、健康状态、Tools / Resources / Prompts、Tool Schema、allowlist、域名范围、最近错误，以及连接、断开、启用、禁用、健康检查和刷新能力。
13. `Skills` 页签必须设计 Skill 列表与详情：名称、描述、来源、版本或更新时间、文件位置、启用状态、依赖 Tool / MCP、触发示例、不触发示例、验证与失败处理，以及启用、禁用、重新加载和查看原始定义能力。
14. 说明管理页面所需的后端 API、权限校验、状态模型和操作状态流转。前端状态必须来自真实 MCP Client Manager、Tool Registry 和 Skill Registry；操作失败后不得保留虚假的成功状态。
15. 连接、断开、扩大 allowlist、启用写操作或重新加载外部定义等高风险动作，需要怎样提示风险、请求人工确认、记录审计信息并支持撤销或禁用。
16. 从配置加载到真实调用跑通的诊断闭环：检查 Node.js / npx、包下载与缓存、进程 stderr、JSON-RPC 初始化、能力发现、浏览器依赖、Tool Schema、Tool 调用和 Tool Result；每一步的可观察状态、错误映射和修复后重试方式都要明确。
17. 设计验收不得停在组件测试。必须包含 Starter Agent 启动 Playwright MCP、发现真实 Tools、通过 allowlist 暴露所需能力、读取一个公开 JD、在 UI 与 Trace 中看到真实结果的端到端路径。
18. 设计 Server 级刷新状态机与接口：刷新只作用于目标 Server，重新发现能力、版本、Schema 与健康状态；说明并发刷新、运行中调用、失败回滚、旧快照过期标记和其他 Server 隔离策略。
19. 设计 Server / Tool 的启停与 Context 暴露模型。关闭项只进入轻量能力目录，至少包含名称与启用状态，但不进入模型 callable tools，也不携带完整 Description / Input Schema；启用且审查通过的 Tool 才生成完整模型 Tool 定义。说明状态变更后怎样原子更新下一轮请求快照。
20. 设计统一 `PreToolCallGate` 或项目等价组件，放在模型产生 Tool Call 与 MCP Client 真正发送调用之间。Gate 必须检查启用状态、Schema、参数、域名或资源范围、权限、白名单、强制确认策略和数据外发范围，并返回 `allow`、`require_confirmation` 或 `deny` 等明确决策。
21. 设计聊天内确认状态流：模型提出调用 → Gate 生成待确认记录 → 对话展示 Tool、Server、参数摘要、风险与数据去向 → 用户选择仅本次执行、执行并加入白名单或取消 → 后端重新校验后执行。等待期间不得调用 Tool，并处理超时、刷新恢复、重复点击、并发请求与审计。
22. 说明权限优先级与白名单数据模型。建议至少遵循 `deny / disabled > always_confirm > allowlist_auto > confirm_once`；强制人工确认规则不可通过加入白名单绕过。白名单应按 Server、Tool 和必要的域名、动作或参数范围收窄，而不是无条件永久放行。
23. 为 Server 刷新、Tool 启停、Context Schema 暴露、自动执行、仅本次确认、加入白名单、强制确认、取消、超时和重复提交分别设计单元、集成与端到端测试，并说明如何从真实模型请求证明关闭 Tool 的完整 Schema 已被移除。

约束：

- 不要臆造项目中不存在的 CLI 或前端页面。如果需要新增，必须在设计中明确列出。
- 不要把管理页面设计成只读静态样例；每个可操作控件必须对应真实后端接口、权限检查、加载状态、成功状态和失败回滚。
- 不要自己手写认证协议或 Token 验证；应使用成熟 SDK 与标准认证机制。
- 不要默认暴露 Server 的全部 Tool。
- 不要把关闭 Server / Tool 的完整 Description 或 Input Schema 注入模型 Context；轻量能力目录必须与 callable tools 分离。
- 不要让 MCP Client、Skill 或前端直接绕过 Pre-Tool-Call Gate 调用真实 Tool。
- 不要让 Browser MCP 绕过登录、robots、反爬、付费墙或其他站点限制。
- 不要用 Mock Server、硬编码 Tool 列表、静态成功状态或模型生成的网页内容替代 Playwright MCP 的真实运行结果。

输出 `job-research-design.md` 后停止，等待我确认设计。
---END---
