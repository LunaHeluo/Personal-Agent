# Session Selection and Precise JD Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve public job candidates per session, resolve follow-up selections before knowledge search, improve generic SerpAPI job-detail precision, and render only readable Candidate-formatted JDs.

**Architecture:** Add a small session-candidate domain module and SQLite persistence API, then intercept deterministic selection references before model routing. Keep query generation, candidate assessment, orchestration, and rendering separate: the query planner produces generic detail-evidence variants, candidate assessment ranks readable detail shapes without company lists, and the API persists/render candidates while hiding inaccessible URLs.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, SQLite, pytest, pytest-asyncio.

## Global Constraints

- Work directly on the current `main` branch.
- Do not hard-code cities, employers, or employer-specific ranking weights.
- Never send resume prose to SerpAPI; use only bounded role and location terms.
- Pending session selection precedes generic classification and knowledge matching.
- Failed URLs remain in structured diagnostics but never appear in user-facing output.
- Complete JDs appear before partial evidence; partial evidence appears only when the complete target is unmet.
- A new public search expires the previous pending candidate snapshot for that session.
- Pending candidates expire after 60 minutes.
- Use TDD: verify every new test fails for the intended missing behavior before production edits.

---

### Task 1: Session Candidate Domain and Persistence

**Files:**
- Create: `src/starter_agent/job_research/selection.py`
- Modify: `src/starter_agent/infrastructure/session_store.py`
- Create: `tests/unit/test_job_selection.py`
- Modify: `tests/unit/test_session_management.py`

**Interfaces:**
- Produces: `JobSelectionReference(ordinal: int | None, candidate_id: UUID | None)`.
- Produces: `parse_job_selection_reference(message: str) -> JobSelectionReference | None`.
- Produces: `PendingJobCandidate` with candidate identity, display order, payload, evidence level, status, and timestamps.
- Produces `replace_pending_job_candidates(*, session_id: UUID, turn_id: UUID, candidates: Sequence[Mapping[str, Any]], expires_at: datetime) -> tuple[PendingJobCandidate, ...]`.
- Produces `resolve_pending_job_candidate(session_id: UUID, *, ordinal: int | None = None, candidate_id: UUID | None = None, now: datetime | None = None) -> PendingJobCandidate | None`.
- Produces `list_pending_job_candidates(session_id: UUID, *, now: datetime | None = None) -> tuple[PendingJobCandidate, ...]`.

- [ ] **Step 1: Write parser tests that fail because the module does not exist**

```python
@pytest.mark.parametrize(
    ("message", "ordinal"),
    [("选择第一个岗位做匹配分析", 1), ("选 2", 2), ("第 3 个", 3)],
)
def test_parse_job_selection_ordinal(message: str, ordinal: int) -> None:
    reference = parse_job_selection_reference(message)
    assert reference is not None
    assert reference.ordinal == ordinal

def test_parse_exact_candidate_id() -> None:
    candidate_id = uuid4()
    reference = parse_job_selection_reference(f"选择 Candidate ID：{candidate_id}")
    assert reference is not None
    assert reference.candidate_id == candidate_id
```

- [ ] **Step 2: Run the parser tests and verify RED**

Run: `uv run pytest tests/unit/test_job_selection.py -q`

Expected: collection/import failure for missing `starter_agent.job_research.selection`.

- [ ] **Step 3: Implement the minimal deterministic parser and immutable models**

Use bounded regexes for Arabic ordinals, Chinese ordinals one through ten, and canonical UUID strings. Return `None` for ordinary requests such as `搜索北京岗位`.

- [ ] **Step 4: Run parser tests and verify GREEN**

Run: `uv run pytest tests/unit/test_job_selection.py -q`

Expected: all parser tests pass.

- [ ] **Step 5: Write persistence tests that fail**

```python
def test_replacing_pending_candidates_expires_previous_snapshot(tmp_path) -> None:
    store = SQLiteSessionStore("sqlite:///sessions.db", tmp_path)
    session_id = store.create_session()
    first = store.replace_pending_job_candidates(
        session_id=session_id,
        turn_id=uuid4(),
        candidates=[complete_payload("First")],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    second = store.replace_pending_job_candidates(
        session_id=session_id,
        turn_id=uuid4(),
        candidates=[complete_payload("Second")],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert store.resolve_pending_job_candidate(session_id, ordinal=1).title == "Second"
    assert first[0].candidate_id != second[0].candidate_id

def test_pending_candidate_is_scoped_to_session_and_expiry(tmp_path) -> None:
    store = SQLiteSessionStore("sqlite:///sessions.db", tmp_path)
    owner = store.create_session()
    other = store.create_session()
    expired = store.replace_pending_job_candidates(
        session_id=owner,
        turn_id=uuid4(),
        candidates=[complete_payload("Expired")],
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert store.resolve_pending_job_candidate(owner, candidate_id=expired[0].candidate_id) is None
    assert store.resolve_pending_job_candidate(other, candidate_id=expired[0].candidate_id) is None
```

- [ ] **Step 6: Run persistence tests and verify RED**

