# RAG Query and Generation Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让中文求职自然问句稳定检索简历与 JD，支持内置映射词和 YAML 覆盖，并严格兼容 `glm-4.7` 的单层 fenced JSON。

**Architecture:** 新增不可变 `QueryMappingCatalog`，由 `KnowledgeConfig` 合并内置词表与 YAML 覆盖后注入 QueryNormalizer；Retriever 合并 FTS 长词与 OR 短词结果，并在比较意图下保持简历/JD 证据覆盖。Generation 仅规范化 JSON 外层 envelope，现有 Schema 与引用校验保持不变。

**Tech Stack:** Python 3.12、Pydantic Settings、SQLAlchemy/SQLite FTS5、FastAPI、pytest、智谱 `glm-4.7`

## Global Constraints

- 第一阶段继续使用 SQLite FTS5 `trigram` 与 BM25，不增加 Embedding、向量索引、模型查询改写或模型 Rerank。
- YAML 映射只允许公共领域词，不允许私人正文、凭据或自动学习。
- Scope、metadata filter、无证据拒答、`tools=[]`、canonical 引用、更新和删除语义不得放宽。
- 所有行为变更必须先写失败测试并观察预期失败。
- 运行测试时使用唯一 `--basetemp`，避免 Windows pytest 临时目录权限冲突。

---

### Task 1: 映射词配置与不可变 Catalog

**Files:**
- Create: `src/starter_agent/knowledge/mappings.py`
- Modify: `src/starter_agent/settings.py`
- Modify: `config/config.example.yaml`
- Test: `tests/unit/test_knowledge_mappings.py`
- Test: `tests/unit/test_knowledge_settings.py`

**Interfaces:**
- Produces: `QueryMappingConfig`, `QueryMappingCatalog`, `build_query_mapping_catalog(config) -> QueryMappingCatalog`
- `QueryMappingCatalog.expand(term: str) -> tuple[str, ...]`
- `QueryMappingCatalog.version: str`

- [ ] **Step 1: Write failing configuration and Catalog tests**

```python
def test_builtin_catalog_expands_chinese_and_english_both_ways():
    catalog = build_query_mapping_catalog(QueryMappingConfig())
    assert "岗位要求" in catalog.expand("JD")
    assert "JD" in catalog.expand("岗位要求")
    assert catalog.version == "builtin-v1"


def test_yaml_groups_replace_add_and_disable():
    config = QueryMappingConfig.model_validate({
        "version": "local-v1",
        "groups": {
            "rag": ["RAG", "知识检索"],
            "agent_platform": ["Agent 平台", "智能体平台"],
        },
        "disabled_groups": ["llm"],
    })
    catalog = build_query_mapping_catalog(config)
    assert catalog.expand("RAG") == ("RAG", "知识检索")
    assert "智能体平台" in catalog.expand("Agent 平台")
    assert catalog.expand("LLM") == ("LLM",)
    assert catalog.version == "local-v1"
```

Add parametrized invalid cases for empty terms, cross-group duplicates, reserved FTS operators, more than 100 groups, more than 32 terms per group, and overrides without an explicit local version.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
uv run pytest tests/unit/test_knowledge_mappings.py tests/unit/test_knowledge_settings.py -q
```

Expected: collection/import failures because the config and Catalog do not exist.

- [ ] **Step 3: Implement Pydantic config and Catalog**

```python
class QueryMappingConfig(BaseModel):
    version: str = "builtin-v1"
    groups: dict[str, list[str]] = Field(default_factory=dict)
    disabled_groups: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class QueryMappingCatalog:
    version: str
    groups: Mapping[str, tuple[str, ...]]
    reverse: Mapping[str, tuple[str, ...]]

    def expand(self, term: str) -> tuple[str, ...]:
        return self.reverse.get(term.casefold(), (term,))
```

Implement validation and built-in groups exactly as specified in the design. Add
`query_mappings: QueryMappingConfig = Field(default_factory=QueryMappingConfig)`
to `KnowledgeConfig`, and add a commented safe YAML example.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 1 command. Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/starter_agent/knowledge/mappings.py src/starter_agent/settings.py config/config.example.yaml tests/unit/test_knowledge_mappings.py tests/unit/test_knowledge_settings.py
git commit -m "feat: add configurable RAG query mappings"
```

### Task 2: 中文自然问句规范化

**Files:**
- Modify: `src/starter_agent/knowledge/query.py`
- Test: `tests/unit/test_knowledge_query.py`

**Interfaces:**
- Consumes: `QueryMappingCatalog`
- Produces: `normalize_query(question: str, catalog: QueryMappingCatalog | None = None) -> NormalizedQuery`
- `NormalizedQuery` adds `comparison_intent: bool` and `mapping_version: str`

- [ ] **Step 1: Write failing natural Chinese query tests**

