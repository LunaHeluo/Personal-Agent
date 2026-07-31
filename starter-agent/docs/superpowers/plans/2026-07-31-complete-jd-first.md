# Complete-JD-First Job Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make public job research return up to three complete, source-backed JDs before falling back to snippets, within ten candidates and a soft 180-second retrieval deadline.

**Architecture:** Filter collection pages before retrieval, then run each remaining candidate through Playwright, safe HTTP, JSON-LD/HTML extraction, and finally search evidence. Normalize job-bearing snapshot evidence instead of comparing whole dynamic pages, and render one compact final status per source URL while keeping complete JDs prominent.

**Tech Stack:** Python 3.11+, Pydantic, BeautifulSoup, asyncio, Playwright MCP through the governed Tool boundary, SafeWebFetcher/httpx, pytest/pytest-asyncio.

## Global Constraints

- Work directly on `main`, as explicitly approved; do not create a worktree.
- Preserve all unrelated tracked and untracked user files in the dirty worktree.
- Stage only the exact files named by each task. Some named files already contain related, uncommitted JD-reliability changes; review their complete staged diff before committing.
- Target three complete JDs.
- Start at most ten candidate URLs.
- Use a soft 180-second orchestration deadline: do not start a new candidate after expiry, but allow the current bounded operation to finish.
- Partial evidence never consumes the complete-JD target.
- Each URL gets at most two Playwright attempts with the existing 15- and 30-second waits.
- Keep SafeWebFetcher DNS, redirect, peer, response-size, robots, and URL-sanitization controls intact.
- Never promote a search snippet to a complete JD.
- Preserve Gate, confirmation, network guard, audit, privacy, redaction, and public-web scope behavior.
- Use TDD for every production behavior: write one focused test, observe the expected failure, implement the minimum fix, and rerun.

---

### Task 1: Reject LinkedIn and no-whitespace job collection pages before retrieval

**Files:**
- Modify: `src/starter_agent/job_research/candidates.py:20-245`
- Modify: `src/starter_agent/tools/builtin/job_search.py:420-570`
- Modify: `src/starter_agent/skills/job_research.py:240-265`
- Modify: `src/starter_agent/interfaces/api.py:705-735`
- Test: `tests/unit/test_job_candidates.py`
- Test: `tests/unit/test_job_search_location_recall.py`
- Test: `tests/unit/test_search_jobs_serpapi.py`
- Test: `tests/unit/test_job_research_skill.py`

**Interfaces:**
- Consumes: `rank_job_candidates(results, *, limit, location_aliases=())`.
- Produces: the same tuple of `JobCandidate`, with collection pages absent and target-location scoring preserved when the API prepares retrieval candidates.
- Produces: `filtered_collection_count` in SerpAPI search statistics so diagnostics explain why raw and retrieval candidate counts differ.

- [ ] **Step 1: Add failing collection-title and path tests**

```python
def test_linkedin_collection_without_space_before_jobs_is_rejected() -> None:
    ranked = rank_job_candidates(
        [{
            "title": "1000+ 北京智能果技术有限公司jobs in Worldwide",
            "url": (
                "https://www.linkedin.com/jobs/"
                "%E5%8C%97%E4%BA%AC%E6%99%BA%E8%83%BD%E6%9E%9C"
                "%E6%8A%80%E6%9C%AF%E6%9C%89%E9%99%90%E5%85%AC"
                "%E5%8F%B8-jobs-worldwide"
            ),
            "url_kind": "organic",
            "provider_position": 0,
        }],
        limit=10,
        location_aliases=("北京", "Beijing"),
    )

    assert ranked == ()


def test_jobs_worldwide_path_is_a_collection_even_with_job_like_title() -> None:
    assessment = assess_job_candidate(
        {
            "url_kind": "organic",
            "snippet": "Beijing AI Agent Engineer",
        },
        url="https://www.linkedin.com/jobs/example-company-jobs-worldwide",
        title="AI Agent Engineer",
        location_aliases=("北京", "Beijing"),
    )

    assert assessment.page_kind == "collection_page"
    assert assessment.reason_codes == ("collection_page_shape",)
```

