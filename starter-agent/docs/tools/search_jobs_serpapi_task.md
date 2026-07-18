# `search_jobs_serpapi` 单一任务书

## 1. 任务目标

在 Starter Agent 中实现一个只负责搜索公开岗位的 Tool：

```text
search_jobs_serpapi
```

该工具通过 SerpAPI 获取带来源和检索时间的岗位线索。它不读取简历、不做岗位匹配、
不修改材料，也不投递岗位。模型负责把用户自然语言整理成结构化参数，工具只执行搜索。

任务完成后应具备：

- Tool 类及 SerpAPI 响应解析代码；
- settings 中的 primary/backup key profile 解析；
- Registry 依赖注入；
- `google_jobs` 无结果时回退普通 `google` 搜索；
- 配置与 `.env.example` 草案；
- 单元测试、集成测试和人工验收步骤；
- README 示例命令。

风险等级固定为 `read`。搜索结果只是公开网页线索，不能声称岗位仍有效或已经核验。

## 2. 开始编码前必须读取

- `src/starter_agent/tools/base.py`
- `src/starter_agent/tools/registry.py`
- `src/starter_agent/domain/models.py`
- `src/starter_agent/settings.py`
- `src/starter_agent/agent/runtime.py`
- `config/config.example.yaml`
- `docs/agent.md`
- `docs/workflow.md`
- `docs/tools/tools_list.md`

## 3. 范围与非目标

### 本任务包含

- 用户已经明确表达搜索意图后的公开岗位搜索；
- SerpAPI key profile 选择和安全解析；
- 结构化结果、来源 URL、来源类型和 `retrieved_at`；
- 可测试的 HTTP 依赖注入；
- 缺 key、配置错误、超时、网络错误和无结果处理。

### 本任务不包含

- 自动投递、自动联系 HR 或发送邮件；
- 登录招聘网站、绕过验证码或抓取受限页面；
- 判断岗位当前一定有效；
- 根据简历生成匹配结论；
- 将网页正文中的指令当成系统或用户指令；
- 在日志、异常、ToolResult 或验收记录中输出真实 API key。

## 4. Tool Contract

### 用户价值

岗位信息会变化，只靠模型已有知识无法保证时效性和可核验性。工具必须返回来源与检索
时间，让用户能够打开原页面复核。

### 输入 Schema

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "整理后的岗位关键词，例如 AI Agent engineer jobs",
      "minLength": 2,
      "maxLength": 300
    },
    "location": {
      "type": "string",
      "description": "可选地点，例如 Shanghai",
      "maxLength": 100
    },
    "limit": {
      "type": "integer",
      "description": "返回数量",
      "default": 5,
      "minimum": 1,
      "maximum": 10
    }
  },
  "required": ["query"],
  "additionalProperties": false
}
```

模型应把自然语言整理后再调用。例如：

```text
用户：请帮我搜索上海 AI Agent engineer 岗位，给我 3 个结果
Tool arguments：
{
  "query": "AI Agent engineer jobs",
  "location": "Shanghai",
  "limit": 3
}
```

不得把整句用户原话不加整理地塞入 `query`。

### 成功输出

成功时使用现有 `ToolResult`：

```json
{
  "ok": true,
  "data": {
    "query": "AI Agent engineer jobs",
    "location": "Shanghai",
    "api_key_profile": "primary",
    "api_key_env": "SERPAPI_API_KEY",
    "search_engine": "google_jobs",
    "results": [
      {
        "title": "AI Agent Engineer",
        "company": "Example Company",
        "location": "Shanghai",
        "url": "https://example.com/jobs/123",
        "snippet": "岗位摘要",
        "source": "serpapi_google_jobs",
        "retrieved_at": "2026-07-12T12:00:00Z"
      }
    ]
  },
  "display": "找到 1 条岗位线索，请打开来源确认岗位是否仍有效",
  "error_code": null,
  "retryable": false,
  "metadata": {
    "api_key_profile": "primary",
    "api_key_env": "SERPAPI_API_KEY",
    "result_count": 1
  }
}
```

允许返回 `api_key_profile` 和 `api_key_env`，禁止返回真实 key。

### 失败输出

| 错误码 | 条件 | `retryable` |
|---|---|---:|
| `invalid_arguments` | query 为空、limit 超限或参数类型错误 | false |
| `missing_api_key` | active profile 不存在或其环境变量缺失 | false |
| `authentication_failed` | SerpAPI 返回 key 无效 | false |
| `rate_limited` | 请求频率受限 | true |
| `quota_exceeded` | 套餐额度不足 | false |
| `search_timeout` | 请求超时 | true |
| `search_failed` | 网络或上游异常 | true |
| `invalid_provider_response` | 响应不是预期 JSON | true |
| `no_results` | `google_jobs` 和普通 `google` 均无结果 | false |

缺少凭据的标准结果：

```json
{
  "ok": false,
  "display": "当前 SerpAPI 凭据未配置",
  "error_code": "missing_api_key",
  "retryable": false,
  "metadata": {
    "api_key_profile": "backup",
    "api_key_env": "SERPAPI_API_KEY_BACKUP"
  }
}
```

## 5. 凭据与配置设计

### YAML 草案

`config/config.example.yaml` 只保存 profile 和环境变量名称：

```yaml
tools:
  enabled:
    - get_current_time
    - search_jobs_serpapi
  allow_risk_levels:
    - read
  serpapi:
    active_key: primary
    active_key_env: SERPAPI_ACTIVE_KEY
    timeout_seconds: 15
    keys:
      primary:
        api_key_env: SERPAPI_API_KEY
      backup:
        api_key_env: SERPAPI_API_KEY_BACKUP
