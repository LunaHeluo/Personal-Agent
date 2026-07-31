# Session-Aware Precise JD Results Design

## Goal

Improve public job research so that a follow-up such as “选择第一个岗位做匹配分析” resolves the candidate from the current session, SerpAPI returns more precise job-detail evidence, readable complete job descriptions are preferred, and verified jobs are rendered in the Candidate format requested by the user.

The implementation must be location-agnostic. Shanghai, Beijing, Chengdu, or any other location follows the same alias expansion, candidate ranking, selection, and rendering rules.

## Confirmed Product Behavior

The source priority for job research is:

1. A pending candidate or selected job from the current session.
2. A JD or job URL explicitly supplied in the current user message.
3. An explicit source instruction from the user, such as knowledge-only or fresh public search.
4. A highly relevant, current, location-compatible saved JD.
5. Public job search and page retrieval.

A pending session selection always wins over generic request classification and knowledge-base matching. The system must never replace “第一个岗位” with an unrelated saved JD.

## Session Candidate State

Each successful public job-search response persists an ordered snapshot scoped to the current session. Each record contains:

- a stable UUID `candidate_id`;
- its one-based displayed ordinal;
- the originating search turn ID;
- title, company, location, and source URL;
- source classification and retrieval method;
- normalized responsibilities and requirements;
- resume-match analysis already produced for the candidate;
- evidence level (`complete` or `partial`);
- status (`PENDING_CONFIRMATION`, `SELECTED`, or `EXPIRED`);
- creation and expiry timestamps.

Starting a new public search expires the previous pending snapshot for that session. Selection state is never shared across sessions. A pending snapshot expires after 60 minutes.

Before generic chat classification, deterministic selection parsing recognizes at least:

- `第一个岗位`, `第二个岗位`, and equivalent Chinese ordinals;
- `选择1`, `选 2`, `第 3 个`;
- an exact visible Candidate ID.

Only a Candidate from the same session and an unexpired snapshot can be selected. Complete candidates may be selected normally. If a user selects a partial candidate, the response must say that the JD is incomplete and offer to retry retrieval; it must not silently substitute a knowledge-base JD.

## SerpAPI Query Strategy

Search uses a bounded portfolio of complementary queries rather than a company allowlist or company-specific weighting.

### Localized role queries

Continue localized Chinese and English role/location queries against both Google Jobs and organic Google results. Location aliases remain dynamically resolved rather than hard-coded by city. The query portfolio preserves the user-visible target role and adds only bounded, non-sensitive role-family synonyms; it never sends resume prose to SerpAPI.

### Job-detail evidence queries

Reserve part of the query budget for result shapes that are more likely to contain a complete JD. Chinese variants combine the resolved location and role with signals such as `招聘`, `岗位职责`, `任职要求`, and `职位描述`. English variants combine the location and role with signals such as `careers`, `responsibilities`, `requirements`, and `job description`.

Organic queries may exclude known collection-only or low-value result shapes when doing so does not remove all coverage. They must not restrict discovery to a single recruitment platform or a fixed employer list. Google Jobs structured apply/share links and ordinary organic results remain enabled for every location.

Each result retains its matched query, engine, provider position, and source URL. Diagnostics report which query families produced complete, partial, filtered, and failed candidates so query precision can be measured rather than inferred.

## Candidate Ranking and Retrieval

Candidate scoring adds these general source and readability signals:

- strong boost for a structured direct-apply link or a URL classified as a concrete job-detail page;
- boost for title, company, location, and snippet evidence that indicates responsibilities and requirements;
- boost for pages whose host/path has previously yielded a complete JD during the current search, without persisting company-specific preference;
- penalty for aggregators, collection pages, generic careers pages, expired pages, login pages, and thin snippets;
- penalty for hosts or result shapes that repeatedly fail preflight readability during the current search;
- existing role, location, language, section, and completeness signals remain in force.

Mirror groups prefer:

1. readable direct employer or structured-apply detail;
2. readable recruiting-platform job detail;
3. readable aggregator mirror;
4. partial search evidence.

