# RAG 最小结构化重试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当 RAG 模型首次返回不可解析的结构时，使用相同问题和 Evidence 严格重试一次，使“我的简历匹配哪个岗位”能够返回经过引用校验的回答。

**Architecture:** 只在 `RagGenerator` 内拆分“消息构造、结构解析、一次重试”三个内部步骤。首次结构失败后重新调用同一 Provider 和模型；Citation 与 Provider 错误位于重试边界之外，第二次结构失败后继续返回现有 `generation_invalid_output`。

**Tech Stack:** Python 3.11+、Pydantic 2、pytest、pytest-asyncio

## Global Constraints

- 只修改 RAG Generation 和对应测试，不修改 Provider 通用接口。
- 每次 RAG 回答最多调用模型两次，不得发生第三次调用。
- 只有 JSON、Pydantic 或非法 status 结构错误允许重试。
- Provider、Retrieval、Evidence Gate 和 Citation 错误不得触发结构重试。
- 第二次请求使用同一 Provider、模型、问题和 Evidence，并保持 `tools=[]`。
- 第二次输出仍必须通过 Evidence ID 和逐字连续 quote 校验。
- 不增加宽松 JSON 提取、模糊 quote 或未经校验的回答回退。
- 测试只使用无敏感信息的 fixture，pytest 临时目录必须位于系统临时目录。

---

## File Map

- Modify: `src/starter_agent/knowledge/generation.py`
  - 提取严格结构解析，构造首次与重试 Prompt，并将 Citation 校验放在重试边界之外。
- Modify: `tests/unit/test_rag_generation.py`
  - 覆盖一次重试预算、Prompt 内容、Provider 错误和 Citation 错误。
- Modify: `tests/integration/test_rag_natural_query.py`
  - 验证自然语言简历/JD 匹配在首次结构失败后能够生成双文档引用。

### Task 1: 在 RagGenerator 中实现一次严格重试

**Files:**
- Modify: `src/starter_agent/knowledge/generation.py`
- Modify: `tests/unit/test_rag_generation.py`

**Interfaces:**
- Consumes: `Provider.complete(messages, model, tools=[]) -> ModelResponse`.
- Consumes: `build_evidence_context(evidence: list[Evidence]) -> str`.
- Produces: `_validate_payload(content: str) -> _GeneratedPayload`.
- Produces: `_build_messages(question: str, evidence: list[Evidence], retry_reason: str | None = None) -> list[Message]`.
- Produces: `RagGenerator.generate()` 最多两次 Provider 调用的稳定行为。

- [ ] **Step 1: 让测试 Provider 支持顺序响应并记录调用**

将 `tests/unit/test_rag_generation.py` 中的 `StubProvider` 替换为：

```python
class StubProvider:
    name = "stub"

    def __init__(self, *contents: str) -> None:
        self.contents = list(contents) or [_valid_payload()]
        self.fallback_content = self.contents[-1]
        self.calls = []
        self.tools = None
        self.messages = None

    async def complete(self, messages, model, tools, **kwargs):
        self.tools = tools
        self.messages = messages
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "tools": tools,
            }
        )
        content = (
            self.contents.pop(0)
            if self.contents
            else self.fallback_content
        )
        return ModelResponse(
            content=content,
            provider="stub",
            model=model,
        )
```

该实现保留现有 `tools` 和 `messages` 属性，因此原有断言继续有效；当旧测试只提供
一个非法响应时，第二次调用会重复该响应并稳定失败。

- [ ] **Step 2: 写出首次成功不重试的测试**

在 `tests/unit/test_rag_generation.py` 增加：

```python
@pytest.mark.asyncio
async def test_valid_first_response_does_not_retry() -> None:
    provider = StubProvider(_valid_payload())

    answer = await RagGenerator(provider, "test").generate(
        "会 Python 吗？",
        [_evidence()],
    )

    assert answer.status == "answered"
    assert len(provider.calls) == 1
    assert provider.calls[0]["tools"] == []
```

- [ ] **Step 3: 写出首次非 JSON、第二次合法的测试**

增加：