```

### `.env.example` 草案

只能列变量名和空值：

```dotenv
SERPAPI_API_KEY=
SERPAPI_API_KEY_BACKUP=
# 可选值：primary 或 backup
SERPAPI_ACTIVE_KEY=
```

真实 key 只能放在未提交的 `.env`、系统环境变量或 secret manager 中。

### profile 解析规则

1. 从 `tools.serpapi.active_key_env` 得到覆盖变量名，默认 `SERPAPI_ACTIVE_KEY`；
2. 如果该环境变量有值，使用其值作为 active profile；
3. 否则使用 `tools.serpapi.active_key`，默认 `primary`；
4. 从 `tools.serpapi.keys[profile].api_key_env` 得到真实 key 对应的环境变量名；
5. 读取该环境变量；
6. profile 不存在或变量为空时失败，不得悄悄切换到其他 profile。

`settings.py` 对外提供：

```python
def serpapi_api_key(self) -> tuple[str, str | None, str | None]:
    """Return (profile, api_key, api_key_env) without logging the key."""
```

## 6. 预计修改文件

```text
src/starter_agent/settings.py
src/starter_agent/tools/registry.py
src/starter_agent/tools/builtin/job_search.py       # 新增
config/config.example.yaml
.env.example
README.md
tests/unit/test_serpapi_settings.py                 # 新增
tests/unit/test_search_jobs_serpapi.py              # 新增
tests/integration/test_serpapi_tool_registration.py # 新增
docs/tool_acceptance.md
```

`httpx` 当前只在 dev dependencies 中；实现时应将其加入项目运行时依赖，或选用已有的
运行时 HTTP 客户端。不得依赖只有测试环境才安装的包。

## 7. settings 代码草案

以下是实现方向，Coding Agent 应根据当前 Pydantic 版本调整并运行测试：

```python
from __future__ import annotations

import os
from pydantic import BaseModel, Field


class SerpApiKeyConfig(BaseModel):
    api_key_env: str


class SerpApiToolConfig(BaseModel):
    active_key: str = "primary"
    active_key_env: str = "SERPAPI_ACTIVE_KEY"
    timeout_seconds: float = Field(default=15, gt=0, le=60)
    keys: dict[str, SerpApiKeyConfig] = Field(default_factory=dict)


class ToolsConfig(BaseModel):
    enabled: list[str] = Field(default_factory=lambda: ["get_current_time"])
    allow_risk_levels: list[str] = Field(default_factory=lambda: ["read"])
    serpapi: SerpApiToolConfig = Field(default_factory=SerpApiToolConfig)