```python
def test_chinese_resume_job_question_extracts_domain_terms():
    query = normalize_query("我的简历匹配哪个岗位")
    assert query.terms == ["简历", "匹配", "岗位"]
    assert query.match_expression is None
    assert query.short_terms == ["简历", "匹配", "岗位"]
    assert query.comparison_intent is True
    assert query.mapping_version == "builtin-v1"


def test_yaml_catalog_expands_mixed_language_terms():
    catalog = build_query_mapping_catalog(QueryMappingConfig.model_validate({
        "version": "local-v1",
        "groups": {"agent_platform": ["Agent 平台", "智能体平台"]},
    }))
    query = normalize_query("Agent 平台需要哪些能力", catalog)
    assert "智能体平台" in query.terms
    assert query.mapping_version == "local-v1"
```

Retain the empty query, FTS escaping and reserved operator tests.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
uv run pytest tests/unit/test_knowledge_query.py -q
```

Expected: the whole Chinese sentence remains one term and new fields are absent.

- [ ] **Step 3: Implement deterministic extraction**

```python
@dataclass(frozen=True)
class NormalizedQuery:
    terms: list[str]
    match_expression: str | None
    short_terms: list[str]
    comparison_intent: bool = False
    mapping_version: str = "builtin-v1"


def normalize_query(
    question: str,
    catalog: QueryMappingCatalog | None = None,
) -> NormalizedQuery:
    catalog = catalog or build_query_mapping_catalog(QueryMappingConfig())
    # Extract ASCII tokens, then longest-first configured CJK phrases.
    # Expand each token through catalog, stable-deduplicate, and detect
    # resume + match/suitable + job/JD comparison intent.
```

Use a fixed intent-term set for “匹配/适合/胜任” and Catalog groups for resume/job
concepts. Do not use model calls or a generic all-ngram expansion.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 2 command. Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/starter_agent/knowledge/query.py tests/unit/test_knowledge_query.py
git commit -m "fix: normalize Chinese RAG questions"
```

### Task 3: 合并长短词检索并覆盖比较证据

**Files:**
- Modify: `src/starter_agent/knowledge/store.py`
- Modify: `src/starter_agent/knowledge/retrieval.py`
- Modify: `src/starter_agent/knowledge/models.py`
- Modify: `src/starter_agent/knowledge/service.py`
- Test: `tests/unit/test_knowledge_store.py`
- Test: `tests/integration/test_knowledge_retrieval_api.py`
- Create: `tests/integration/test_rag_natural_query.py`

**Interfaces:**
- Consumes: `QueryMappingCatalog`, `NormalizedQuery`
- `KnowledgeRetriever(store, mapping_catalog)`
- `search_short_terms(...) -> list[tuple[KnowledgeChunk, str, int]]`
- `RetrievalMatch.mapping_version: str`

- [ ] **Step 1: Write failing Store OR-ranking test**

Create two documents where one Chunk contains “简历/经历” and one contains
“岗位/要求”. Assert `search_short_terms(..., ["简历", "匹配", "岗位"])` returns
both, orders higher hit counts first, and respects `document_types=["resume"]`.

- [ ] **Step 2: Write failing end-to-end natural query test**

```python
matches = service.retrieve(
    service.default_knowledge_base_id,
    "我的简历匹配哪个岗位",
    top_k=6,
)
assert {item.document_type for item in matches} == {
    "resume",
    "job_description",
}
assert all(item.mapping_version == "builtin-v1" for item in matches)
assert [item.rank for item in matches] == list(range(1, len(matches) + 1))
```

Also assert `document_types=["resume"]` returns no JD and `top_k=1` returns exactly
one item.

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
uv run pytest tests/unit/test_knowledge_store.py tests/integration/test_rag_natural_query.py -q
```

Expected: current short-term AND query returns no comparison evidence.

- [ ] **Step 4: Implement OR short-term scoring**

```python
predicates = [
    func.instr(KnowledgeChunkRow.search_text, term.casefold()) > 0
    for term in terms
]
hit_count = sum(
    case((predicate, 1), else_=0) for predicate in predicates
).label("hit_count")
query = query.where(or_(*predicates)).order_by(
    hit_count.desc(),
    KnowledgeChunkRow.document_id,
    KnowledgeChunkRow.ordinal,
)
```

Return `hit_count` with each row. Keep all existing Scope and metadata filters in
the SQL query.

- [ ] **Step 5: Implement Retriever merge and coverage**

Build Catalog from `settings.knowledge.query_mappings` in
`KnowledgeApplicationService`, inject it into `KnowledgeRetriever`, execute both
long and short paths when present, deduplicate by `chunk_id`, preserve filters,
ensure resume/JD coverage only when `comparison_intent` and `top_k >= 2`, then
assign rank and mapping version.

- [ ] **Step 6: Run tests and verify GREEN**

Run:

```powershell
uv run pytest tests/unit/test_knowledge_query.py tests/unit/test_knowledge_store.py tests/integration/test_knowledge_retrieval_api.py tests/integration/test_rag_natural_query.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```powershell
git add src/starter_agent/knowledge/store.py src/starter_agent/knowledge/retrieval.py src/starter_agent/knowledge/models.py src/starter_agent/knowledge/service.py tests/unit/test_knowledge_store.py tests/integration/test_knowledge_retrieval_api.py tests/integration/test_rag_natural_query.py
git commit -m "fix: retrieve evidence for natural job matching questions"
```

