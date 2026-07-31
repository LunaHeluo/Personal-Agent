# Session-Aware Official JD Results Design

## Goal

Improve public job research so that a follow-up such as “选择第一个岗位做匹配分析” resolves the candidate from the current session, readable employer-hosted job descriptions are preferred, and verified jobs are rendered in the Candidate format requested by the user.

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

## Official Employer Discovery

Search uses two complementary lanes within a bounded request budget:

### General lane

Continue localized bilingual role/location queries against both Google Jobs and organic Google results. Location aliases remain dynamically resolved rather than hard-coded by city.

### Official-employer lane

Add bounded queries aimed at employer career sites. An application configuration list provides known official career hosts for large employers, initially including Tencent, ByteDance, Baidu, Alibaba, Meituan, and Huawei. The list is configuration, not ranking logic, so deployments can add or remove employers without changing code.

Generic employer-host detection remains available for companies outside that list. It uses job-detail URL shape, company metadata, same-company mirror grouping, and the absence of known job-board or aggregator signals. A configured host is strong evidence of an official source; a generic heuristic is weaker evidence and must not assert ownership when ambiguous.

The query plan reserves a bounded subset of variants for official-employer discovery without removing localized Chinese and English coverage. Each result retains its matched query and search engine for diagnostics.

## Candidate Ranking and Retrieval

Candidate scoring adds these source signals:

- strong boost for a configured official career host with a job-detail URL;
- moderate boost for a probable employer-hosted job-detail URL;
- penalty for aggregators, collection pages, and job-board mirrors when a readable employer detail exists;
- existing role, location, language, section, and completeness signals remain in force.

Mirror groups prefer:

1. readable official employer detail;
2. readable recruiting-platform job detail;
3. readable aggregator mirror;
4. partial search evidence.

The first retrieval window reserves room for official-employer candidates that pass role and location checks. This reservation does not admit irrelevant official jobs. Failed attempts do not consume the complete-JD target. Retrieval continues until the configured complete-JD target is reached, the candidate limit is exhausted, or the existing time budget expires.

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
- configured Tencent and ByteDance job-detail hosts receive official-source reason codes and outrank job-board or aggregator mirrors;
- official-employer query variants coexist with localized Chinese and English variants;
- irrelevant or wrong-location official jobs do not bypass relevance checks;
- readable official jobs are represented in the first retrieval window;
- the Candidate template contains overview, complete responsibility and requirement sections, match summary, Candidate ID, and pending status;
- partial evidence is rendered only when the complete target is unmet;
- inaccessible URLs and their failure codes remain in diagnostics but not in the user answer;
- selection order is identical to displayed Candidate order;
- existing job research, API, persistence, and full test suites remain green.

## Non-Goals

- Automatically submitting job applications.
- Automatically ingesting a retrieved JD into the knowledge base before user confirmation.
- Claiming that a generic employer-like domain is official without adequate evidence.
- Hard-coding behavior to a specific city.
