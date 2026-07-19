# L8 · 求职调研外部能力需求文档

用途：为求职 Agent 的 Browser MCP 与 `job-research` Skill 生成 `job-research-requirements.md`。本提示词只做 brainstorming 和需求文档，不做设计、不生成任务计划、不修改代码。

---BEGIN---
你是我的 Agent 功能开发协作伙伴。请使用中文工作。

现在要为 Starter Agent 新增「求职调研外部能力」：

- SerpAPI 工具负责找到岗位摘要和公开 JD 链接。
- Browser MCP 负责访问允许的公开链接，读取完整 JD 页面内容。
- 现有 RAG 检索能力负责从用户简历中取回可引用证据。
- `job-research` Skill 负责规定何时调用这些能力、按什么顺序工作、怎样验证以及失败时怎样停下。

Browser MCP 明确使用 Playwright MCP，项目需要兼容并接入以下配置：

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

MCP 管理与 Tool 权限必须满足以下规则：

- 支持按 Server 级别单独刷新。刷新某一个 MCP Server 时，重新发现该 Server 的 Tools / Resources / Prompts、Schema、版本与健康状态；不得要求刷新全部 Server，也不得影响其他 Server 的连接和运行中任务。
- Server 与 Tool 都有明确的启用状态。关闭的 Server、关闭的 Tool 只能在模型可见的轻量能力目录中保留名称和启用状态，不得作为 callable tool 暴露，也不得把完整 Description、Input Schema 或其他全量定义加入模型 Context。
- 只有已连接、已启用且通过审查的 Tool，才允许把完整 Name、Description 和 Input Schema 放入模型可调用工具列表。启停或刷新后，下一轮模型请求必须使用最新快照。
- Tool Call 真正发往 MCP Server 之前必须经过统一的 Pre-Tool-Call Gate。Gate 至少检查 Server / Tool 启用状态、参数 Schema、域名或资源范围、权限策略、白名单、强制人工确认规则和待外发数据。
- 白名单内且没有命中强制确认规则的调用可以自动执行；不在白名单的调用必须暂停，在对话中显示确认卡片，用户点击后才允许执行。
- 确认卡至少提供「仅本次执行」「执行并加入白名单」「取消」；若调用属于强制人工确认动作，则不得通过加入白名单绕过，以后每次仍需确认。
- 等待确认期间不得提前调用 Tool。确认请求必须支持超时、取消、防重复提交、审计记录与刷新后的状态恢复。

第一阶段只做 brainstorming 和 `job-research-requirements.md`，不要写设计，不要生成任务计划，不要修改任何文件。

请先检查项目现状，再向我提出最多 5 个必要问题，优先确认：

1. Starter Agent 当前的 MCP 配置格式、配置文件位置，以及如何加载上述 `playwright` Server。
2. 允许访问的域名、页面类型和是否允许登录。
3. Browser 能力是否只读，哪些写操作必须禁用或强制人工确认。
4. 白名单粒度、可加入白名单的权限范围，以及哪些 Tool / 参数 / 动作始终强制人工确认。
5. 现有 SerpAPI、RAG 工具的实际名称与 Schema，以及外部服务不可用、页面拒绝访问或内容过长时的期望行为。

然后生成 `job-research-requirements.md`，必须包含：

- 需求背景
- 功能范围
- 目标用户与使用场景
- 用户故事
- 功能需求
- 非功能需求
- 验收标准
- 边界情况
- 风险与待确认事项

验收标准至少覆盖：

- 前端提供统一的「能力管理」入口，并使用 `MCP Servers` 与 `Skills` 两个页签分别管理外部连接和程序性能力。
- `MCP Servers` 页签能查看 Server 列表、来源、配置版本与运行时实际解析版本、transport、连接与健康状态、已发现能力、allowlist、最近错误；支持在权限控制下连接、断开、启用、禁用和重新检查。
- `MCP Servers` 页签支持按单个 Server 刷新能力与健康状态；刷新一个 Server 不影响其他 Server，刷新失败保留上一份可用快照并明确标记过期与错误。
- 每个 Tool 都可以启用或关闭。关闭项只在轻量能力目录中向模型保留名称，不进入 callable tools，也不注入完整 Description / Input Schema；启用项才按权限策略暴露完整 Schema。
- 能通过真实模型请求或 Context 调试信息证明：关闭 Tool 后完整 Schema 已从模型请求移除，重新启用后才恢复；不能只依据前端开关状态判断。
- Tool Call 前存在统一 Pre-Tool-Call Gate。白名单内的普通调用可自动执行；非白名单调用在聊天中显示参数摘要、风险和数据去向，并等待用户选择仅本次执行、执行并加入白名单或取消。
- 强制确认动作每次都必须确认，不能被白名单绕过；确认前无真实 Tool 请求，取消或超时后也不得执行。
- `Skills` 页签能查看 Skill 名称、描述、来源、版本或更新时间、文件位置、启用状态、工具依赖、触发示例与不触发示例；支持启用、禁用、重新加载和查看详情。
- 管理页面的状态必须来自真实后端配置、Registry 和运行状态；后端不可用或操作失败时展示明确错误，不得只在前端伪造成功状态。
- 能看到 MCP Server 连接状态、版本与已发现能力。
- 上述 `playwright` 配置能被 Starter Agent 真实加载；`npx @playwright/mcp@latest` 对应的进程能够启动、完成 MCP 初始化并返回真实能力列表。
- 必须先查看 Tool Schema，再决定 allowlist；默认不全部暴露。
- Browser MCP 只能访问允许的公开求职页面，不能自动投递、登录、发送信息或绕过站点限制。
- 能从一个公开 JD URL 提取岗位名称、公司、地点、职责、必备要求与来源 URL。
- 必须持续排查并修复配置、进程启动、初始化、能力发现、浏览器运行、Tool 调用与结果解析问题，直到真实公开 JD 调用跑通；不能以写完配置、单元测试通过、Mock 成功或模型口述能力作为完成。
- `job-research` Skill 能组合岗位搜索、Browser MCP 与 RAG 检索，但不隐藏每次 Tool Call 的输入、输出和失败。
- 服务不可用或页面无法读取时，Agent 明确说明缺少哪项能力，不伪造 JD 内容。
- 保留一条正常运行记录和一条 MCP 不可用的降级记录。

约束：

- 不要臆造项目中不存在的 CLI 命令、配置路径或 Tool 名称。
- 不要只做静态演示页面；管理操作必须调用真实后端接口并在刷新后保持一致。
- 不要把关闭 Server / Tool 的完整 Description、Input Schema 或其他全量定义放进模型 Context；名称只能出现在不可调用的轻量能力目录中。
- 不要让前端确认按钮绕过后端 Pre-Tool-Call Gate；是否允许执行必须由后端统一判定。
- 不要将 API Key、Token、Cookie、邮箱授权码或个人登录信息写入仓库。
- 不要把“模型说已经完成”当成验收；必须检查真实 Tool Result 和来源 URL。
- 除非遇到确实需要用户提供的系统权限、网络访问或外部授权，不得在真实调用跑通前停止；发生外部阻塞时必须给出已执行步骤、原始错误、阻塞原因和用户需要完成的最小动作，阻塞解除后继续验证。

输出 `job-research-requirements.md` 后停止，等待我确认需求。
---END---