Add a third case with two collection rows followed by eleven valid detail
rows, call `rank_job_candidates(..., limit=10)`, and assert all ten returned
items are detail rows. This proves collections are removed before, rather than
counted inside, the candidate limit.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_job_candidates.py -q --tb=short -p no:cacheprovider
```

Expected: the no-whitespace LinkedIn result remains ranked or the
`-jobs-worldwide` URL remains a candidate.

- [ ] **Step 3: Implement bounded collection detection**

Update `_COLLECTION_TITLE` so the leading-count pattern does not depend on a
Unicode word boundary before `jobs`, and add a path pattern for collection
suffixes:

```python
_COLLECTION_TITLE = re.compile(
    r"(?:^\s*[\d,]+\+?\s+.*?jobs?(?:\s+in\b|$)|"
    r"\bjobs?\s+in\b|招聘(?:信息|[^\s，。|]{0,12}人才)|职位列表)",
    re.IGNORECASE,
)
_COLLECTION_SUFFIX = re.compile(
    r"(?:^|/)jobs/[^/?#]*-jobs-(?:worldwide|in-[^/?#]+)/*$",
    re.IGNORECASE,
)
```

Include `_COLLECTION_SUFFIX.search(path)` in the existing collection branch.
Do not add a permanent LinkedIn domain ban.

- [ ] **Step 4: Add a failing API candidate-order preservation test**

Construct a `ToolResult.data` payload whose `results` are already scored for
`("北京", "Beijing")`, then assert `_public_job_candidates()` excludes the
collection and keeps the same first two detail URLs. Include
`location_aliases` in the payload.

- [ ] **Step 5: Run the API ordering test and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_job_search_location_recall.py -q --tb=short -p no:cacheprovider
```

Expected: re-ranking without aliases changes the expected order.

- [ ] **Step 6: Preserve location aliases in the API boundary**

In `_public_job_candidates`, read bounded string aliases from
`result.data["location_aliases"]` and pass them to `rank_job_candidates`.
Keep the existing Pydantic-backed `JobCandidate` output and candidate limit.

- [ ] **Step 7: Add and implement filtered-collection statistics**

First add a failing `tests/unit/test_search_jobs_serpapi.py` case with two
collection rows and one direct job-detail row. Assert the direct row remains
and:

```python
assert result.data["filtered_collection_count"] == 2
```

Then compute the count from the merged, normalized rows before ranking:

```python
filtered_collection_count = sum(
    1
    for row in merged.values()
    if assess_job_candidate(
        row,
        url=str(row.get("url") or ""),
        title=str(row.get("title") or ""),
        location_aliases=location_aliases,
    ).page_kind == "collection_page"
)
```

Return the value as top-level
`ToolResult.data["filtered_collection_count"]`. Import
`assess_job_candidate` beside the existing `rank_job_candidates` import.
In `JobResearchOrchestrator.search_prepared`, add
`"filtered_collection_count"` to the existing explicit allowlist that copies
tool statistics into `search_statistics`. Add a focused skill test asserting
the field survives that boundary.

- [ ] **Step 8: Run focused ranking tests and verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_job_candidates.py tests/unit/test_job_search_location_recall.py tests/unit/test_search_jobs_serpapi.py tests/unit/test_job_research_skill.py -q --tb=short -p no:cacheprovider
```

Expected: PASS; LinkedIn collections are absent and location ordering remains
stable.

- [ ] **Step 9: Review and commit only Task 1 files**

```powershell
git diff --check -- src/starter_agent/job_research/candidates.py src/starter_agent/tools/builtin/job_search.py src/starter_agent/skills/job_research.py src/starter_agent/interfaces/api.py tests/unit/test_job_candidates.py tests/unit/test_job_search_location_recall.py tests/unit/test_search_jobs_serpapi.py tests/unit/test_job_research_skill.py
git add -- src/starter_agent/job_research/candidates.py src/starter_agent/tools/builtin/job_search.py src/starter_agent/skills/job_research.py src/starter_agent/interfaces/api.py tests/unit/test_job_candidates.py tests/unit/test_job_search_location_recall.py tests/unit/test_search_jobs_serpapi.py tests/unit/test_job_research_skill.py
git diff --cached --check
git commit -m "fix: filter job collection candidates"
```

---

### Task 2: Extract Randstad inline English and Schneider Chinese responsibility sections

**Files:**
- Modify: `src/starter_agent/tools/adapters/job_description_extractor.py:40-620`
- Test: `tests/unit/test_job_description_extractor.py`
- Test: `tests/unit/test_job_page_fallback.py`

**Interfaces:**
- Consumes: `JobDescriptionExtractor.extract(content, content_type)`.
- Produces: `ExtractedJobDescription` with a complete result when a
  source-backed title, responsibilities, and requirements exist in inline
  JSON-LD or HTML.

- [ ] **Step 1: Add a failing Randstad-style JSON-LD test**

```python
def test_json_ld_splits_inline_english_job_and_required_skills() -> None:
    posting = {
        "@type": "JobPosting",
        "title": "AI Agent & LLM Engineer",
        "description": (
            "about the company. A financial technology lab. "
            "about the job. 1. Build and optimize AI Agent frameworks. "
            "2. Deploy vertical LLM services. "
            "skills and experience required. "
            "1. Three years of LLM engineering experience. "
            "2. Strong Python and PyTorch skills."
        ),
    }

    result = JobDescriptionExtractor().extract(
        '<script type="application/ld+json">'
        + json.dumps(posting)
        + "</script>",
        "text/html",
    )

    assert result.responsibilities == [
        "1. Build and optimize AI Agent frameworks. 2. Deploy vertical LLM services."
    ]
    assert result.requirements == [
        "1. Three years of LLM engineering experience. 2. Strong Python and PyTorch skills."
    ]
    assert result.completeness == "complete"
