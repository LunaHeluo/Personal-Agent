# Starter Agent Web Frontend 构建计划

## 目标

在 `starter-agent-00` 中新增一个独立的 HTML 前端页面，用于以类似 ChatGPT 的对话体验展示并联动 Starter Agent。

前端页面放在：

```text
src/web/index.html
```

目录约束：

- Web 前端相关静态文件统一放在 `src/web/` 下。
- 项目根目录不再新增或保留独立的 `web/` 目录，避免和 Python 源码目录之外的散落入口混用。
- 启动静态服务时统一使用 `python3 -m http.server 8001 -d src/web`。

前端优先调用现有流式接口：

```text
POST http://127.0.0.1:8000/v1/chat/stream
```

后端 API 服务仍由现有命令启动：

```bash
uv run agent serve
```

静态前端建议用单独端口启动，例如：

```bash
python3 -m http.server 8001 -d src/web
```

当前 `src/starter_agent/interfaces/api.py` 已经允许 `http://127.0.0.1:8001` 和 `http://localhost:8001` 跨域访问，因此前端独立启动后可以直接调用本地 API。

## 页面功能

`src/web/index.html` 作为单文件前端实现，包含 HTML、CSS、JavaScript，不引入复杂构建工具。

需要实现的核心能力：

- ChatGPT 风格的聊天布局：顶部标题区、消息列表、底部输入区。
- 用户消息和 Agent 消息分左右或不同样式展示。
- 支持输入消息后回车发送，`Shift + Enter` 换行。
- 调用 `/v1/chat/stream`，实时展示 Agent 流式返回内容。
- 从 `done` 事件中保存 `session_id`，让后续消息进入同一个会话。
- 页面启动时调用 `GET /v1/providers` 读取后端已配置 provider 列表、默认 provider/model 和 Key 状态。
- 支持选择后端已配置的 `provider`；默认选项为空，表示使用后端 `config/config.yaml` 中的 `model.default_provider`。
- 可选填写 `model`，为空时使用后端 `config/config.yaml` 中的 `model.default_model`。
- 发送中禁用输入和发送按钮，避免重复提交。
- 支持错误提示，例如后端未启动、模型配置错误、接口返回 `error` 事件。
- 支持会话侧边栏：查看历史会话、新建会话、切换会话、删除会话。
- 历史会话过多时，左侧会话列表必须在固定区域内独立滚动，不能撑高整个页面或导致主内容区被挤出视口。
- 支持读取某个历史会话的消息，并续着该会话继续对话。
- 支持清空当前页面对话，并重置本地 `session_id`，作为创建新会话的入口之一。
- 支持删除历史会话；删除前必须提示用户确认，确认后再调用后端删除接口。

建议第一版不做登录、会话重命名、Markdown 完整渲染、文件上传、语音输入等高级能力，保持课程 starter 项目足够轻量。

## 需要改动的文件

### 1. 新增 `src/web/index.html`

新增单文件静态页面，建议结构如下：

```text
src/web/
  index.html
```

页面中包含：

- `<main>`：整体聊天应用容器。
- 消息列表区域：用于追加用户消息、Agent 消息、错误消息。
- 会话侧边栏：用于展示历史 session、新建会话、切换会话、删除会话。
- 输入表单：textarea、发送按钮、清空按钮。
- 设置区域：API 地址、provider、model。
- 内联 CSS：完成响应式布局、消息气泡、滚动区域、按钮状态。
- 内联 JavaScript：处理事件、调用流式 API、解析 SSE 数据、加载 session 列表和历史消息。

JavaScript 关键逻辑：

1. 维护页面状态：

```js
let sessionId = null;
let isSending = false;
let sessions = [];
```

2. 发送请求体：

```js
{
  message,
  session_id: sessionId,
  provider,
  model
}
```

其中 `session_id`、`provider`、`model` 为空时不要传或传 `null`，避免误传空字符串。

