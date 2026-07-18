# Starter Agent 学生版

这是一个可以直接运行、逐步改造的个人 Agent 入门工程。你会从一个最小可用
Agent 开始，先理解它怎么聊天、怎么保存会话、怎么调用工具，再把它改造成
自己的个人 Agent。

## 你会得到什么

- 一个命令行 Agent：可以在终端里对话。
- 一个本地 Web API：可以用浏览器、curl 或前端页面调用。
- 一个 Mock Provider：不需要 API Key，也能练习 Agent Loop 和工具调用。
- 一个 OpenAI-compatible Provider：后续可接入 OpenAI、DeepSeek、通义、
  智谱、TokenRouter 或本地 Ollama。
- 一个内置工具 `get_current_time`：用于观察模型如何请求工具、读取结果、
  再继续回答。
- 一组测试：用于确认你的改动没有破坏基础功能。

## 运行环境

| 环境 | 要求 | 说明 |
|---|---|---|
| Python | 3.11 或 3.12 | 推荐用 uv 安装和管理 |
| uv | 最新稳定版 | 负责安装依赖、运行命令和测试 |
| Git | 推荐 | 用于保存每次课程作业 |
| curl | 可选 | 用于测试本地 API |
| 模型 API Key | 可选 | 只在接入真实模型时需要 |

使用 `mock` provider 时不需要 API Key，也不需要联网调用模型。

## 第一次启动

进入项目目录：

```bash
cd starter-agent-student
```

如果没有安装 uv，先安装：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows PowerShell 使用：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

准备本地配置：

```bash
cp config/config.example.yaml config/config.yaml
cp .env.example .env
```

Windows PowerShell 使用：

```powershell
Copy-Item config/config.example.yaml config/config.yaml
Copy-Item .env.example .env
```

安装依赖并检查环境：

```bash
uv sync --python 3.11 --extra dev
uv run agent doctor
```

运行一次模型连通性测试：

```bash
uv run agent model test --provider mock
```

开始聊天：

```bash
uv run agent chat --provider mock
```

可以试着输入：

```text
现在几点？
```

如果一切正常，Agent 会通过 `get_current_time` 工具获取当前时间。

## 常用命令

```bash
uv run agent doctor
uv run agent chat "请介绍你自己" --provider mock
uv run agent chat --provider mock
uv run agent model list
uv run agent model test --provider mock
uv run agent tools list
uv run agent serve
uv run pytest
```

## 启动后如何验证和使用

### 1. 验证本地环境

```bash
uv run agent doctor
```

预期看到 `Config`、`Identity`、`Data directory`、`Default provider` 都是
`OK`。

### 2. 验证模型 Provider

```bash
uv run agent model list
uv run agent model test --provider mock
```

`mock` provider 不需要 API Key。预期看到类似
`OK Mock provider ready (starter-mock)`。

### 3. 验证工具是否启用

```bash
uv run agent tools list
```

预期能看到 `get_current_time`。这是当前内置的只读工具。

### 4. 验证命令行聊天

单轮对话：

```bash
uv run agent chat "请介绍你自己" --provider mock
```

交互式对话：

```bash
uv run agent chat --provider mock
```

进入交互后输入：

```text
现在几点？
```

如果返回中显示 `tools=1`，说明 Agent 成功调用了工具。退出交互输入
`/exit`。

### 5. 验证测试用例

```bash
uv run pytest
```

预期全部测试通过。

启动 API 服务后，默认地址是 `http://127.0.0.1:8000`：

```bash
uv run agent serve
```

另开一个终端测试：

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"现在几点？","provider":"mock"}'
```

也可以验证流式接口：

```bash
curl -N -X POST http://127.0.0.1:8000/v1/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message":"请介绍你自己","provider":"mock"}'
```

## 你的第一个改造任务

编辑 `docs/agent.md`，把 Starter Agent 改成你自己的个人 Agent：

- 它叫什么名字？
- 它主要帮助谁？
- 它最重要的任务是什么？
- 哪些事情它不能做？
- 哪些操作必须先问你确认？

保存后重新运行：

```bash
uv run agent chat "请介绍你自己" --provider mock
```

Agent 会读取最新的 `docs/agent.md`，不需要改 Python 代码。

## 接入真实模型

1. 打开 `.env`，填入对应供应商的 API Key。
2. 打开 `config/config.yaml`，把 `model.default_provider` 改成要使用的
   provider。
3. 确认 `model.default_model` 是供应商支持的模型名称。
4. 运行连通性测试。

示例：

```bash
uv run agent model test --provider deepseek --model deepseek-chat
```

`config/config.example.yaml` 里的地址只是课程示例。真实项目中，请以供应商
当前官方文档为准。不要把 `.env` 提交或发给别人。

## 项目结构

```text
config/              配置和 system prompt
docs/                Agent 身份和架构说明
src/starter_agent/
  agent/             上下文构建和 Agent Runtime
  providers/         Mock 与 OpenAI-compatible Provider
  tools/             工具定义、注册和策略检查
  infrastructure/    SQLite 会话存储
  interfaces/        CLI 与 FastAPI
tests/               单元测试与集成测试
```

## 交作业前检查

```bash
uv run agent doctor
uv run agent model test --provider mock
uv run pytest
```

确认不要提交或打包这些本地文件：

- `.env`
- `.venv/`
- `data/agent.db`
- `logs/agent.jsonl`
- `__pycache__/`
- `.pytest_cache/`

这些文件已经写在 `.gitignore` 中，正常使用 Git 时会被忽略。

## SerpAPI 岗位搜索

在 `.env` 中配置真实凭据（不要提交该文件）：

```dotenv
SERPAPI_API_KEY=
SERPAPI_API_KEY_BACKUP=
SERPAPI_ACTIVE_KEY=primary
```

`config/config.yaml` 中的 `tools.serpapi.keys` 只保存环境变量名称。切换备用
profile 时将 `SERPAPI_ACTIVE_KEY` 设置为 `backup`，工具会读取
`SERPAPI_API_KEY_BACKUP`，不会把真实 Key 写入工具结果或日志。

使用 GLM-4.7 搜索悉尼岗位：

```bash
uv run agent chat "请搜索悉尼的 AI Agent 工程师岗位，返回 5 条带来源和检索时间的结果" \
  --provider zhipu --model glm-4.7
```

搜索结果是公开网页线索，不代表岗位仍然有效；投递前应打开来源页面复核。