```

- [ ] **Step 2: Add a failing Schneider-style heading test**

```python
def test_json_ld_recognizes_core_responsibilities() -> None:
    posting = {
        "@type": "JobPosting",
        "title": "Senior AI Engineer",
        "description": (
            "<h2>核心职责</h2><ul><li>开发企业 AI Agent 平台。</li></ul>"
            "<h2>任职要求</h2><ul><li>熟悉 Python 和 RAG。</li></ul>"
        ),
    }

    result = JobDescriptionExtractor().extract(
        '<script type="application/ld+json">'
        + json.dumps(posting, ensure_ascii=False)
        + "</script>",
        "text/html",
    )

    assert result.responsibilities == ["开发企业 AI Agent 平台。"]
    assert result.requirements == ["熟悉 Python 和 RAG。"]
    assert result.completeness == "complete"
```

- [ ] **Step 3: Run the two tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_job_description_extractor.py -q --tb=short -p no:cacheprovider
```

Expected: Randstad responsibilities/requirements are empty and “核心职责” is
not recognized.

- [ ] **Step 4: Generalize inline section parsing**

Rename `_INLINE_CHINESE_SECTION` and `_split_inline_chinese_sections` to
`_INLINE_SECTION` and `_split_inline_sections`. Build the pattern only from
approved, explicit markers:

```python
_INLINE_SECTION = re.compile(
    r"(?P<label>"
    r"about the job|responsibilities|what you will do|job description|"
    r"skills and experience required|requirements|qualifications|"
    r"what we(?:'|’)re looking for|"
    r"岗位职责|工作职责|职位描述|岗位描述|核心职责|主要职责|工作内容|"
    r"任职要求|岗位要求|职位要求|我们希望[你您](?:有|具备|拥有)?"
    r")\s*[:：.]?\s*",
    re.IGNORECASE,
)
```

Add `"job description"` to responsibility aliases and
`"核心职责"`, `"主要职责"`, `"工作内容"` to Chinese responsibility aliases.
Use the same inline fallback in `_from_json_ld` and `_from_html`. Continue
preferring already parsed heading sections so inline scanning cannot overwrite
stronger structure.