### Task 4: 严格兼容 GLM fenced JSON

**Files:**
- Modify: `src/starter_agent/knowledge/generation.py`
- Test: `tests/unit/test_rag_generation.py`
- Test: `tests/unit/test_rag_citations.py`

**Interfaces:**
- Produces private `_normalize_json_envelope(content: str) -> str`
- Existing `RagGenerator.generate()` contract remains unchanged

- [ ] **Step 1: Write failing envelope tests**

Parametrize accepted content as naked JSON, ` ```json\n{...}\n``` ` and
` ```\n{...}\n``` `. Parametrize rejected content as prose before/after a fence,
multiple fences, non-JSON content and empty fences.

```python
@pytest.mark.asyncio
async def test_generation_accepts_single_json_code_fence():
    provider = StubProvider(content=f"```json\n{valid_payload}\n```")
    answer = await RagGenerator(provider, "glm-4.7").generate(question, evidence)
    assert answer.status == "answered"
    assert answer.citations[0].quote == "熟练 Python"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
uv run pytest tests/unit/test_rag_generation.py tests/unit/test_rag_citations.py -q
```

Expected: fenced JSON fails with `generation_invalid_output`.

- [ ] **Step 3: Implement strict envelope normalization**

```python
_FENCED_JSON = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*)\r?\n```[ \t]*\Z",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_json_envelope(content: str) -> str:
    stripped = content.strip()
    match = _FENCED_JSON.fullmatch(stripped)
    return match.group("body").strip() if match else stripped
```

Call it immediately before `_GeneratedPayload.model_validate_json()`. Do not
extract JSON from surrounding prose. Keep `assemble_citations()` unchanged.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 4 command. Expected: accepted envelopes pass; malformed/free-text
cases still raise `generation_invalid_output`.

- [ ] **Step 5: Commit**

```powershell
git add src/starter_agent/knowledge/generation.py tests/unit/test_rag_generation.py tests/unit/test_rag_citations.py
git commit -m "fix: accept strict fenced JSON from RAG providers"
```

### Task 5: 文档、完整回归与真实 GLM 验证

**Files:**
- Modify: `docs/rag-final-validation.md`
- Modify: `README.md`
- Test: existing full suite

**Interfaces:**
- Documents the YAML mapping schema, restart requirement, version field and
  real-model acceptance record.

- [ ] **Step 1: Update documentation**

Add the safe YAML example, built-in group behavior, replacement/append/disable
semantics, no-private-content warning, and `mapping_version` retrieval field.
Change the final validation record only after rerunning real `glm-4.7`.

- [ ] **Step 2: Run focused regression**

```powershell
uv run pytest tests/unit/test_knowledge_mappings.py tests/unit/test_knowledge_query.py tests/unit/test_knowledge_store.py tests/unit/test_rag_generation.py tests/unit/test_rag_citations.py tests/unit/test_rag_refusal.py tests/integration/test_knowledge_retrieval_api.py tests/integration/test_rag_natural_query.py tests/integration/test_rag_chat.py -q
```

Expected: all pass.

- [ ] **Step 3: Run full regression**

```powershell
$baseTemp = Join-Path $env:TEMP ("starter-agent-rag-hardening-" + [guid]::NewGuid())
uv run pytest -q --basetemp="$baseTemp"
```

Expected: 100%, exit code 0, no failures or collection errors.

- [ ] **Step 4: Run real `glm-4.7` safe-fixture validation**

Load the existing local `.env` only inside the validation process. Upload
`resume_demo.md` and `job_demo.md` into an isolated in-memory Store, ask
“我的简历匹配哪个岗位” and one no-evidence HR-phone question.

Assert:

```python
assert {m.document_type for m in retrieval} == {"resume", "job_description"}
assert answer.status == "answered"
assert answer.citations
assert all(c.quote in evidence_by_chunk[c.chunk_id] for c in answer.citations)
assert refusal.status == "refused"
assert refusal.refusal_reason == "no_evidence"
```

- [ ] **Step 5: Commit**

```powershell
git add README.md docs/rag-final-validation.md
git commit -m "docs: record hardened RAG validation"
```
