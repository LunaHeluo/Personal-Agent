# L8 · 求职调研外部能力任务计划

用途：设计确认后，生成 `job-research-task.md`，待用户确认计划后再实现。

---BEGIN---
你是我的 Agent 工程实现协作伙伴。请使用中文工作。

前提：

- `job-research-requirements.md` 已确认。
- `job-research-design.md` 已确认。
- 现在先生成 `job-research-task.md`，不要立即修改代码。等我确认计划后，再按顺序执行。

实现时 Browser MCP 必须使用以下配置：

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

`job-research-task.md` 必须由有序 Task1 / Task2 / Task3 ... 组成。每个 Task 必须包含：

- 任务目标
- 子任务
- 依赖关系
- 验收标准
- 预估复杂度

不要生成“状态”字段，不要写“未开始 / 进行中 / 已完成”等静态状态文本。实际执行进度由任务管理机制单独记录。

任务计划至少覆盖：

1. 审计现有 Tool Registry、配置、状态展示、SerpAPI、RAG 与 Skill 加载机制，记录实际可用能力与缺口。
2. 新增 MCP Client Manager，支持配置加载、独立 Client 连接、初始化、健康检查、超时、关闭与错误状态。
3. 把上述 `playwright` Server 配置接入 Starter Agent 的真实配置系统，通过 stdio 启动 `npx @playwright/mcp@latest`；处理进程生命周期、stderr、退出码、初始化超时和关闭，并记录实际解析到的包版本。
4. 实现 Tools / Resources / Prompts 发现、Tool Schema 展示和版本化能力快照，未通过审查的能力不进入模型 Tool Registry。
5. 实现按单个 Server 刷新：只重连或重新发现目标 Server，不影响其他 Server；处理并发刷新、运行中调用、失败回滚、旧快照过期标记和错误展示。
6. 实现 Server / Tool 启停与两级暴露：关闭项只进入包含名称和启用状态的轻量能力目录，不进入 callable tools；启用且审查通过的 Tool 才向模型暴露完整 Name、Description 与 Input Schema。状态变化后原子更新下一轮模型请求快照。
7. 实现 Browser Tool allowlist、域名范围、读写风险标记、强制确认策略和可禁用配置。
8. 在所有真实 Tool 执行入口前实现统一 Pre-Tool-Call Gate，返回自动允许、等待确认或拒绝；任何 MCP Client、Skill、重试和前端路径都不得绕过。
9. 实现待确认记录与聊天确认 API：展示 Server、Tool、参数摘要、风险与数据去向；支持仅本次执行、执行并加入白名单、取消、超时、刷新恢复、防重复提交和审计。强制确认动作不能加入自动执行白名单。
10. 实现 Tool Result 长度上限、裁剪标记、脱敏、来源 URL 和 Trace 字段。
11. 实现能力管理所需的后端查询与操作接口，分别读取 MCP Client Manager、Tool Registry 和 Skill Registry 的真实状态；加入权限校验、错误映射、操作审计和失败回滚。
12. 实现统一的前端「能力管理」页面和 `MCP Servers` / `Skills` 两个页签，包括加载、空、成功、失败和窄屏状态。
13. 完成 `MCP Servers` 管理：列表、详情、来源、版本、transport、连接与健康状态、能力发现、Schema、Tool 启停、allowlist、域名范围、最近错误，以及按 Server 连接、断开、启用、禁用、健康检查和刷新操作。
14. 在对话中实现 Tool Call 确认卡片及仅本次执行、执行并加入白名单、取消操作；等待确认时不发送真实调用，操作后显示最终状态与审计引用。
15. 完成 `Skills` 管理：列表、详情、名称、描述、来源、更新时间、文件位置、启用状态、工具依赖、触发与不触发示例，以及启用、禁用、重新加载和查看原始定义操作。
16. 生成 `job-research` Skill，组合 SerpAPI、Browser MCP 与 RAG 检索，包含前置条件、步骤、验证、失败处理和输出格式，并确保 Skill 调用仍经过 Pre-Tool-Call Gate。
17. 更新 `docs/capability_catalog.md`，记录来源、版本、transport、能力、启停状态、Context 暴露级别、权限、白名单、强制确认、数据去向、负责人、健康检查和禁用方式。
18. 新增 Server 级刷新隔离、Tool 启停、关闭项只暴露名称、启用项暴露完整 Schema、白名单自动执行、非白名单确认、加入白名单、强制确认、取消、超时与重复提交测试；同时覆盖域名越界、Server 不可用、结果裁剪和来源追溯。
19. 执行真实端到端验收：Starter Agent 加载配置 → 启动 Playwright MCP → 完成初始化 → 按 Server 刷新 → 发现真实 Tools → 切换 Tool 启停并检查模型请求 → 经过 Pre-Tool-Call Gate 与确认卡 → 调用真实 Tool 读取公开 JD → UI 与 Trace 展示真实来源和结果。
20. 对端到端过程中出现的错误持续诊断和修复，包括 Node.js / npx、包下载、进程启动、stderr、JSON-RPC、初始化超时、刷新隔离、Schema 快照、权限决策、确认恢复、浏览器依赖、Tool 调用和结果解析；每次修复后重新执行完整链路，直到通过。

每个 Task 必须可以独立执行与验收，并且按依赖顺序排列。

输出 `job-research-task.md` 后停止，等待我确认计划。

当我明确说“确认计划，开始执行”后，再按 Task 顺序小步实现。每完成一个 Task，汇报：

- 修改了哪些文件
- 运行了哪些测试
- 是否满足该 Task 的验收标准
- 剩余风险

执行约束：

- 不得在只写入配置、只完成代码、只通过 Mock 或单元测试时宣称完成。
- 不得用硬编码 Tool 列表、静态前端状态或模型口述结果替代真实 Playwright MCP 调用。
- 如果测试失败，继续读取日志、定位原因、修改并重跑，直到端到端验收通过。
- 只有遇到必须由用户处理的系统权限、网络限制或外部授权时才可以暂停；暂停时必须提供原始错误、已完成的排查、明确阻塞点和用户需要执行的最小动作。阻塞解除后继续执行剩余验证。
---END---
