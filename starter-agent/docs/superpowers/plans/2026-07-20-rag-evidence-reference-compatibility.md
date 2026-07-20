# RAG 逐证据引用兼容 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让跨简历/JD的一个 claim 能为每个 Evidence 提供独立 quote，同时兼容旧的 `evidence_ids + quote` 单证据格式。

**Architecture:** `GeneratedClaim` 在 Pydantic `before` validator 中接受新旧两种输入，并统一为有序 `GeneratedEvidenceRef` 列表；兼容字段在 `after` validator 中回填。`assemble_citations()` 只消费归一化引用对，并继续从 Evidence 生成 canonical metadata；Generation 提示模型优先使用新格式。

**Tech Stack:** Python 3.12、Pydantic v2、FastAPI、pytest、SQLite FTS5、现有 Provider 抽象。

## Global Constraints

- 不改变 Retrieval、Chunk、metadata filter、拒答门槛、文档生命周期或 FTS5/BM25。
- quote 必须是对应 Evidence 正文的非空连续子串，不做模糊匹配或模型二次修复。
- canonical 文件名、版本、章节、行号和 Chunk ID 只能来自服务端 Evidence。
- 新旧格式不能在同一个模型输入 claim 中混用。
- 旧 `evidence_ids + quote` 输入继续支持，旧 API 字段类型保持 `list[str]` 和 `str`。
- 新格式序列化时必须同时输出完整 `evidence_refs` 和兼容字段。
- 不记录 API Key、私人正文或不必要的模型 quote。
- 不新增依赖。

---

### Task 1: 建立双格式 Claim 模型与逐证据 canonical 校验

**Files:**
- Modify: `src/starter_agent/knowledge/models.py:169-173`
- Modify: `src/starter_agent/knowledge/citations.py:7-35`
- Modify: `tests/unit/test_rag_citations.py`

**Interfaces:**
- Produces: `GeneratedEvidenceRef(evidence_id: str, quote: str)`
- Produces: `GeneratedClaim.evidence_refs: list[GeneratedEvidenceRef]`
- Preserves: `GeneratedClaim.evidence_ids: list[str]`
- Preserves: `GeneratedClaim.quote: str`
- Consumes later: `assemble_citations(claims, evidence)` 遍历 `claim.evidence_refs`

- [ ] **Step 1: 添加新格式跨文档成功测试**

在 `tests/unit/test_rag_citations.py` 添加：

```python
def test_distinct_quotes_are_validated_per_evidence() -> None:
    resume = Evidence(
        evidence_id="E1",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="resume.md",
        version=1,
        start_line=1,
        end_line=1,
        text="候选人熟悉 Python。",
    )
    job = Evidence(
        evidence_id="E2",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="job.md",
        version=1,
        start_line=1,
        end_line=1,
        text="岗位要求熟悉 Python。",
    )
    claim = GeneratedClaim.model_validate(
        {
            "text": "候选人的 Python 能力符合岗位要求。",
            "evidence_refs": [
                {"evidence_id": "E1", "quote": "候选人熟悉 Python"},
                {"evidence_id": "E2", "quote": "岗位要求熟悉 Python"},
            ],
        }
    )

    citations = assemble_citations([claim], [resume, job])

    assert [item.chunk_id for item in citations] == [
        resume.chunk_id,
        job.chunk_id,
    ]
    assert [item.quote for item in citations] == [
        "候选人熟悉 Python",
        "岗位要求熟悉 Python",
    ]
    assert claim.evidence_ids == ["E1", "E2"]
    assert claim.quote == "候选人熟悉 Python"
```

- [ ] **Step 2: 运行新测试确认 RED**