class AgentSettings(BaseModel):
    # 保留现有字段

    def serpapi_api_key(self) -> tuple[str, str | None, str | None]:
        config = self.tools.serpapi
        profile = os.getenv(config.active_key_env) or config.active_key
        selected = config.keys.get(profile)
        if selected is None:
            return profile, None, None
        api_key_env = selected.api_key_env
        api_key = os.getenv(api_key_env)
        if not api_key:
            api_key = self._project_env_value(api_key_env)  # 复用统一 .env 解析
        return profile, api_key, api_key_env
```

实现时应把当前 `provider_api_key()` 中读取项目 `.env` 的逻辑抽成统一私有方法，避免
SerpAPI 和模型 Provider 各自实现一遍。私有方法不得记录读取到的值。

## 8. Tool 类代码草案

建议新增 `src/starter_agent/tools/builtin/job_search.py`：

```python
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from starter_agent.domain.models import ToolResult
from starter_agent.tools.base import Tool, ToolContext


KeyResolver = Callable[[], tuple[str, str | None, str | None]]


class AsyncJsonClient(Protocol):
    async def get(self, url: str, *, params: dict[str, Any], timeout: float): ...


SENSITIVE_QUERY_KEYS = {"api_key", "apikey", "key", "token", "access_token"}


def sanitize_url(value: str) -> str:
    if not value:
        return ""
    split = urlsplit(value)
    query = [
        (key, item)
        for key, item in parse_qsl(split.query, keep_blank_values=True)
        if key.lower() not in SENSITIVE_QUERY_KEYS
    ]
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), ""))