```python
@pytest.mark.asyncio
async def test_invalid_first_response_retries_once() -> None:
    provider = StubProvider(
        "我认为候选人熟练 Python。",
        _valid_payload(),
    )

    answer = await RagGenerator(provider, "test").generate(
        "会 Python 吗？",
        [_evidence()],
    )

    assert answer.status == "answered"
    assert len(provider.calls) == 2
    retry_messages = provider.calls[1]["messages"]
    retry_prompt = "\n".join(
        message.content for message in retry_messages
    )
    assert "只输出一个 JSON 对象" in retry_prompt
    assert '"evidence_refs"' in retry_prompt
    assert '"evidence_id"' in retry_prompt
    assert '"quote"' in retry_prompt
    assert "熟练 Python" in retry_prompt
    assert provider.calls[1]["tools"] == []
```

`熟练 Python` 来自 `_evidence()`，该断言证明第二次请求复用了相同 Evidence。

- [ ] **Step 4: 写出缺字段和混合引用格式的重试测试**

增加：

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_payload",
    [
        json.dumps(
            {
                "status": "answered",
                "answer": "候选人熟练 Python。",
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "status": "answered",
                "answer": "候选人熟练 Python。",
                "claims": [
                    {
                        "text": "候选人熟练 Python。",
                        "evidence_refs": [
                            {
                                "evidence_id": "E1",
                                "quote": "熟练 Python",
                            }
                        ],
                        "evidence_ids": ["E1"],
                        "quote": "熟练 Python",
                    }
                ],
            },
            ensure_ascii=False,
        ),
    ],
)
async def test_schema_errors_retry_once(
    invalid_payload: str,
) -> None:
    provider = StubProvider(
        invalid_payload,
        _valid_payload(),
    )

    answer = await RagGenerator(provider, "test").generate(
        "会 Python 吗？",
        [_evidence()],
    )

    assert answer.status == "answered"
    assert len(provider.calls) == 2
```

- [ ] **Step 5: 写出两次失败后停止的测试**

增加：

```python
@pytest.mark.asyncio
async def test_second_invalid_response_does_not_call_third_time() -> None:
    provider = StubProvider(
        "first invalid response",
        "second invalid response",
    )

    with pytest.raises(KnowledgeError) as error:
        await RagGenerator(provider, "test").generate(
            "会 Python 吗？",
            [_evidence()],
        )

    assert error.value.code == "generation_invalid_output"
    assert len(provider.calls) == 2
```

- [ ] **Step 6: 写出 Provider 错误不重试的测试**

增加导入：

```python
from starter_agent.domain.errors import ProviderTimeoutError
```

增加：

```python
class TimeoutProvider:
    name = "timeout"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, model, tools, **kwargs):
        self.calls += 1
        raise ProviderTimeoutError(
            provider=self.name,
            model=model,
        )


@pytest.mark.asyncio
async def test_provider_error_is_not_retried() -> None:
    provider = TimeoutProvider()

    with pytest.raises(ProviderTimeoutError):
        await RagGenerator(provider, "test").generate(
            "会 Python 吗？",
            [_evidence()],
        )

    assert provider.calls == 1
```

- [ ] **Step 7: 写出 Citation 错误不重试的测试**

增加：

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference",
    [
        {"evidence_id": "E2", "quote": "熟练 Python"},
        {"evidence_id": "E1", "quote": "改写后的 Python 能力"},
    ],
)
async def test_citation_error_is_not_retried(
    reference: dict[str, str],
) -> None:
    payload = json.dumps(
        {
            "status": "answered",
            "answer": "候选人熟练 Python。",
            "claims": [
                {
                    "text": "候选人熟练 Python。",
                    "evidence_refs": [reference],
                }
            ],
        },
        ensure_ascii=False,
    )
    provider = StubProvider(payload)

    with pytest.raises(KnowledgeError) as error:
        await RagGenerator(provider, "test").generate(
            "会 Python 吗？",
            [_evidence()],
        )

    assert error.value.code == "citation_validation_failed"
    assert len(provider.calls) == 1
```

- [ ] **Step 8: 运行新增单元测试并确认失败**

Run:

```powershell
$baseTemp = Join-Path $env:TEMP "starter-agent-rag-retry-red"
.\.venv\Scripts\python.exe -m pytest tests/unit/test_rag_generation.py --basetemp="$baseTemp" -q
```

Expected: 新增的重试测试 FAIL；当前实现只调用一次并立即返回
`generation_invalid_output`。

- [ ] **Step 9: 提取严格结构解析函数**

在 `src/starter_agent/knowledge/generation.py` 中增加：