- [ ] **Step 5: Run extractor and fallback tests and verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_job_description_extractor.py tests/unit/test_job_page_fallback.py -q --tb=short -p no:cacheprovider
```

Expected: PASS; malformed JSON-LD, challenge pages, Chinese inline sections,
Randstad inline sections, and Schneider headings all remain covered.

- [ ] **Step 6: Review and commit only Task 2 files**

```powershell
git diff --check -- src/starter_agent/tools/adapters/job_description_extractor.py tests/unit/test_job_description_extractor.py tests/unit/test_job_page_fallback.py
git add -- src/starter_agent/tools/adapters/job_description_extractor.py tests/unit/test_job_description_extractor.py tests/unit/test_job_page_fallback.py
git diff --cached --check
git commit -m "fix: extract inline job description sections"
```

---

### Task 3: Compare normalized job evidence instead of full dynamic snapshot hashes

**Files:**
- Modify: `src/starter_agent/job_research/page_reader.py:180-240`
- Test: `tests/unit/test_job_page_reader.py`

**Interfaces:**
- Consumes: two successful `ToolResult` snapshots and the expected final URL.
- Produces: `_validate_snapshots(...) -> str | None`, accepting stable job
  evidence even when unrelated snapshot metadata changes.

- [ ] **Step 1: Add a failing dynamic-page stability test**

```python
async def test_dynamic_snapshot_hashes_are_stable_when_job_evidence_matches() -> None:
    snapshots = iter([
        _snapshot("a" * 64),
        _snapshot("b" * 64),
    ])

    async def call(tool_name, arguments, _context):
        if tool_name.endswith("browser_navigate"):
            result = ToolResult(
                ok=True,
                data={"source_url": URL},
                metadata={"source_url": URL, "final_url": URL},
            )
        elif tool_name.endswith("browser_wait_for"):
            result = ToolResult(ok=True)
        else:
            result = next(snapshots)
        return result, _trace(tool_name, arguments)

    result = await PlaywrightJobPageReader(call, sleeper=_no_sleep).read(
        URL, _context()
    )

    assert result.ok
    assert len(result.attempts) == 1
```

- [ ] **Step 2: Add a materially changed JD test**

Create two snapshots with different `structured_content["requirements"]` and
different hashes. Assert two attempts occur and the final error is
`page_not_stable`.

- [ ] **Step 3: Add bounded metadata-evidence stability tests**

Create snapshots without `structured_content`, but with identical normalized
`snapshot_headings` and `snapshot_signal_samples`.

- With content sizes `900` and `1050`, assert the pair is stable.
- With content sizes `900` and `1300`, assert the pair is
  `page_not_stable`.
- With title-only metadata, assert exact-hash fallback remains active.

- [ ] **Step 4: Run reader tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_job_page_reader.py -q --tb=short -p no:cacheprovider
```

Expected: the dynamic-page test fails because current code compares the full
snapshot hash.

- [ ] **Step 5: Implement a bounded job-evidence signature**

Add:

```python
@classmethod
def _job_evidence_signature(cls, result: ToolResult) -> tuple[str, ...]:
    data = result.data if isinstance(result.data, dict) else {}
    structured = data.get("structured_content")
    if isinstance(structured, dict):
        responsibilities = structured.get("responsibilities") or []
        requirements = structured.get("requirements") or []
        if responsibilities or requirements:
            values = [
                structured.get("title"),
                structured.get("location"),
                *responsibilities,
                *requirements,
            ]
        else:
            values = []
    else:
        headings = (
            result.metadata.get("snapshot_headings")
            or data.get("snapshot_headings")
            or []
        )
        samples = (
            result.metadata.get("snapshot_signal_samples")
            or data.get("snapshot_signal_samples")
            or []
        )
        values = [*headings, *samples] if samples else []
    return tuple(
        " ".join(str(value).split()).casefold()
        for value in values
        if isinstance(value, str) and value.strip()
    )
```

In `_validate_snapshots`, after URL and minimum-size checks, derive each
reported content size from the same bounded snapshot metadata already used by
the minimum-size validation:

- if both signatures are non-empty and equal and their size delta is at most
  20% of the larger size, return `None`;
- if signatures match but the size delta exceeds 20%, return
  `page_not_stable`;
- if both signatures are non-empty and differ, return `page_not_stable`;
- otherwise retain the existing hash comparison as a conservative fallback.

Do not treat title-only evidence as stable; require at least one responsibility
or requirement value for structured evidence, or at least one signal sample
for metadata evidence.