Run: `uv run pytest tests/unit/test_session_management.py -q`

Expected: failure because the candidate persistence methods do not exist.

- [ ] **Step 7: Add `JobResearchCandidateRow` and store methods**

Store the normalized candidate payload as JSON, index `session_id`, `status`, and `expires_at`, and update prior `PENDING_CONFIRMATION` rows to `EXPIRED` inside the same transaction before inserting a replacement snapshot. Resolve by ordinal or Candidate ID only when session, status, and expiry are valid.

- [ ] **Step 8: Run Task 1 tests and verify GREEN**

Run: `uv run pytest tests/unit/test_job_selection.py tests/unit/test_session_management.py -q`

- [ ] **Step 9: Commit Task 1**

```powershell
git add src/starter_agent/job_research/selection.py src/starter_agent/infrastructure/session_store.py tests/unit/test_job_selection.py tests/unit/test_session_management.py
git commit -m "feat: persist pending job selections"
```

### Task 2: Selection Precedence and Same-Session Analysis

**Files:**
- Modify: `src/starter_agent/interfaces/api.py`
- Modify: `tests/integration/test_rag_chat.py`
- Modify: `tests/integration/test_api.py`

**Interfaces:**
- Consumes: `parse_job_selection_reference(message: str) -> JobSelectionReference | None` and `resolve_pending_job_candidate(...)` from Task 1.
- Produces: `_try_pending_job_selection(request, application) -> ChatResult | None`.
- Produces: `_dispatch_chat_request(request: ChatRequest, *, application, on_tool_event=None) -> ChatResult` shared by buffered and streaming endpoints so selection is checked before `_classify_chat_request`.

- [ ] **Step 1: Write the two-turn regression test**

Construct a session with two persisted complete candidates, send `选择第一个岗位做匹配分析`, and assert the response contains the first candidate title/source URL and does not invoke `knowledge.answer`, public search preparation, or the generic route classifier.

- [ ] **Step 2: Add missing/expired/cross-session tests**

Assert that an unresolved selection produces a concise “候选已失效或不属于当前会话，请重新搜索” response and never substitutes a saved knowledge-base JD.

- [ ] **Step 3: Run focused API tests and verify RED**

Run: `uv run pytest tests/integration/test_rag_chat.py tests/integration/test_api.py -q -k "job_selection or pending_candidate"`

Expected: the old path classifies the message or returns knowledge content instead of the stored Candidate.

- [ ] **Step 4: Implement pre-classification selection dispatch**

Both `/v1/chat` and `/v1/chat/stream` call one shared dispatcher. It ensures the request session, parses a selection reference, resolves it from the store, and returns the stored JD/match analysis directly. Only when no selection reference is present does normal classification continue.

- [ ] **Step 5: Persist visible candidates after a public search**

After analysis, store complete candidates in displayed order and append substantive partial candidates only when the complete target is unmet. Candidate IDs returned by persistence are added to the render payload. Do not persist failed attempts as selectable candidates.

- [ ] **Step 6: Run focused API tests and verify GREEN**

Run: `uv run pytest tests/integration/test_rag_chat.py tests/integration/test_api.py -q -k "job_selection or pending_candidate"`

- [ ] **Step 7: Commit Task 2**

```powershell
git add src/starter_agent/interfaces/api.py tests/integration/test_rag_chat.py tests/integration/test_api.py
git commit -m "fix: resolve job selections before knowledge search"
```

### Task 3: Generic SerpAPI Query Precision and Candidate Readability

**Files:**
- Modify: `src/starter_agent/job_research/query_planner.py`
- Modify: `src/starter_agent/job_research/candidates.py`
- Modify: `src/starter_agent/tools/builtin/job_search.py`
- Modify: `tests/unit/test_job_query_planner.py`
- Modify: `tests/unit/test_job_candidates.py`
- Modify: `tests/unit/test_search_jobs_serpapi.py`

**Interfaces:**
- Produces query-family diagnostics alongside the existing planned/executed queries.
- Produces general reason codes such as `concrete_job_detail`, `section_rich_snippet`, `structured_job_link`, `collection_page_shape`, and `thin_snippet_signal` without employer-specific codes.

- [ ] **Step 1: Write failing query portfolio tests**

For an arbitrary location with a resolved local and Latin alias, assert the bounded plan contains:

```python
assert any("岗位职责" in query or "任职要求" in query for query in plan.queries)
assert any("job description" in query.casefold() or "responsibilities" in query.casefold() for query in plan.queries)
assert all("tencent" not in query.casefold() and "bytedance" not in query.casefold() for query in plan.queries)
assert len(plan.queries) <= 12
```

Also assert arbitrary non-China locations retain both aliases and locale parameters.

- [ ] **Step 2: Run query tests and verify RED**

Run: `uv run pytest tests/unit/test_job_query_planner.py -q`

Expected: current fixed role pairs contain no detail-evidence variants.

- [ ] **Step 3: Implement the bounded generic query portfolio**