```python
def _validate_payload(content: str) -> _GeneratedPayload:
    payload = _GeneratedPayload.model_validate_json(
        _normalize_json_envelope(content)
    )
    if payload.status not in {
        "answered",
        "refused",
        "conflict",
    }:
        raise ValueError(payload.status)
    return payload
```

增加结构失败分类：

```python
def _retry_reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        if any(
            item["type"] == "json_invalid"
            for item in exc.errors()
        ):
            return "invalid_json"
        return "schema_validation_failed"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json"
    return "invalid_status"
```

- [ ] **Step 10: 提取首次与重试消息构造**

在 `src/starter_agent/knowledge/generation.py` 增加：

```python
_RETRY_EXAMPLE = {
    "status": "answered",
    "answer": "基于证据的回答",
    "claims": [
        {
            "text": "一项可由证据支持的结论",
            "evidence_refs": [
                {
                    "evidence_id": "E1",
                    "quote": "E1 中的逐字连续原文",
                },
                {
                    "evidence_id": "E2",
                    "quote": "E2 中的逐字连续原文",
                },
            ],
        }
    ],
}


def _build_messages(
    question: str,
    evidence: list[Evidence],
    *,
    retry_reason: str | None = None,
) -> list[Message]:
    retry_instruction = ""
    if retry_reason is not None:
        retry_instruction = (
            "\n上一次输出未通过结构校验，错误类型为 "
            f"{retry_reason}。请重新生成，不要解释错误。"
            "\n只输出一个 JSON 对象，格式示例："
            f"\n{json.dumps(_RETRY_EXAMPLE, ensure_ascii=False)}"
            "\n不得同时输出旧格式 evidence_ids/quote。"
        )
    return [
        Message(
            role="system",
            content=(
                "你是受证据约束的问答器。只输出一个 JSON 对象，"
                "不要输出解释、前言或其他自由文本。JSON 必须包含 "
                "status、answer、claims。status 只能是 "
                '"answered"、"refused"、"conflict"，不要输出 '
                '"success" 或其他状态。每个 claim 必须包含 text 和 '
                '"evidence_refs"；evidence_refs 中每个引用项只对应一个 '
                'Evidence，并包含 "evidence_id" 与 "quote"。'
                "evidence_id 只能使用下方证据中给出的 ID，quote 必须是"
                "该 Evidence 中的非空连续原文，不得改写。多个 Evidence "
                "必须分别提供各自 quote。不得输出文件名、版本、行号或 "
                "Chunk ID，不得使用资料外事实。"
                f"{retry_instruction}"
            ),
        ),
        Message(
            role="user",
            content=(
                f"问题：{question}\n\n"
                f"{build_evidence_context(evidence)}"
            ),
        ),
    ]
```

删除 `generate()` 内原有的内联 `messages` 列表构造，首次调用改用：

```python
messages = _build_messages(question, evidence)
```

- [ ] **Step 11: 实现一次重试并把 Citation 校验移出捕获边界**

用以下控制流替换 `generate()` 中首次 Provider 调用后的 `try/except`：

```python
response = await self.provider.complete(
    messages,
    self.model,
    tools=[],
)
try:
    payload = _validate_payload(response.content or "")
except (
    ValidationError,
    ValueError,
    json.JSONDecodeError,
) as first_error:
    retry_messages = _build_messages(
        question,
        evidence,
        retry_reason=_retry_reason(first_error),
    )
    retry_response = await self.provider.complete(
        retry_messages,
        self.model,
        tools=[],
    )
    try:
        payload = _validate_payload(
            retry_response.content or ""
        )
    except (
        ValidationError,
        ValueError,
        json.JSONDecodeError,
    ) as second_error:
        raise KnowledgeError(
            "generation_invalid_output"
        ) from second_error

citations = assemble_citations(payload.claims, evidence)
return RagAnswer(
    status=payload.status,
    answer=payload.answer,
    claims=payload.claims,
    citations=citations,
)
```

`assemble_citations()` 必须位于两个结构错误捕获块之外，从而确保未知 Evidence ID
和非连续 quote 不触发重试。

- [ ] **Step 12: 运行单元测试并确认通过**

Run:

```powershell
$baseTemp = Join-Path $env:TEMP "starter-agent-rag-retry-green"
.\.venv\Scripts\python.exe -m pytest tests/unit/test_rag_generation.py tests/unit/test_rag_citations.py --basetemp="$baseTemp" -q
```