3. 使用 `fetch` 调用流式接口：

```js
const response = await fetch(`${apiBase}/v1/chat/stream`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload)
});
```

4. 用 `ReadableStream` 读取 SSE 文本，按 `\n\n` 拆分事件。

5. 解析每条 `data: {...}`：

- `type: "delta"`：把 `content` 追加到当前 Agent 消息。
- `type: "done"`：读取 `result.session_id`，更新本地 `sessionId`，展示 provider/model/tool_calls 等元信息。
- `type: "error"`：展示错误信息，并结束发送状态。

6. 加载和切换 session：

- 页面启动时调用 `GET /v1/providers`，渲染 provider 下拉框；不要在前端硬编码真实供应商列表。
- `GET /v1/providers` 只返回 provider 名称、类型、默认值和 Key 状态，不返回 API Key。
- 页面启动时调用 `GET /v1/sessions`，渲染会话侧边栏。
- 点击历史会话时调用 `GET /v1/sessions/{session_id}/messages`，把历史消息恢复到消息列表。
- 点击删除会话时先展示确认提示；用户确认后调用 `DELETE /v1/sessions/{session_id}`。
- 删除当前会话后，将本地 `sessionId` 设为 `null`，清空当前消息列表，并刷新会话侧边栏。
- 点击“新建会话”时将 `sessionId` 设为 `null`，清空当前消息列表。
- 新会话第一次发送完成后，从 `done.result.session_id` 获得真实 `session_id`，再刷新会话侧边栏。

### 2. 可选更新 `README.md`

如果要让用户更容易启动前端，可以在 README 的 API 使用部分后面追加一小节：

```text
启动 Web 前端
```

包含两步：

```bash
uv run agent serve
python3 -m http.server 8001 -d src/web
```

然后打开：

```text
http://127.0.0.1:8001
```

这一步不是第一版必须改动，但建议实现前端时同步补充，降低使用门槛。

### 3. 修改 `src/starter_agent/interfaces/api.py`

基础聊天能力已经满足，但要支持 ChatGPT 风格的历史会话切换，需要新增 session 查询接口。

保留现有接口：

```text
GET /health
POST /v1/chat
POST /v1/chat/stream
```

新增接口：

```text
GET /v1/providers
GET /v1/sessions
GET /v1/sessions/{session_id}/messages
DELETE /v1/sessions/{session_id}
```

建议响应模型：

```python
class SessionSummary(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    message_count: int = 0
    last_message: str | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]


class HistoryMessage(BaseModel):
    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    created_at: datetime
    turn_id: UUID


class SessionMessagesResponse(BaseModel):
    session_id: UUID
    messages: list[HistoryMessage]
```

`GET /v1/sessions` 行为：

- 默认按 `updated_at` 倒序返回。
- 支持 `limit` 查询参数，默认 50，最大 200。
- `title` 可以先从该 session 的第一条用户消息截断生成，不需要新增 title 字段。
- `last_message` 可以取最后一条非空消息内容并截断。

`GET /v1/sessions/{session_id}/messages` 行为：

- 返回该 session 的历史消息，按创建时间正序。
- 默认返回最近 100 条，可支持 `limit` 查询参数。
- 如果 session 不存在，返回 404。

`DELETE /v1/sessions/{session_id}` 行为：

- 删除该 session 及其所有历史消息。
- 如果 session 不存在，返回 404。
- 前端调用该接口前必须先提示用户确认删除。

### 4. 修改 `src/starter_agent/infrastructure/session_store.py`

当前 `SQLiteSessionStore` 已经有：

```text
sessions
messages
```

并且已有：

```python
ensure_session()
add_message()
list_messages()
```

需要扩展：

```python
list_sessions(limit: int = 50) -> list[...]
list_history_messages(session_id: UUID, limit: int = 100) -> list[...]
session_exists(session_id: UUID) -> bool
```