Run:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
D:\code\C\Personal-Agent\starter-agent\.venv\Scripts\python.exe -m pytest tests/unit/test_rag_citations.py::test_distinct_quotes_are_validated_per_evidence -q
```

Expected: FAIL，`GeneratedClaim` 报告 `evidence_ids` 与 `quote` 缺失，证明新格式尚未实现。

- [ ] **Step 3: 实现双格式归一化模型**

在 `models.py` 的 import 中加入：

```python
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
```

用以下模型替换现有 `GeneratedClaim`：

```python
class GeneratedEvidenceRef(BaseModel):
    evidence_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)

    @field_validator("evidence_id", "quote")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be empty")
        return stripped


class GeneratedClaim(BaseModel):
    text: str
    evidence_refs: list[GeneratedEvidenceRef] = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    quote: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_reference_input(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        has_new = "evidence_refs" in data
        has_legacy = "evidence_ids" in data or "quote" in data
        if has_new and has_legacy:
            raise ValueError("new and legacy evidence formats cannot be mixed")
        if has_new:
            return data
        if "evidence_ids" not in data or "quote" not in data:
            raise ValueError("claim evidence is required")
        evidence_ids = data.get("evidence_ids")
        if not isinstance(evidence_ids, list):
            raise ValueError("evidence_ids must be a list")
        data["evidence_refs"] = [
            {"evidence_id": evidence_id, "quote": data["quote"]}
            for evidence_id in evidence_ids
        ]
        return data

    @model_validator(mode="after")
    def populate_compatibility_fields(self) -> "GeneratedClaim":
        evidence_ids = [item.evidence_id for item in self.evidence_refs]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("duplicate evidence_id")
        self.evidence_ids = evidence_ids
        self.quote = self.evidence_refs[0].quote
        return self
```

这里的 `before` validator 只依据模型原始输入键判断新旧格式；`after` validator
统一回填兼容输出，所以新格式的 `model_dump()` 仍包含字符串 `quote`。

- [ ] **Step 4: 改为逐证据引用组装**

将 `citations.py` 的 claim 循环改为：

```python
    for claim_index, claim in enumerate(claims, start=1):
        for evidence_index, reference in enumerate(
            claim.evidence_refs, start=1
        ):
            item = available.get(reference.evidence_id)
            if (
                item is None
                or not reference.quote
                or reference.quote not in item.text
            ):
                raise KnowledgeError("citation_validation_failed")
            citations.append(
                Citation(
                    citation_id=f"C{claim_index}.{evidence_index}",
                    document_id=item.document_id,
                    filename=item.filename,
                    document_version=item.version,
                    chunk_id=item.chunk_id,
                    page=item.page,
                    section=" / ".join(item.section_path) or None,
                    start_line=item.start_line,
                    end_line=item.end_line,
                    quote=reference.quote,
                )
            )
```

不要对 quote 做大小写、空白、标点或 Unicode 模糊归一化。

- [ ] **Step 5: 运行新测试确认 GREEN**

Run:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
D:\code\C\Personal-Agent\starter-agent\.venv\Scripts\python.exe -m pytest tests/unit/test_rag_citations.py::test_distinct_quotes_are_validated_per_evidence -q
```

Expected: PASS。

- [ ] **Step 6: 添加兼容性与严格失败测试**

在 `tests/unit/test_rag_citations.py` 添加：

```python
from pydantic import ValidationError


def test_legacy_single_evidence_populates_new_reference_shape() -> None:
    claim = GeneratedClaim(
        text="候选人熟悉 Python。",
        evidence_ids=["E1"],
        quote="熟悉 Python",
    )

    assert [item.model_dump() for item in claim.evidence_refs] == [
        {"evidence_id": "E1", "quote": "熟悉 Python"}
    ]
    assert claim.model_dump()["evidence_ids"] == ["E1"]
    assert claim.model_dump()["quote"] == "熟悉 Python"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "text": "claim",
            "evidence_refs": [
                {"evidence_id": "E1", "quote": "one"},
                {"evidence_id": "E1", "quote": "two"},
            ],
        },
        {
            "text": "claim",
            "evidence_refs": [{"evidence_id": "E1", "quote": "one"}],
            "evidence_ids": ["E1"],
            "quote": "one",
        },
        {
            "text": "claim",
            "evidence_refs": [{"evidence_id": "E1", "quote": "   "}],
        },
    ],
)
def test_invalid_reference_shapes_are_rejected(payload) -> None:
    with pytest.raises(ValidationError):
        GeneratedClaim.model_validate(payload)
```

保留现有 `test_unknown_evidence_or_non_contiguous_quote_is_rejected`，它继续证明
未知 ID 或非连续 quote 返回 `citation_validation_failed`。

- [ ] **Step 7: 运行完整引用单元测试**

Run:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
D:\code\C\Personal-Agent\starter-agent\.venv\Scripts\python.exe -m pytest tests/unit/test_rag_citations.py -q
```

Expected: 全部 PASS。

- [ ] **Step 8: 提交 Task 1**

```powershell
git add src/starter_agent/knowledge/models.py src/starter_agent/knowledge/citations.py tests/unit/test_rag_citations.py
git commit -m "feat: validate quotes per RAG evidence"
```

---

### Task 2: 更新 Generation 协议并验证 API 兼容输出

**Files:**
- Modify: `src/starter_agent/knowledge/generation.py:53-67`
- Modify: `tests/unit/test_rag_generation.py`
- Modify: `tests/integration/test_rag_natural_query.py`

**Interfaces:**
- Consumes: `GeneratedClaim.evidence_refs`
- Preserves: `_GeneratedPayload(status, answer, claims)`
- Preserves: `RagAnswer.claims` 与 top-level `RagAnswer.citations`
- Produces: 新格式 claim 的 API JSON 同时含 `evidence_refs`、`evidence_ids`、`quote`

- [ ] **Step 1: 添加 Generation 新协议失败测试**

把 `tests/unit/test_rag_generation.py` 的 `_valid_payload()` 改为新格式：

```python
def _valid_payload() -> str:
    return json.dumps(
        {
            "status": "answered",
            "answer": "候选人熟练 Python。",
            "claims": [
                {
                    "text": "候选人熟练 Python。",
                    "evidence_refs": [
                        {"evidence_id": "E1", "quote": "熟练 Python"}
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )
```

在 prompt 测试中增加：

```python
    assert '"evidence_refs"' in system_prompt
    assert '"evidence_id"' in system_prompt
    assert "每个引用项只对应一个 Evidence" in system_prompt
```

另加旧格式生成兼容测试：

```python
@pytest.mark.asyncio
async def test_generation_still_accepts_legacy_single_evidence_claim() -> None:
    legacy = json.dumps(
        {
            "status": "answered",
            "answer": "候选人熟练 Python。",
            "claims": [
                {
                    "text": "候选人熟练 Python。",
                    "evidence_ids": ["E1"],
                    "quote": "熟练 Python",
                }
            ],
        },
        ensure_ascii=False,
    )

    answer = await RagGenerator(StubProvider(legacy), "test").generate(
        "会 Python 吗？", [_evidence()]
    )

    assert answer.status == "answered"
    assert answer.claims[0].evidence_refs[0].evidence_id == "E1"
```

- [ ] **Step 2: 运行 prompt 测试确认 RED**

Run:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
D:\code\C\Personal-Agent\starter-agent\.venv\Scripts\python.exe -m pytest tests/unit/test_rag_generation.py::test_generation_prompt_defines_the_strict_status_contract -q
```

Expected: FAIL，system prompt 尚未包含 `evidence_refs` 协议。

- [ ] **Step 3: 更新严格 Generation 提示**

将 `generation.py` 的 system message 改为：

```python
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
),
```

保持 `_normalize_json_envelope()`、严格状态枚举和 `tools=[]` 不变。

- [ ] **Step 4: 运行 Generation 单元测试确认 GREEN**

Run:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
D:\code\C\Personal-Agent\starter-agent\.venv\Scripts\python.exe -m pytest tests/unit/test_rag_generation.py tests/unit/test_rag_citations.py -q
```

Expected: 全部 PASS，包括裸 JSON、普通 fence、`json` fence、自由文本拒绝和旧格式兼容。

- [ ] **Step 5: 添加跨简历/JD集成测试**

在 `tests/integration/test_rag_natural_query.py` 添加 import：

```python
import json

import pytest

from starter_agent.domain.models import ModelResponse
```

添加 Provider 与测试：

```python
class CrossDocumentProvider:
    name = "cross-document"

    async def complete(self, messages, model, tools, **kwargs):
        assert tools == []
        return ModelResponse(
            content=json.dumps(
                {
                    "status": "answered",
                    "answer": "候选人的 Agent 经历符合岗位要求。",
                    "claims": [
                        {
                            "text": "候选人的 Agent 经历符合岗位要求。",
                            "evidence_refs": [
                                {
                                    "evidence_id": "E1",
                                    "quote": "负责 AI Agent 平台",
                                },
                                {
                                    "evidence_id": "E2",
                                    "quote": "需要大模型应用和 Agent 平台开发经验",
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


@pytest.mark.asyncio
async def test_cross_document_answer_validates_each_quote_and_keeps_legacy_fields() -> None:
    service = _service()
    _upload_comparison_documents(service)
    service.providers.get = lambda _name: CrossDocumentProvider()

    answer = await service.answer(
        service.default_knowledge_base_id,
        "我的简历匹配哪个岗位",
    )

    claim = answer.claims[0].model_dump(mode="json")
    assert answer.status == "answered"
    assert [item.filename for item in answer.citations] == [
        "resume.md",
        "agent-jd.md",
    ]
    assert claim["evidence_ids"] == ["E1", "E2"]
    assert claim["quote"] == "负责 AI Agent 平台"
    assert claim["evidence_refs"] == [
        {"evidence_id": "E1", "quote": "负责 AI Agent 平台"},
        {
            "evidence_id": "E2",
            "quote": "需要大模型应用和 Agent 平台开发经验",
        },
    ]
```

如果 Retrieval 的稳定顺序测试显示 E1/E2 与预期不同，只能修正 fixture 或明确排序
断言，不能让 Provider 根据正文猜 ID，也不能放宽 canonical quote 校验。

- [ ] **Step 6: 运行跨文档测试确认 GREEN**

Run:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
D:\code\C\Personal-Agent\starter-agent\.venv\Scripts\python.exe -m pytest tests/integration/test_rag_natural_query.py tests/integration/test_rag_end_to_end.py -q
```

Expected: 全部 PASS；旧 end-to-end Provider 继续证明 legacy 单证据格式兼容。

- [ ] **Step 7: 提交 Task 2**

```powershell
git add src/starter_agent/knowledge/generation.py tests/unit/test_rag_generation.py tests/integration/test_rag_natural_query.py
git commit -m "fix: generate per-evidence RAG quotes"
```

---

### Task 3: 文档同步、完整回归与安全真实模型验证

**Files:**
- Modify: `README.md`
- Modify: `docs/rag-final-validation.md`
- Test: existing full suite

**Interfaces:**
- Documents: `claims[].evidence_refs[]`
- Documents: deprecated compatibility meaning of `claims[].quote`
- Validates: real `zhipu / glm-4.7` with safe fixtures only

- [ ] **Step 1: 更新 README**

在“带引用的个人知识库”章节补充以下 API 示例与说明：

```json
{
  "text": "候选人的 Python 能力符合岗位要求",
  "evidence_refs": [
    {"evidence_id": "E1", "quote": "简历原文"},
    {"evidence_id": "E2", "quote": "JD 原文"}
  ],
  "evidence_ids": ["E1", "E2"],
  "quote": "简历原文"
}
```

明确：

- 新调用方读取 `evidence_refs` 或 top-level `citations`；
- `evidence_ids` 与 `quote` 是兼容字段；
- 多证据 claim 的兼容 `quote` 只表示第一条引用；
- top-level `citations` 才是带 canonical metadata 的权威来源；
- quote 仍必须是对应 Chunk 的连续原文。

- [ ] **Step 2: 更新最终验证清单**

在 `docs/rag-final-validation.md` 的 Generation Review 和最终通过标准中增加：

```markdown
- [ ] 跨简历/JD claim 为每个 Evidence 返回独立 quote。
- [ ] 每个 `evidence_refs[].quote` 都能在对应 canonical Chunk 中逐字定位。
- [ ] 兼容字段存在，但人工验收不使用首条兼容 `quote` 代替完整逐证据核验。
```

只有真实模型验证通过后，才在实测记录中写入通过结果。

- [ ] **Step 3: 运行聚焦回归**

Run:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$baseTemp = Join-Path $env:TEMP ("starter-agent-evidence-focused-" + [guid]::NewGuid().ToString("N"))
D:\code\C\Personal-Agent\starter-agent\.venv\Scripts\python.exe -m pytest -q --basetemp="$baseTemp" tests/unit/test_rag_citations.py tests/unit/test_rag_generation.py tests/unit/test_rag_refusal.py tests/integration/test_rag_natural_query.py tests/integration/test_rag_chat.py tests/integration/test_rag_end_to_end.py
```

Expected: 100%，退出码 0；仅允许既有 Starlette/httpx 弃用警告。

- [ ] **Step 4: 运行完整回归**

Run:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$baseTemp = Join-Path $env:TEMP ("starter-agent-evidence-full-" + [guid]::NewGuid().ToString("N"))
D:\code\C\Personal-Agent\starter-agent\.venv\Scripts\python.exe -m pytest -q --basetemp="$baseTemp"
```

Expected: 100%，退出码 0，无失败或收集错误。

- [ ] **Step 5: 运行真实 `glm-4.7` 安全 fixture 验证**

仅在验证进程中加载现有 `.env`。使用隔离内存 Store 上传：

- `tests/fixtures/knowledge/resume_demo.md`
- `tests/fixtures/knowledge/job_demo.md`

提问“根据我的简历和目标 JD，我匹配哪个岗位？请给出引用。”并断言：

```python
assert {match.document_type for match in retrieval} == {
    "resume",
    "job_description",
}
assert answer.status == "answered"
assert len(answer.citations) >= 2
assert {citation.filename for citation in answer.citations} >= {
    "resume_demo.md",
    "job_demo.md",
}
for claim in answer.claims:
    assert claim.evidence_refs
for citation in answer.citations:
    canonical = service.resolve_citation(base_id, citation.chunk_id)
    assert citation.quote in canonical.text
```

再提问安全的无证据 HR 手机号问题并断言：

```python
assert refusal.status == "refused"
assert refusal.refusal_reason == "no_evidence"
assert refusal.citations == []
```

验证输出只记录模型名、状态、引用数量、引用文件名和 canonical 校验布尔值，不打印
API Key、完整正文、完整 answer 或 quote。

- [ ] **Step 6: 写入真实验收结果**

若 Step 5 全部断言通过，在 `docs/rag-final-validation.md` 实测记录追加：

```markdown
| `glm-4.7` 逐证据跨文档引用 | 通过 | 安全 fixture 的简历/JD回答使用独立 quote；所有 quote 均通过 canonical Chunk 连续子串校验。 |
```

若任一断言失败，记录实际失败阶段和错误码，最终结论保持“不通过”，不得因回答流畅
而改判。

- [ ] **Step 7: 提交 Task 3**

```powershell
git add README.md docs/rag-final-validation.md
git commit -m "docs: record per-evidence RAG validation"
```

- [ ] **Step 8: 最终提交状态检查**

Run:

```powershell
git status --short
git log --oneline main..HEAD
git diff --check main...HEAD
```

Expected: 只允许平台生成的 `.session-only-*` 未跟踪目录；实现、测试和文档均已提交，
`git diff --check` 无错误。