Generate a balanced set containing role recall variants and section-rich detail variants. Use the bounded search-profile query only to recover role terms; strip line breaks, URLs, personal-contact patterns, and excessive text. Fall back to the existing AI role vocabulary when no safe role phrase is available.

- [ ] **Step 4: Run query tests and verify GREEN**

Run: `uv run pytest tests/unit/test_job_query_planner.py -q`

- [ ] **Step 5: Write failing generic ranking tests**

Assert that a concrete arbitrary employer detail page with section-rich evidence outranks an aggregator mirror for the same job, that wrong-location results remain penalized, and that no reason code names a specific employer.

- [ ] **Step 6: Run ranking tests and verify RED**

Run: `uv run pytest tests/unit/test_job_candidates.py -q -k "detail or readable or aggregator"`

- [ ] **Step 7: Implement generic detail/readability scoring**

Extend path and snippet signals for singular `/job/`, job IDs, JSON-LD metadata propagated by structured results, explicit responsibility/requirement pairs, login/expired signals, and known collection shapes. Keep source ownership claims conservative.

- [ ] **Step 8: Write and run SerpAPI diagnostic tests RED then GREEN**

Verify merged results retain matched query families and ranking diagnostics expose general reason codes. Run:

`uv run pytest tests/unit/test_search_jobs_serpapi.py -q -k "variant or diagnostic or ranking"`

- [ ] **Step 9: Run all Task 3 tests**

Run: `uv run pytest tests/unit/test_job_query_planner.py tests/unit/test_job_candidates.py tests/unit/test_search_jobs_serpapi.py -q`

- [ ] **Step 10: Commit Task 3**

```powershell
git add src/starter_agent/job_research/query_planner.py src/starter_agent/job_research/candidates.py src/starter_agent/tools/builtin/job_search.py tests/unit/test_job_query_planner.py tests/unit/test_job_candidates.py tests/unit/test_search_jobs_serpapi.py
git commit -m "feat: improve precise job detail discovery"
```

### Task 4: Candidate Output and Failure Visibility

**Files:**
- Modify: `src/starter_agent/interfaces/api.py`
- Modify: `tests/unit/test_job_research_api_answer.py`

**Interfaces:**
- Consumes persisted Candidate IDs and normalized JD payloads.
- Produces the confirmed Candidate/overview/responsibility/requirement/match template.

- [ ] **Step 1: Replace old output expectations with failing Candidate-template tests**

Assert headings and fields exactly include `# Candidate 1：`, `## 岗位概览`, company, role, location, source URL, read status, Candidate ID, `PENDING_CONFIRMATION`, `## 职责摘录`, `## 任职要求`, and `## 简历匹配概览`.

- [ ] **Step 2: Add visibility tests**

Assert all normalized responsibility/requirement items appear within the safety limit, failed URLs never appear, partial evidence appears only when `len(jobs) < target_count`, and the final prompt references Candidate number/ID.

- [ ] **Step 3: Run answer tests and verify RED**

Run: `uv run pytest tests/unit/test_job_research_api_answer.py -q`

- [ ] **Step 4: Implement Candidate rendering**

Render each complete job as its own Candidate block. Label structured/direct employer pages conservatively (`招聘详情页` unless source ownership is verified). Enforce a per-candidate display cap while keeping stored payload intact. Remove the user-facing inaccessible section entirely; retain attempts in `jd_result.data` and logs.

- [ ] **Step 5: Run answer tests and verify GREEN**

Run: `uv run pytest tests/unit/test_job_research_api_answer.py -q`

- [ ] **Step 6: Commit Task 4**

```powershell
git add src/starter_agent/interfaces/api.py tests/unit/test_job_research_api_answer.py
git commit -m "feat: render selectable complete job candidates"
```

### Task 5: Regression and End-to-End Verification

**Files:**
- Modify only if a test exposes a defect in the scoped implementation.

**Interfaces:**
- Verifies all interfaces produced by Tasks 1–4.

- [ ] **Step 1: Run the focused regression suite**

```powershell
uv run pytest tests/unit/test_job_selection.py tests/unit/test_session_management.py tests/unit/test_job_query_planner.py tests/unit/test_job_candidates.py tests/unit/test_search_jobs_serpapi.py tests/unit/test_job_research_api_answer.py tests/integration/test_rag_chat.py tests/integration/test_api.py -q
```

- [ ] **Step 2: Run all job-research tests**

Run: `uv run pytest tests/unit tests/integration -q -k "job_research or job_selection or search_jobs_serpapi or rag_chat"`

- [ ] **Step 3: Run the complete test suite**

Run: `uv run pytest -q`

- [ ] **Step 4: Run a deterministic two-turn smoke**

Use fixture-backed search data: first turn returns at least two complete JDs, second turn selects Candidate 1. Verify the selected title and URL match exactly and no knowledge-base answer path runs.

- [ ] **Step 5: Inspect diagnostics**

Report generated query variants, SerpAPI locale parameters, top ranking reason codes, complete/partial counts, hidden failure count, and Candidate selection identity.

- [ ] **Step 6: Commit any verification-only test adjustments**

```powershell
git add tests
git commit -m "test: cover precise selectable job results"
```