建议不要破坏现有 `list_messages()`，因为它被 Agent 上下文构建使用，只返回 `Message` 对象即可。

历史消息 API 需要 `created_at`、`turn_id` 等展示字段，可以新增一个专门的存储 DTO，例如：

```python
class StoredHistoryMessage(BaseModel):
    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    created_at: datetime
    turn_id: UUID
```

session 列表可以新增：

```python
class StoredSessionSummary(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    first_user_message: str | None = None
    last_message: str | None = None
```

这些模型可以放在 `src/starter_agent/domain/models.py`，也可以先作为 API 层响应模型和存储层内部 DTO。为了后续复用，建议放在 `domain/models.py`。

### 5. 可选修改 `src/starter_agent/application.py`

如果希望 API 层不直接访问 `store`，可以在 `ApplicationService` 中增加查询方法：

```python
list_sessions(limit: int = 50)
list_session_messages(session_id: UUID, limit: int = 100)
```

这样 `interfaces/api.py` 只调用 `create_application()`，保持和现有聊天接口一致。

### 6. 暂不需要修改 Agent Runtime、Provider 或 Tool

session 切换只涉及历史查询和前端展示，不需要修改：

```text
src/starter_agent/agent/runtime.py
src/starter_agent/providers/*
src/starter_agent/tools/*
```

这样可以降低改动风险，确保 Agent 生成逻辑不被前端功能影响。

## 建议的前端交互细节

页面初始状态：

- API 地址默认 `http://127.0.0.1:8000`。
- Provider 默认 `mock`。
- Model 默认留空。
- 输入框 placeholder 可以提示“输入消息，Enter 发送”。
- 左侧会话栏加载最近更新的 sessions。
- 如果没有历史 session，显示空状态，并让用户直接开始新对话。

发送消息时：

- 立即把用户消息追加到消息列表。
- 创建一个空的 Agent 消息气泡。
- 流式 delta 到达时持续追加文本。
- 自动滚动到底部。
- 请求完成后恢复输入。

错误处理：

- 如果 HTTP 状态不是 200，展示状态码和响应文本。
- 如果读取流失败，提示检查 `uv run agent serve` 是否已启动。
- 如果收到后端 `error` 事件，展示 `error.code` 和 `error.message`。

清空对话：

- 清空页面消息列表。
- 将 `sessionId` 重置为 `null`。
- 不删除后端 SQLite 中已有会话记录。

新建会话：

- 行为与清空当前页面类似。
- 不调用后端创建空 session。
- 只有用户发送第一条消息并收到 `done` 事件后，后端才产生真实 session。

切换会话：

- 点击侧边栏 session。
- 设置当前 `sessionId`。
- 调用历史消息接口并重绘消息列表。
- 后续发送消息时带上该 `sessionId`，从而续着历史对话。

## 测试计划

### 1. 后端现有测试

先确保前端依赖的 API 没有坏：

```bash
uv run pytest
```

重点关注已有测试：

```text
tests/integration/test_api.py
```

当前已经覆盖：

- `/health`
- `/v1/chat`
- `/v1/chat/stream`
- `8001` 跨域访问

需要新增覆盖：

- `GET /v1/sessions` 能返回已创建的 session。
- session 列表按 `updated_at` 倒序。
- session 列表包含 `message_count`、`title` 或可展示摘要。
- `GET /v1/sessions/{session_id}/messages` 能按正序返回历史消息。
- 不存在的 session 返回 404。
- 切换到历史 session 后继续调用 `/v1/chat/stream`，后端能沿用该 session。

### 2. 手工 API 验证

启动 API：

```bash
uv run agent serve
```

验证健康检查：

```bash
curl http://127.0.0.1:8000/health
```

验证流式接口：

```bash
curl -N -X POST http://127.0.0.1:8000/v1/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message":"请介绍你自己","provider":"mock"}'
```

预期结果：