- [ ] **Step 6: Run reader tests and verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_job_page_reader.py tests/unit/test_mcp_tool_adapter.py -q --tb=short -p no:cacheprovider
```

Expected: PASS; dynamic widgets no longer force a retry, while changed JD
content remains unstable.

- [ ] **Step 7: Review and commit only Task 3 files**

```powershell
git diff --check -- src/starter_agent/job_research/page_reader.py tests/unit/test_job_page_reader.py
git add -- src/starter_agent/job_research/page_reader.py tests/unit/test_job_page_reader.py
git diff --cached --check
git commit -m "fix: stabilize dynamic job page snapshots"
```

---

### Task 4: Enforce three complete JDs, ten candidates, and a soft 180-second deadline

**Files:**
- Modify: `src/starter_agent/settings.py:292-297`
- Modify: `config/config.example.yaml:77-82`
- Modify: `config/config.yaml:81-86`
- Modify: `src/starter_agent/application.py:235-270`
- Modify: `src/starter_agent/interfaces/api.py:315-385`
- Modify: `src/starter_agent/skills/job_research.py:427-670`
- Test: `tests/unit/test_settings.py`
- Test: `tests/unit/test_job_research_skill.py`
- Test: `tests/integration/test_job_research_orchestration.py`

**Interfaces:**
- Adds: `JobResearchConfig.retrieval_budget_seconds: int = 180`.
- Changes default: `JobResearchConfig.max_candidate_urls` from `5` to `10`.
- Adds optional `max_candidates: int = 10` and
  `retrieval_budget_seconds: float = 180` to candidate analysis boundaries.
- Produces: `candidate_attempts`, `budget_exhausted`, `candidate_limit`, and
  `retrieval_budget_seconds` in result data. Each attempt also keeps bounded
  candidate ranking provenance for diagnosis.

- [ ] **Step 1: Add failing settings tests**

```python
def test_job_research_defaults_prioritize_complete_jds() -> None:
    settings = load_settings("config/config.example.yaml")

    assert settings.job_research.max_candidate_urls == 10
    assert settings.job_research.target_valid_jds == 3
    assert settings.job_research.retrieval_budget_seconds == 180
```

- [ ] **Step 2: Run the settings test and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_settings.py -q --tb=short -p no:cacheprovider
```

Expected: maximum candidates is 5 and the budget field is absent.

- [ ] **Step 3: Add deterministic orchestration stop tests**

Inject `clock: Callable[[], float] = perf_counter` into
`JobResearchOrchestrator.__init__`. In
`tests/unit/test_job_research_skill.py`, promote the small gate/executor/
orchestrator setup currently nested in
`test_network_guard_snapshot_rejection_uses_fallback_instead_of_crashing` into
module-level `_FallbackGate`, `_FallbackExecutor`, `_FallbackOrchestrator`, and
`_no_sleep` helpers so both the existing regression and the new budget tests
use the same real browser-failure boundary. Add these helpers:

```python
class ScriptedClock:
    def __init__(self, values: list[float]) -> None:
        self.values = iter(values)

    def __call__(self) -> float:
        return next(self.values)


class ScriptedFallback:
    def __init__(self, outcomes: list[str]) -> None:
        self.outcomes = iter(outcomes)

    async def retrieve(self, candidate):
        outcome = next(self.outcomes)
        payload = {
            "title": candidate.title,
            "company": "Example",
            "location": "Beijing",
            "responsibilities": ["Build agent workflows"],
            "requirements": ["Python"],
            "source_url": candidate.url,
            "retrieval_method": (
                "http_json_ld" if outcome == "verified" else "search_snippet"
            ),
            "validation_state": (
                "verified" if outcome == "verified" else "partial_verified"
            ),
        }
        return SimpleNamespace(
            jobs=(payload,) if outcome == "verified" else (),
            partial_jobs=(payload,) if outcome == "partial" else (),
            method=payload["retrieval_method"],
            failures=(),
        )


def candidates(count: int) -> tuple[JobCandidate, ...]:
    return tuple(
        JobCandidate(
            url=f"https://jobs.example.test/{index}",
            title=f"Agent Engineer {index}",
            url_kind="structured_apply",
            confidence=1.0,
            provider_position=index,
            score=1.0,
            reason_codes=("employer_detail_signal",),
        )
        for index in range(count)
    )


async def test_partial_results_do_not_consume_complete_jd_target() -> None:
    orchestrator = _FallbackOrchestrator(
        None,  # type: ignore[arg-type]
        _FallbackExecutor(),  # type: ignore[arg-type]
        page_fallback=ScriptedFallback(
            ["partial", "verified", "partial", "verified", "verified"]
        ),
        browser_sleeper=_no_sleep,
        clock=ScriptedClock([0, 0, 1, 2, 3, 4]),
    )
    result = await orchestrator.analyze_candidates(
        query="Beijing AI Agent",
        candidates=candidates(5),
        context=ToolContext(session_id=uuid4(), turn_id=uuid4()),
        target_count=3,
        max_candidates=10,
        retrieval_budget_seconds=180,
        resume_evidence=[],
    )

    assert len(result.data["jobs"]) == 3
    assert len(result.data["partial_jobs"]) == 2
    assert len(result.data["candidate_attempts"]) == 5


async def test_soft_deadline_prevents_starting_the_next_candidate() -> None:
    orchestrator = _FallbackOrchestrator(
        None,  # type: ignore[arg-type]
        _FallbackExecutor(),  # type: ignore[arg-type]
        page_fallback=ScriptedFallback(["partial"]),
        browser_sleeper=_no_sleep,
        clock=ScriptedClock([0, 0, 181]),
    )
    result = await orchestrator.analyze_candidates(
        query="Beijing AI Agent",
        candidates=candidates(3),
        context=ToolContext(session_id=uuid4(), turn_id=uuid4()),
        max_candidates=10,
        retrieval_budget_seconds=180,
        resume_evidence=[],
    )

    assert len(result.data["candidate_attempts"]) == 1
    assert result.data["budget_exhausted"] is True
```

