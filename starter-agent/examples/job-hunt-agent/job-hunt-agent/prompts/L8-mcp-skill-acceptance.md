# L8 · MCP 与 Skill 最终验证

用途：Browser MCP 与 `job-research` Skill 实现后，生成可重复的最终验证方案。

---BEGIN---
你是我的 Agent 功能验收协作伙伴。请使用中文工作。

请阅读：

- `job-research-requirements.md`
- `job-research-design.md`
- `job-research-task.md`
- `docs/capability_catalog.md`
- `job-research` Skill
- MCP Client、Browser Server 配置、allowlist、健康检查、Tool Trace 和相关测试

请执行代码、配置和测试审查，然后生成 `docs/mcp-skill-acceptance.md`。不要因为文档声称“已完成”就判定通过。

Browser MCP 必须使用并验证以下真实配置：

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

验收必须包含：

1. 配置与发现
   - Starter Agent 能真实读取上述 `playwright` 配置，并通过 stdio 启动 `npx @playwright/mcp@latest`；记录实际解析到的包版本、进程状态、stderr 与退出码。
   - 能看到初始化结果、健康状态以及发现的 Tools / Resources / Prompts。
   - Tool Name、Description 和 Input Schema 与 Server 返回一致。
2. 最小权限
   - 支持按单个 MCP Server 刷新；刷新目标 Server 时其他 Server 的连接和运行任务不受影响，刷新失败能回滚或保留上一份可用快照并标记过期。
   - Server 与 Tool 可以独立启用和关闭。关闭项只在轻量能力目录中保留名称与启用状态，不进入 callable tools，模型请求中不包含其完整 Description / Input Schema；启用且审查通过后才恢复完整 Schema。
   - 必须检查至少一组真实模型请求或 Context 调试快照，证明 Tool 关闭前后 Schema 确实移除与恢复，不能只看前端开关。
   - 白名单内且未命中强制确认的 Browser Tool 可以自动执行；域名越界、禁用项和拒绝策略不能执行。
   - 非白名单调用在真正发送前进入 Pre-Tool-Call Gate，并在对话中展示确认卡；确认前不得出现真实 MCP Tool 请求。
   - 确认卡支持仅本次执行、执行并加入白名单和取消。加入白名单后仅匹配范围内的后续调用可自动执行；强制确认动作每次仍需确认。
   - 登录、上传、提交、发送等强制确认动作不能通过白名单绕过；确认超时、取消、刷新恢复与重复点击均不会造成误执行或重复执行。
3. 真实求职调研
   - 选择一个可公开访问的 JD URL，运行 `job-research` Skill。
   - 保留 SerpAPI、Browser MCP 和 RAG Tool 的真实 Tool Trace。
   - 输出中的岗位要求可回到 JD URL，匹配经历可回到简历 Chunk。
   - Browser 结果来自 Playwright MCP 的真实 Tool Result；不能使用 Mock、硬编码页面内容、静态成功状态或模型自行生成的 JD 代替。
4. 失败与降级
   - Server 不可用、Tool 不在 allowlist、页面无法读取、请求超时和 Tool Result 被裁剪各有至少一条验证。
   - 外部能力不可用时，核心对话仍可继续，Agent 不伪造 Tool Result。
5. Skill 行为
   - 正确的求职调研请求能触发 Skill。
   - 不相关的请求不误触发。
   - 输入不足时先追问，多个候选岗位时先请用户选择。
6. 安全与文档
   - 日志、Trace、截图、测试和提交中不含 API Key、Token、Cookie 或个人隐私。
   - `docs/capability_catalog.md` 记录来源、版本、权限、数据去向、负责人、健康检查与禁用方式。
7. 能力管理页面
   - 存在统一的前端「能力管理」入口，并能在 `MCP Servers` 与 `Skills` 页签之间切换；桌面和窄屏均无重叠、裁切或不可操作控件。
   - MCP 列表与详情展示真实 Server 来源、版本、transport、连接与健康状态、能力发现、Schema、allowlist、域名范围和最近错误。
   - Skills 列表与详情展示真实名称、描述、来源、更新时间、文件位置、启用状态、工具依赖、触发与不触发示例。
   - 连接、断开、Server / Tool 启用与禁用、单 Server 健康检查与刷新、Skill 重新加载均调用真实后端；刷新页面后状态保持一致。
   - 对话页能渲染真实待确认 Tool Call，显示参数摘要、风险和数据去向，并把三种用户选择提交给后端 Gate 状态机。
   - 扩大 allowlist、启用写操作或连接未知 Server 时出现人工确认；权限不足、后端失败或超时时展示明确错误并回滚乐观状态。
   - 验收至少保留一组单 Server 刷新、一组 Tool 启停与模型 Schema 对比、一组非白名单确认、一组强制确认不可绕过、一组 Skill 启停或重新加载、一组后端失败回滚的真实 UI、API 与 Trace 记录。
8. 跑通要求
   - 从 Starter Agent 加载配置开始，依次验证 Server 进程启动、MCP 初始化、真实能力发现、allowlist、公开 JD Tool 调用、Tool Result、来源 URL、UI 状态和 Trace，完整链路必须全部通过。
   - 任一步失败都要读取原始日志并继续诊断、修改和重跑，直到真实端到端调用通过；不得把配置存在、代码完成或单元测试通过视为最终完成。
   - 只有系统权限、网络限制或外部授权必须由用户处理时才可标记为阻塞；必须记录原始错误、已执行排查、阻塞原因和用户最小操作，解除后继续验收。

最后输出：

- 通过项
- 失败项
- 未执行项及原因
- 可重现命令或用户操作路径
- 证据文件路径
- 剩余风险
- 最终结论：PASS / PARTIAL / FAIL

只有真实 Playwright MCP 端到端链路全部通过时，最终结论才允许为 PASS。
---END---