Expected: PASS；首次成功调用一次，结构失败最多调用两次，Citation 和 Provider
错误只调用一次。

- [ ] **Step 13: 提交最小生成修复**

```powershell
git add src/starter_agent/knowledge/generation.py tests/unit/test_rag_generation.py
git commit -m "fix: retry invalid rag structure once"
```

### Task 2: 验证简历与 JD 的跨文档匹配

**Files:**
- Modify: `tests/integration/test_rag_natural_query.py`

**Interfaces:**
- Consumes: Task 1 的 `RagGenerator.generate()` 一次重试行为。
- Produces: 首次无效、第二次合法的跨 `resume` 与 `job_description` 集成验收。

- [ ] **Step 1: 让跨文档 Provider 首次返回无效结构**

将 `tests/integration/test_rag_natural_query.py` 中的
`CrossDocumentProvider` 改为：

```python
class CrossDocumentProvider:
    name = "cross-document"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, model, tools, **kwargs):
        assert tools == []
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                content="候选人的 Agent 经历符合岗位要求。",
                provider=self.name,
                model=model,
            )
        return ModelResponse(
            content=json.dumps(
                {
                    "status": "answered",
                    "answer": (
                        "候选人的 Agent 经历符合岗位要求。"
                    ),
                    "claims": [
                        {
                            "text": (
                                "候选人的 Agent 经历符合岗位要求。"
                            ),
                            "evidence_refs": [
                                {
                                    "evidence_id": "E1",
                                    "quote": "负责 AI Agent 平台",
                                },
                                {
                                    "evidence_id": "E2",
                                    "quote": (
                                        "需要大模型应用和 Agent "
                                        "平台开发经验"
                                    ),
                                },
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            provider=self.name,
            model=model,
        )
```

- [ ] **Step 2: 加强跨文档回答断言**

在 `test_cross_document_answer_validates_each_quote_and_keeps_legacy_fields`
中保存 Provider：

```python
provider = CrossDocumentProvider()
service.providers.get = lambda _name: provider
```

在现有断言后增加：

```python
assert provider.calls == 2
evidence_text_by_filename = {
    "resume.md": "负责 AI Agent 平台和大语言模型应用开发。",
    "agent-jd.md": "需要大模型应用和 Agent 平台开发经验。",
}
assert all(
    citation.quote
    in evidence_text_by_filename[citation.filename]
    for citation in answer.citations
)
```

- [ ] **Step 3: 运行跨文档集成测试**

Run:

```powershell
$baseTemp = Join-Path $env:TEMP "starter-agent-rag-retry-integration"
.\.venv\Scripts\python.exe -m pytest tests/integration/test_rag_natural_query.py --basetemp="$baseTemp" -q
```

Expected: PASS；自然语言问题检索 `resume` 与 `job_description`，首次结构失败后第二次
回答包含两份可定位引用。

- [ ] **Step 4: 运行全部 RAG focused tests**

Run:

```powershell
$baseTemp = Join-Path $env:TEMP "starter-agent-rag-retry-focused"
.\.venv\Scripts\python.exe -m pytest tests/unit/test_rag_generation.py tests/unit/test_rag_citations.py tests/unit/test_rag_refusal.py tests/integration/test_rag_natural_query.py tests/integration/test_rag_end_to_end.py tests/integration/test_rag_chat.py --basetemp="$baseTemp" -q
```

Expected: PASS。

- [ ] **Step 5: 运行完整离线测试**

Run:

```powershell
$baseTemp = Join-Path $env:TEMP "starter-agent-rag-retry-full"
.\.venv\Scripts\python.exe -m pytest --basetemp="$baseTemp" -q
```

Expected: 全部测试通过；只允许保留既有第三方弃用警告，不在仓库中创建新的
`.pytest-*` 临时目录。

- [ ] **Step 6: 提交跨文档验收**

```powershell
git add tests/integration/test_rag_natural_query.py
git commit -m "test: verify rag retry across resume and job"
```

- [ ] **Step 7: 检查最终变更范围**

Run:

```powershell
git status --short
git log -2 --oneline
```

Expected: 本计划只新增两个提交，生产代码只修改
`src/starter_agent/knowledge/generation.py`；工作区已有的无关修改保持不变。