Add a third test with eleven candidates and only partial outcomes; assert ten
attempts and no eleventh start.

The shared `_FallbackExecutor` should continue forcing the browser snapshot failure
used by `test_network_guard_snapshot_rejection_uses_fallback_instead_of_crashing`,
so these tests exercise the real fallback boundary rather than bypassing it.

- [ ] **Step 4: Run orchestration tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_job_research_skill.py tests/integration/test_job_research_orchestration.py -q --tb=short -p no:cacheprovider
```

Expected: the new arguments/data fields do not exist or all candidates start.

- [ ] **Step 5: Implement settings and deadline propagation**

Use:

```python
class JobResearchConfig(BaseModel):
    jd_freshness_days: int = Field(default=30, ge=1, le=365)
    max_candidate_urls: int = Field(default=10, ge=1, le=10)
    target_valid_jds: int = Field(default=3, ge=1, le=5)
    retrieval_budget_seconds: int = Field(default=180, ge=30, le=600)
```

Set both YAML configs to:

```yaml
job_research:
  jd_freshness_days: 30
  max_candidate_urls: 10
  target_valid_jds: 3
  retrieval_budget_seconds: 180
```

Pass `max_candidates` and `retrieval_budget_seconds` from the API through
`ApplicationService.analyze_job_research_candidates`.

In `analyze_candidates`:

```python
deadline = self.clock() + retrieval_budget_seconds
budget_exhausted = False
for index, candidate in enumerate(candidates[:max_candidates]):
    if self.clock() >= deadline:
        budget_exhausted = True
        break
    ...
```

Keep all existing `len(jobs) >= target_count` stops. Add the bounded batch
fields to `common_data`. Extend the local `record_attempt` helper to accept the
current `candidate`, add only bounded ranking diagnostics, then append and
audit the enriched attempt:

```python
def record_attempt(
    attempt: dict[str, Any],
    *,
    candidate: JobCandidate,
    call_id: str,
) -> None:
    attempt.update(
        candidate_score=candidate.score,
        candidate_page_kind=candidate.page_kind,
        candidate_reason_codes=list(candidate.reason_codes),
        matched_queries=list(candidate.matched_queries),
        search_engines=list(candidate.search_engines),
        started_before_deadline=True,
    )
    attempts.append(attempt)
    self._audit_candidate_attempt(attempt, context=context, call_id=call_id)
```

Pass `candidate=candidate` at every call site. In
`_audit_candidate_attempt`, add `candidate_score`,
`candidate_page_kind`, the first 20 `candidate_reason_codes`, the first 20
`matched_queries`, the first 10 `search_engines`, and
`started_before_deadline` to the existing payload, preserving its current
string-length bounds. Do not persist snippets, HTML, JSON-LD bodies, cookies,
credentials, or resume content.

- [ ] **Step 6: Run settings and orchestration tests and verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_settings.py tests/unit/test_job_research_skill.py tests/integration/test_job_research_orchestration.py tests/integration/test_api.py -q --tb=short -p no:cacheprovider
```

Expected: PASS; partial outcomes continue scanning, and target/count/deadline
each stop independently.

- [ ] **Step 7: Review and commit only Task 4 files**