- 能看到 `type: "delta"`。
- 能看到 `type: "done"`。
- `done.result.session_id` 存在。

验证 session 列表：

```bash
curl http://127.0.0.1:8000/v1/sessions
```

验证 session 历史消息：

```bash
curl http://127.0.0.1:8000/v1/sessions/<session_id>/messages
```

### 3. 前端本地启动验证

启动静态前端：

```bash
python3 -m http.server 8001 -d src/web
```

浏览器打开：

```text
http://127.0.0.1:8001
```

手工检查：

- 页面能正常加载。
- 输入“你好”后能显示用户消息。
- Agent 能流式返回内容。
- 连续发送第二条消息时复用同一个 `session_id`。
- 左侧会话栏能出现刚创建的 session。
- 点击“新建会话”后页面进入空对话状态。
- 切换回旧 session 后能看到历史消息。
- 切换回旧 session 后继续发送消息，能续着历史对话。
- 清空按钮能清空页面并重置当前会话。
- 后端关闭时，页面能展示可理解的错误提示。

### 4. 跨域验证

浏览器开发者工具中确认：

- 请求地址为 `http://127.0.0.1:8000/v1/chat/stream`。
- 请求 Origin 为 `http://127.0.0.1:8001`。
- 没有 CORS 报错。

如果出现 CORS 报错，优先检查前端实际启动端口是否为 `8001`。

### 5. 视觉与响应式检查

至少检查两个视口：

- 桌面宽度，例如 1440 x 900。
- 手机宽度，例如 390 x 844。

检查点：

- 输入区固定在底部或始终容易访问。
- 会话侧边栏在桌面端可见，移动端可折叠或移动到顶部。
- 会话历史很多时，只有会话列表区域出现滚动条，页面整体高度不被左侧历史记录撑长。
- 消息文本不溢出气泡。
- 长文本自动换行。
- 按钮文字不重叠。
- 页面滚动行为自然，最新消息可见。

## 实施顺序

1. 扩展 `SQLiteSessionStore`，增加 session 列表和历史消息查询能力。
2. 在 `domain/models.py` 或 API 层增加 session summary 和历史消息响应模型。
3. 在 `application.py` 增加查询方法，或由 API 层直接调用 store。
4. 在 `interfaces/api.py` 新增 `GET /v1/sessions` 和 `GET /v1/sessions/{session_id}/messages`。
5. 为新增 session API 补充集成测试。
6. 新增 `src/web/index.html`，完成带会话侧边栏的基础布局和静态样式。
7. 接入 `/v1/chat/stream`，完成流式读取和消息追加。
8. 接入 session 列表、历史消息读取、新建会话、切换会话。
9. 增加错误态、发送中状态、空消息校验。
10. 手工跑通 `mock` provider 的新建、切换、续聊流程。
11. 运行 `uv run pytest` 确认后端测试全部通过。
12. 可选更新 README，补充 Web 前端启动方式。

## 风险与注意事项

- 浏览器原生 `EventSource` 不适合这里，因为当前接口是 `POST`，所以前端应使用 `fetch` + stream reader。
- SSE 数据可能被拆包，解析时不能假设一次 `reader.read()` 就是一条完整事件，需要缓存未完成文本。
- `session_id` 必须取自 `done.result.session_id`，不能由前端自己生成。
- 切换 session 时，前端必须先停止或等待当前流式请求完成，避免把 delta 写入错误的会话窗口。
- session 列表标题第一版可以由第一条用户消息生成，不建议第一版引入可编辑标题字段。
- 历史消息接口应过滤或正确展示 `tool` 消息，避免工具调用结果让普通用户困惑；第一版可以只展示 `user` 和 `assistant`，但后端最好保留完整返回能力。
- 第一版建议默认 provider 为 `mock`，确保无需 API Key 也能体验完整流程。
- 不要把前端做成依赖 Node/Vite 的项目，保持 starter 项目轻量。
