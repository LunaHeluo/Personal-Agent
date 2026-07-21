## Task3 实施报告

### 结果

- 新增每 Server 独立的官方 MCP stdio `ClientSession`、`AsyncExitStack` 与生命周期 Manager。
- 接入 bootstrap 与 FastAPI lifespan；单个 MCP 启动失败只记录真实失败状态，不阻塞基础 API。
- 生产配置继续使用 `command=npx`、`args=["@playwright/mcp@latest"]`；运行时版本只取 initialize result，公开 SDK 无退出码时保持 `None/unknown`。

### SDK 公开 API 核对

在 `uv run --isolated --frozen --offline` 中确认锁定 `mcp==1.28.1`：

- `StdioServerParameters(*, command, args, env, cwd, encoding, encoding_error_handler)`
- `stdio_client(server, errlog: TextIO)`
- `ClientSession(read_stream, write_stream, ...)` 支持 async context manager
- `ClientSession.initialize() -> InitializeResult`
- `InitializeResult` 公开字段为 `protocolVersion` 与 `serverInfo.name/version`
- `ClientSession` 公开 API 不提供 close 或子进程退出码；关闭由 context manager/`AsyncExitStack` 完成，退出码未知时不伪造

### TDD 证据

- 初始 RED：两个指定测试文件分别因缺少 `starter_agent.mcp.client` 与 `starter_agent.mcp.manager` 失败。
- 清理分支 mutation RED：临时移除候选 `AsyncExitStack.aclose()` 后，初始化超时测试准确失败于 Session 未退出；恢复实现后转 GREEN。
- 两个新测试文件：10 tests，0 failures。

### 验证

- 相关回归（新生命周期测试、API/Application、Task2 config/models/store）：93 tests，exit 0。
- `uv lock --check`：exit 0。
- `git diff --check`：exit 0（仅 Git 的 LF→CRLF 工作树提示）。

### 已知边界

- 官方 SDK 不公开可靠退出码或进程句柄，生产状态使用 `exit_code=None`；只有受控依赖注入能验证已知退出码传播。
- 本任务不实现 Tools/Resources/Prompts 发现与真实 Playwright JD E2E；按计划保留到后续任务。