```powershell
git diff --check -- src/starter_agent/settings.py config/config.example.yaml config/config.yaml src/starter_agent/application.py src/starter_agent/interfaces/api.py src/starter_agent/skills/job_research.py tests/unit/test_settings.py tests/unit/test_job_research_skill.py tests/integration/test_job_research_orchestration.py
git add -- src/starter_agent/settings.py config/config.example.yaml config/config.yaml src/starter_agent/application.py src/starter_agent/interfaces/api.py src/starter_agent/skills/job_research.py tests/unit/test_settings.py tests/unit/test_job_research_skill.py tests/integration/test_job_research_orchestration.py
git diff --cached --check
git commit -m "feat: prioritize complete job descriptions"
```

---

### Task 5: Render one concise status per source URL with complete JDs first

**Files:**
- Modify: `src/starter_agent/interfaces/api.py:734-925`
- Test: `tests/unit/test_job_research_api_answer.py`
- Test: `tests/integration/test_api.py`

**Interfaces:**
- Consumes: search results and JD result fields `jobs`, `partial_jobs`,
  `candidate_attempts`, and search statistics.
- Produces: a Chinese answer with non-overlapping `完整 JD`, `部分证据`, and
  `无法访问` sections; the selection prompt is the final line.

- [ ] **Step 1: Replace duplicate-output expectations with failing compact-output tests**

Create one mixed fixture containing:

- one verified Randstad job;
- one partial Schneider job with `http_json_ld`;
- one LinkedIn failed attempt;
- the same Schneider URL in `candidate_attempts`.
- search statistics with `filtered_collection_count=2`.

Assert:

```python
assert answer.count("https://careers.se.com/jobs/127591?lang=zh-cn") == 1
assert "完整 JD（1 个）" in answer
assert "部分证据（1 个）" in answer
assert "无法访问（1 个）" in answer
assert "摘要降级 · 浏览器页面持续变化 · HTTP 仅提取到部分章节" in answer
assert "browser_network_target_required" not in answer
assert answer.splitlines()[-1] == "请选择一个岗位后，我再继续做最终匹配分析或确认入库。"
```

Add a second test proving raw source URLs render once as Markdown source links
and long responsibilities are rendered as separate bounded bullets.

- [ ] **Step 2: Run answer tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_job_research_api_answer.py -q --tb=short -p no:cacheprovider
```

Expected: URLs repeat between partial/degraded sections, internal codes are
visible, and the selection prompt is not last.

- [ ] **Step 3: Implement one per-source presentation model**

Inside `_public_job_search_answer`, build URL-keyed maps:

```python
verified_by_url = {
    str(job.get("source_url") or ""): job
    for job in jobs
    if isinstance(job, dict) and job.get("source_url")
}
partial_by_url = {
    str(job.get("source_url") or ""): job
    for job in partial_jobs
    if isinstance(job, dict)
    and job.get("source_url")
    and job.get("source_url") not in verified_by_url
}
attempt_by_url = {
    str(item.get("source_url") or ""): item
    for item in attempts or []
    if isinstance(item, dict) and item.get("source_url")
}
```

Render failed attempts only when their URL exists in neither verified nor
partial maps. Remove the separate “抓取降级链接” section.

Map internal codes to bounded user labels:

```python
_JOB_RETRIEVAL_REASON_LABELS = {
    "page_not_stable": "浏览器页面持续变化",
    "browser_network_target_required": "浏览器访问受安全策略限制",
    "robots_blocked": "网站禁止自动读取",
    "selector_unmatched": "HTTP 未识别岗位章节",
    "access_blocked_challenge": "网站要求安全验证",
}
```

Keep original codes in `ToolResult.data` and audit records. Render source as
`[来源](<url>)`, cap verified responsibilities and requirements at the existing
bounded counts, and use one line per item.

- [ ] **Step 4: Condense statistics and move selection prompt**

Use one search line, including the pre-retrieval collection filter count:

```text
搜索：12 个查询变体 · 120 条原始结果 · 82 条去重结果 · 过滤集合页 2 · 56 个中文标题
```

Use one result line:

```text
结果：完整 JD 3 · 部分证据 2 · 无法访问 1 · 尝试候选 6/10
```

Append the selection prompt only after these sections and statistics.

- [ ] **Step 5: Run answer and API tests and verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_job_research_api_answer.py tests/integration/test_api.py -q --tb=short -p no:cacheprovider
```