Before expensive browser retrieval, candidates receive a bounded readability preflight using URL shape, response status when available, JSON-LD presence, content type, login/expired signals, and snippet completeness. This is a prioritization hint rather than proof of completeness. Failed attempts do not consume the complete-JD target. Retrieval continues until the configured complete-JD target is reached, the candidate limit is exhausted, or the existing time budget expires.

## User-Facing Output

Verified complete jobs are displayed first using this structure:

```markdown
# Candidate 1：AI智能体开发工程师

## 岗位概览

- 公司：示例公司
- 岗位：AI智能体开发工程师
- 地点：北京
- 来源：公司招聘官网
  https://careers.example.com/jobs/42
- 读取状态：已读取完整 JD 核心字段
- Candidate ID：`550e8400-e29b-41d4-a716-446655440000`
- 状态：`PENDING_CONFIRMATION`

## 职责摘录

- 负责……

## 任职要求

- 熟悉……

## 简历匹配概览

- 匹配项：3
- 证据缺口：1
```

All normalized responsibility and requirement items are rendered instead of the previous fixed three/five-item truncation. A per-candidate safety character limit prevents pathological pages from overwhelming the response. If the limit is reached, the answer explicitly says that the display was truncated while the stored normalized JD remains intact.

The public answer follows these visibility rules:

- complete JDs always appear before partial evidence;
- partial evidence appears only when the complete-JD target was not reached and the snippet contains substantive job evidence;
- inaccessible, blocked, timed-out, crashed, login-only, expired, and otherwise failed URLs never appear in the user-facing answer;
- failure codes and URLs remain available in internal attempt diagnostics and logs;
- a failure never occupies a Candidate number or complete-JD target slot;
- the final selection prompt refers to the visible Candidate number or Candidate ID.

## Error Handling

If a selection refers to an expired or missing snapshot, the assistant asks the user to run a new search or provide the URL. It does not fall back to an unrelated knowledge-base JD.

If no complete JD can be retrieved, the answer may show substantive partial evidence, but it must clearly state that no complete JD was verified. If neither complete nor substantive partial evidence exists, return a concise no-results message without exposing failed URLs.

All detailed retrieval failure reasons remain in structured diagnostics for operators and tests.

## Persistence and Boundaries

Session candidate persistence belongs in the session-store infrastructure rather than message text or transient tool artifacts. The API orchestration layer owns selection precedence. Query planning owns official-site query generation. Candidate assessment owns source classification and ranking. Rendering owns the Candidate template and visibility rules.

These boundaries avoid coupling selection behavior to the language model and allow each unit to be tested independently.

## Test Strategy

Use TDD for every behavior change. Required coverage includes:

- a two-turn Beijing search followed by `选择第一个岗位` returns the first displayed Beijing Candidate and never calls the knowledge answer path;
- numeric, Chinese ordinal, and Candidate-ID selection parsing;
- cross-session and expired selections are rejected without knowledge fallback;
- a new search expires the prior pending snapshot;
- location aliases remain generic for arbitrary locations;
- precise job-detail query variants coexist with localized Chinese and English role variants;
- arbitrary company-hosted detail pages can outrank job-board or aggregator mirrors without a company allowlist;
- irrelevant or wrong-location company pages do not bypass relevance checks;
- structured apply links, concrete detail paths, JSON-LD signals, and section-rich snippets improve retrieval priority;
- repeated unreadable result shapes are deprioritized within the current search without hiding diagnostics;
- the Candidate template contains overview, complete responsibility and requirement sections, match summary, Candidate ID, and pending status;
- partial evidence is rendered only when the complete target is unmet;
- inaccessible URLs and their failure codes remain in diagnostics but not in the user answer;
- selection order is identical to displayed Candidate order;
- existing job research, API, persistence, and full test suites remain green.

## Non-Goals

- Automatically submitting job applications.
- Automatically ingesting a retrieved JD into the knowledge base before user confirmation.
- Maintaining a Tencent, ByteDance, or other employer allowlist or applying company-specific ranking weight.
- Claiming that a generic employer-like domain is official without adequate evidence.
- Hard-coding behavior to a specific city.