class SearchJobsSerpApiTool(Tool):
    name = "search_jobs_serpapi"
    description = (
        "Search public job listings with sources and retrieval timestamps. "
        "Results are leads and must be verified on the source page."
    )
    risk_level = "read"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 2, "maxLength": 300},
            "location": {"type": "string", "maxLength": 100},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        key_resolver: KeyResolver | None = None,
        *,
        client: AsyncJsonClient | None = None,
        timeout: float = 15,
    ) -> None:
        self.key_resolver = key_resolver or self._fallback_key_resolver
        self.client = client or httpx.AsyncClient()
        self.timeout = timeout

    @staticmethod
    def _fallback_key_resolver() -> tuple[str, str | None, str]:
        # 仅为独立单元测试/向后兼容保留；生产 Registry 必须注入 settings resolver。
        import os
        return "primary", os.getenv("SERPAPI_API_KEY"), "SERPAPI_API_KEY"

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        parsed = self._validate_arguments(arguments)
        if isinstance(parsed, ToolResult):
            return parsed
        query, location, limit = parsed

        profile, api_key, api_key_env = self.key_resolver()
        safe_meta = {
            "api_key_profile": profile,
            "api_key_env": api_key_env,
        }
        if not api_key or not api_key_env:
            return ToolResult(
                ok=False,
                display="当前 SerpAPI 凭据未配置",
                error_code="missing_api_key",
                metadata=safe_meta,
            )

        retrieved_at = datetime.now(UTC).isoformat()
        try:
            jobs = await self._request(
                engine="google_jobs",
                query=query,
                location=location,
                api_key=api_key,
            )
            results = self._parse_google_jobs(jobs, retrieved_at)
            search_engine = "google_jobs"

            if not results:
                google_query = " ".join(
                    part for part in (query, location, "jobs") if part
                )
                generic = await self._request(
                    engine="google",
                    query=google_query,
                    location=location,
                    api_key=api_key,
                )
                results = self._parse_google(generic, retrieved_at)
                search_engine = "google"
        except httpx.TimeoutException:
            return ToolResult(
                ok=False,
                display="岗位搜索超时，请稍后重试",
                error_code="search_timeout",
                retryable=True,
                metadata=safe_meta,
            )
        except httpx.HTTPError:
            return ToolResult(
                ok=False,
                display="岗位搜索服务暂时不可用",
                error_code="search_failed",
                retryable=True,
                metadata=safe_meta,
            )

        results = results[:limit]
        if not results:
            return ToolResult(
                ok=False,
                display="没有找到可用的岗位搜索结果",
                error_code="no_results",
                metadata={**safe_meta, "retrieved_at": retrieved_at},
            )

        data = {
            "query": query,
            "location": location,
            "api_key_profile": profile,
            "api_key_env": api_key_env,
            "search_engine": search_engine,
            "results": results,
        }
        return ToolResult(
            ok=True,
            data=data,
            display=f"找到 {len(results)} 条岗位线索，请打开来源确认岗位是否仍有效",
            metadata={**safe_meta, "result_count": len(results)},
        )

    def _validate_arguments(
        self, arguments: dict[str, Any]
    ) -> tuple[str, str, int] | ToolResult:
        query = arguments.get("query")
        location = arguments.get("location", "")
        limit = arguments.get("limit", 5)
        if (
            not isinstance(query, str)
            or not 2 <= len(query.strip()) <= 300
            or not isinstance(location, str)
            or len(location) > 100
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 10
        ):
            return ToolResult(
                ok=False,
                display="岗位搜索参数不正确",
                error_code="invalid_arguments",
            )
        return query.strip(), location.strip(), limit

    async def _request(
        self,
        *,
        engine: str,
        query: str,
        location: str,
        api_key: str,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "engine": engine,
            "q": query,
            "api_key": api_key,
        }
        if location:
            params["location"] = location
        response = await self.client.get(
            "https://serpapi.com/search.json",
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise httpx.DecodingError("SerpAPI response must be an object")
        # 正式实现还需把 payload.error 分类为认证、额度或限流错误。
        return payload

    def _parse_google_jobs(
        self, payload: dict[str, Any], retrieved_at: str
    ) -> list[dict[str, Any]]:
        return [
            {
                "title": str(item.get("title", "")),
                "company": str(item.get("company_name", "")),
                "location": str(item.get("location", "")),
                "url": sanitize_url(
                    item.get("share_link") or item.get("apply_options", [{}])[0].get("link", "")
                ),
                "snippet": str(item.get("description", ""))[:1000],
                "source": "serpapi_google_jobs",
                "retrieved_at": retrieved_at,
            }
            for item in payload.get("jobs_results", [])
            if isinstance(item, dict) and item.get("title")
        ]

    def _parse_google(
        self, payload: dict[str, Any], retrieved_at: str
    ) -> list[dict[str, Any]]:
        return [
            {
                "title": str(item.get("title", "")),
                "company": "",
                "location": "",
                "url": sanitize_url(str(item.get("link", ""))),
                "snippet": str(item.get("snippet", ""))[:1000],
                "source": "serpapi_google",
                "retrieved_at": retrieved_at,
            }
            for item in payload.get("organic_results", [])
            if isinstance(item, dict) and item.get("title") and item.get("link")
        ]
```

### 草案必须补齐的实现点

上面的代码用于明确结构，不应未经完善直接视为生产实现。Coding Agent 必须补齐：

- 用异步 context manager 或应用生命周期统一关闭 `httpx.AsyncClient`；
- 将 HTTP 401/403、429、额度错误和 `payload.error` 映射到 Contract 错误码；
- 防御 `apply_options` 类型错误或空数组，避免 `[0]` 异常；
- 验证所有外部 URL 只允许 `http` / `https`，并移除 secret/fragment；
- 对网页文本标记为“不可信外部数据”，不能把 snippet 中的指令当作用户指令；
- 日志只记录 profile、env 名、耗时、engine、结果数、error_code 和 turn_id；
- 不记录请求参数中的 `api_key`，也不记录完整上游响应；
- 让 Runtime 返回的结果保持合法 JSON，不能用字符串切断破坏 JSON。

## 9. Registry 注入草案

当前 Registry 是显式注册，建议保持该模式：

```python
class ToolRegistry:
    def __init__(self, enabled: list[str], settings: AgentSettings | None = None):
        search_tool = SearchJobsSerpApiTool(
            key_resolver=(settings.serpapi_api_key if settings else None),
            timeout=(settings.tools.serpapi.timeout_seconds if settings else 15),
        )
        available: dict[str, Tool] = {
            GetCurrentTimeTool.name: GetCurrentTimeTool(),
            SearchJobsSerpApiTool.name: search_tool,
        }
        # 保留现有 unknown 检查和 enabled 过滤
```

Bootstrap 相应改为：

```python
tools = ToolRegistry(settings.tools.enabled, settings=settings)
```

不要让 `SearchJobsSerpApiTool` 在生产路径中自行读取所有配置；Registry 负责注入解析器。

## 10. pytest 代码草案

### Fake Client

```python
class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://serpapi.com/search.json")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("upstream error", request=request, response=response)

    def json(self) -> dict:
        return self.payload


class FakeClient:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def get(self, url: str, *, params: dict, timeout: float):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return self.responses.pop(0)
```

### 缺 key

```python
async def test_missing_active_profile_key_returns_safe_error() -> None:
    tool = SearchJobsSerpApiTool(
        key_resolver=lambda: ("backup", None, "SERPAPI_API_KEY_BACKUP"),
        client=FakeClient([]),
    )
    result = await tool.execute(
        {"query": "AI Agent jobs"},
        ToolContext(session_id=uuid4(), turn_id=uuid4()),
    )

    assert not result.ok
    assert result.error_code == "missing_api_key"
    assert result.metadata["api_key_profile"] == "backup"
    assert result.metadata["api_key_env"] == "SERPAPI_API_KEY_BACKUP"
    assert "real-secret" not in result.model_dump_json()
```

### active profile 切换

```python
def test_serpapi_active_profile_can_be_overridden(settings, monkeypatch) -> None:
    monkeypatch.setenv("SERPAPI_ACTIVE_KEY", "backup")
    monkeypatch.setenv("SERPAPI_API_KEY_BACKUP", "backup-secret-for-test")

    profile, key, env_name = settings.serpapi_api_key()

    assert profile == "backup"
    assert key == "backup-secret-for-test"
    assert env_name == "SERPAPI_API_KEY_BACKUP"
```

还必须增加反向断言：`ToolResult`、结构化日志和异常文本中均不包含
`backup-secret-for-test`。

### 正常 `google_jobs`

```python
async def test_google_jobs_result_contains_source_and_timestamp() -> None:
    client = FakeClient([
        FakeResponse({
            "jobs_results": [{
                "title": "AI Agent Engineer",
                "company_name": "Example",
                "location": "Shanghai",
                "share_link": "https://jobs.example/1?token=secret",
                "description": "Build agent systems",
            }]
        })
    ])
    tool = SearchJobsSerpApiTool(
        key_resolver=lambda: ("primary", "test-secret", "SERPAPI_API_KEY"),
        client=client,
    )
    result = await tool.execute(
        {"query": "AI Agent jobs", "location": "Shanghai", "limit": 1},
        ToolContext(session_id=uuid4(), turn_id=uuid4()),
    )

    assert result.ok
    assert result.data["api_key_profile"] == "primary"
    assert result.data["api_key_env"] == "SERPAPI_API_KEY"
    assert result.data["results"][0]["source"] == "serpapi_google_jobs"
    assert result.data["results"][0]["retrieved_at"]
    assert "token=" not in result.data["results"][0]["url"]
    assert "test-secret" not in result.model_dump_json()
```

### fallback 到普通 Google

```python
async def test_empty_google_jobs_falls_back_to_google() -> None:
    client = FakeClient([
        FakeResponse({"jobs_results": []}),
        FakeResponse({
            "organic_results": [{
                "title": "AI Engineer - Example",
                "link": "https://example.com/careers/1",
                "snippet": "Shanghai role",
            }]
        }),
    ])
    tool = SearchJobsSerpApiTool(
        key_resolver=lambda: ("primary", "test-secret", "SERPAPI_API_KEY"),
        client=client,
    )
    result = await tool.execute(
        {"query": "AI Agent jobs", "location": "Shanghai", "limit": 3},
        ToolContext(session_id=uuid4(), turn_id=uuid4()),
    )

    assert result.ok
    assert len(client.calls) == 2
    assert client.calls[0]["params"]["engine"] == "google_jobs"
    assert client.calls[1]["params"]["engine"] == "google"
    assert result.data["search_engine"] == "google"
    assert result.data["results"][0]["source"] == "serpapi_google"
```

### 参数边界

```python
@pytest.mark.parametrize("limit", [0, 11, -1, True, "5"])
async def test_invalid_limit_is_rejected(limit) -> None:
    tool = SearchJobsSerpApiTool(
        key_resolver=lambda: ("primary", "test-secret", "SERPAPI_API_KEY"),
        client=FakeClient([]),
    )
    result = await tool.execute(
        {"query": "AI Agent jobs", "limit": limit},
        ToolContext(session_id=uuid4(), turn_id=uuid4()),
    )
    assert result.error_code == "invalid_arguments"
```

### 必须覆盖的完整测试矩阵

| 测试 | 必须断言 |
|---|---|
| 默认 primary | 使用 `SERPAPI_API_KEY` |
| 环境覆盖 backup | 使用 `SERPAPI_API_KEY_BACKUP` |
| 不存在的 active profile | `missing_api_key`，不回退 primary |
| 当前 profile 缺 key | `missing_api_key`，不发送 HTTP 请求 |
| 正常 google_jobs | 字段完整、来源和时间存在 |
| google_jobs 空 | 发起第二次普通 google 请求 |
| 两次均空 | `no_results` |
| limit 为 0/11/错误类型 | `invalid_arguments` |
| HTTP 超时 | `search_timeout`、`retryable=true` |
| 网络失败 | `search_failed` |
| 401/403 | `authentication_failed` |
| 429 | `rate_limited` 或 `quota_exceeded` |
| 非法 JSON/结构 | `invalid_provider_response` |
| URL 带 key/token | 返回 URL 已脱敏 |
| secret 泄露扫描 | 结果、日志、异常均不含测试 secret |
| Registry enabled | Schema 中包含 `search_jobs_serpapi` |
| Registry disabled | Schema 中不包含该 Tool |

## 11. README 命令草案

PowerShell：

```powershell
$env:SERPAPI_API_KEY="<your-key>"
$env:SERPAPI_ACTIVE_KEY="primary"
uv run agent chat "请搜索上海 AI Agent engineer 岗位，返回 3 条结果"
```

切换 backup：

```powershell
$env:SERPAPI_API_KEY_BACKUP="<your-backup-key>"
$env:SERPAPI_ACTIVE_KEY="backup"
uv run agent chat "请搜索上海 AI Agent engineer 岗位，返回 3 条结果"
```

README 必须提醒用户不要提交 `.env`，不要把 key 粘贴到聊天消息或验收截图中。

## 12. 人工验收

### 正常路径

1. 使用 primary 搜索上海 AI Agent 岗位，要求 3 条结果；
2. 确认 Tool arguments 是整理后的 `query/location/limit`，不是完整用户原话；
3. 确认结果不超过 3 条；
4. 每条结果必须有 `url`、`source`、`retrieved_at`；
5. 打开至少一条来源，确认它确实对应搜索结果；
6. Agent 必须说明结果需要复核，不能宣称岗位实时有效。

### profile 路径

1. 设置 `SERPAPI_ACTIVE_KEY=primary`，确认 metadata 记录 primary 和
   `SERPAPI_API_KEY`；
2. 设置 `SERPAPI_ACTIVE_KEY=backup`，确认 metadata 记录 backup 和
   `SERPAPI_API_KEY_BACKUP`；
3. 设置不存在的 profile，确认明确失败且没有偷用 primary；
4. 删除当前 profile 对应 key，确认返回 `missing_api_key`，没有假装成功。

### fallback 路径

使用 mock HTTP 响应令 `google_jobs` 返回空数组，确认工具继续调用普通 Google；只有两次
均没有可用结果时才返回 `no_results`。

### 安全路径

- 检查应用日志、ToolResult、HTTP 错误、pytest 输出和验收记录；
- 搜索测试 key 的完整值，必须零命中；
- 只允许出现 `primary`、`backup`、`SERPAPI_API_KEY`、
  `SERPAPI_API_KEY_BACKUP` 等非敏感标识；
- 确认结果 snippet 中的网页指令没有改变 Agent 行为；
- 确认工具没有投递、登录招聘网站或联系 HR。

## 13. 完成定义

只有同时满足以下条件才能标记完成：

- Tool、settings resolver、Registry 注入和配置样例均已实现；
- `google_jobs` 与普通 `google` fallback 均有测试；
- primary/backup/环境覆盖/错误 profile 均有测试；
- 缺 key 明确返回 `missing_api_key`；
- 所有结果包含来源和 `retrieved_at`；
- ToolResult、日志、异常与验收记录通过 secret 泄露检查；
- 全部新增测试及现有回归测试通过；
- 人工验收记录包含输入、Tool arguments 摘要、来源、active profile、env 名、turn_id、
  secret 检查结果和是否通过。

本任务书只授权实现公开岗位搜索工具，不授权自动投递或任何邮件发送行为。