Expected: PASS; each URL occurs once, complete JDs are first, and the prompt is
last.

- [ ] **Step 6: Review and commit only Task 5 files**

```powershell
git diff --check -- src/starter_agent/interfaces/api.py tests/unit/test_job_research_api_answer.py tests/integration/test_api.py
git add -- src/starter_agent/interfaces/api.py tests/unit/test_job_research_api_answer.py tests/integration/test_api.py
git diff --cached --check
git commit -m "fix: simplify job research results"
```

---

### Task 6: Run fixed regressions, public URL smoke, and the complete suite

**Files:**
- No repository source change is expected in this task.
- Existing tests: the focused and complete-suite commands below.
- Runtime artifacts: one temporary redacted smoke script/report and the JUnit
  report; delete them after results are read.

**Interfaces:**
- Consumes: the complete candidate-ranking, extraction, stability, orchestration,
  and output pipeline.
- Produces: test evidence and redacted per-URL outcomes without retaining full
  page content.

- [ ] **Step 1: Run the complete focused regression matrix**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_job_candidates.py tests/unit/test_job_search_location_recall.py tests/unit/test_search_jobs_serpapi.py tests/unit/test_job_description_extractor.py tests/unit/test_job_page_fallback.py tests/unit/test_job_page_reader.py tests/unit/test_job_research_skill.py tests/unit/test_job_research_api_answer.py tests/unit/test_settings.py tests/integration/test_job_research_orchestration.py tests/integration/test_api.py tests/integration/test_gate_no_bypass.py -q --tb=short -p no:cacheprovider
```

Expected: PASS with no failure or error.

- [ ] **Step 2: Run content-redacted public URL smoke**

Use `SafeWebFetcher` and `JobDescriptionExtractor` against:

- `https://www.randstad.com/jobs/ai-agent-llm-engineer_bei-jing-_47096669/`
- `https://careers.se.com/jobs/127591?lang=zh-cn`

The temporary diagnostic must print only URL, HTTP status, final URL,
extraction method, completeness, title, and responsibility/requirement counts.
It must not save or print full HTML, full JSON-LD, cookies, headers, resume
content, or credentials.

Expected:

- Randstad: `http_json_ld`, complete, responsibilities > 0, requirements > 0.
- Schneider: `http_json_ld`, complete, responsibilities > 0, requirements > 0,
  unless the external page has materially changed; if changed, report the new
  source-backed structure rather than weakening validation.

- [ ] **Step 3: Run a Beijing ranking diagnostic without external personal data**

Feed the two diagnosed LinkedIn collection rows plus direct Randstad,
Schneider, and Liepin job-detail rows into `rank_job_candidates`.

Expected:

- both LinkedIn collection rows are absent;
- direct job details occupy the first positions;
- no collection consumes the ten-candidate retrieval budget.

- [ ] **Step 4: Run the complete test suite with an explicit JUnit summary**

```powershell
.venv\Scripts\python.exe -m pytest --junitxml=.verification-complete-jd.xml --tb=short -p no:cacheprovider
```

On Windows, verify the underlying base-Python pytest process has exited before
reading the report. Read `tests`, `failures`, `errors`, `skipped`, and `time`
from the `<testsuite>` node.

Expected: `failures="0"` and `errors="0"`.

- [ ] **Step 5: Clean only generated verification artifacts**

Resolve every target to an absolute path and confirm it is inside the repository
before deleting:

- `.verification-complete-jd.xml`;
- the temporary redacted smoke script/report;
- `.session-only-trust-*-tests`;
- task-specific `.tmp-*` pytest directories.

Do not remove `.playwright-mcp`, `.uv-cache`, `data`, `logs`, user examples,
specifications, plans, or unrelated untracked files.

- [ ] **Step 6: Verify repository hygiene and final diff**

```powershell
git diff --check
git status --short
git log -8 --oneline
```

Expected: no whitespace errors, no generated artifacts, and only intended
source/test/config changes plus pre-existing user files.

- [ ] **Step 7: Present evidence**

Report:

- filtered collection count;
- attempted candidate count and stop condition;
- complete/partial/inaccessible counts;
- Randstad and Schneider extraction outcomes;
- focused and full-suite results;
- any remaining external blocking reason;
- exact commits created during Tasks 1-5.
