# Complete-JD-First Job Research Design

**Date:** 2026-07-31

**Status:** Approved for specification review

## Goal

Make public job research optimize for useful, complete job descriptions rather
than early search snippets. A normal run targets three complete JDs, may inspect
up to ten candidates, and has a 180-second total retrieval budget. Partial
evidence remains useful but never consumes the complete-JD target.

## Confirmed Product Behavior

- Prefer complete, source-backed JDs over a larger number of search snippets.
- Stop when three complete JDs have been obtained, ten candidates have been
  attempted, the retrieval budget is exhausted, or no candidates remain.
- Keep verified results already obtained when later candidates fail or time out.
- Never upgrade a search snippet to a complete JD.
- Show each source URL once in the final answer.
- Put the job-selection prompt after all result and diagnostic sections.

The 180-second budget is a soft orchestration deadline. A network or browser
operation already in progress may complete within its own bounded timeout, but
no new candidate starts after the budget has expired.

## Candidate Quality and Filtering

Candidate assessment runs before any browser or HTTP retrieval.

Collection and search pages are excluded from the candidate budget. Detection
includes:

- titles shaped like `1,000+ ... jobs`, including cases where Chinese text is
  immediately adjacent to `jobs`;
- LinkedIn paths ending in forms such as `-jobs-worldwide`;
- job-search, topic, pagination, generic careers, login, and result-list pages;
- pages whose snippets represent several unrelated roles rather than one job.

Candidate ordering remains:

1. employer-hosted job detail;
2. structured or directly accessible recruitment-platform detail;
3. job-specific aggregator detail;
4. unknown candidates requiring browser classification.

Target-location matches, Chinese titles, Agent/LLM relevance, and job-section
signals increase score. Collection, non-target location, expired, blocked, and
thin-snippet signals decrease score. The ranked results shown to the user and
the candidates sent to retrieval must come from the same ranked list.

## Retrieval Pipeline

For each candidate, the orchestrator uses the existing bounded sequence:

1. Playwright initial attempt with the configured 15-second wait.
2. One Playwright retry with the configured 30-second wait.
3. Safe HTTP fetch and JSON-LD/HTML extraction.
4. Structured search data or search snippet as partial evidence.

The orchestrator proceeds to later candidates while fewer than three complete
JDs exist. Partial evidence does not stop the loop. At most ten candidates are
started, and a new candidate is not started after the soft 180-second deadline.

### Stable Page Evidence

Playwright stability no longer requires the entire snapshot hash to be equal.
The comparison uses normalized job evidence:

- final source URL;
- job title and location;
- recognized section headings;
- normalized responsibilities and requirements text;
- bounded content-length tolerance.

Volatile accessibility references, timestamps, carousels, application-process
widgets, ads, and unrelated navigation changes are excluded. A page is stable
when two snapshots preserve the same source and job evidence. Empty or
materially different job evidence still returns `page_not_stable`.

## JD Extraction

JSON-LD and HTML extraction recognize both heading-delimited and inline
sections.

English responsibility markers include:

- `about the job`;
- `responsibilities`;
- `what you will do`;
- `job description`.

English requirement markers include:

- `skills and experience required`;
- `requirements`;
- `qualifications`;
- `what we're looking for`.

Chinese responsibility markers include:

- 岗位职责、工作职责、职位描述、岗位描述；
- 核心职责、主要职责、工作内容。

Chinese requirement markers retain the existing labels and the approved
“我们希望你/您” variants.

A complete JD still requires a source-backed title plus non-empty
responsibilities and requirements. Pages with only one section remain partial.

## Output Design

The answer contains three non-overlapping sections:

1. **完整 JD** — expanded by default, showing title, company, location,
   responsibilities, requirements, match summary, and source.
2. **部分证据** — one compact record per URL, showing title, source, short
   snippet, and a user-readable reason.
3. **无法访问** — only candidates that yielded no usable evidence.

A URL shown under partial evidence is not repeated in a separate degradation
list. Its compact status line contains the final provenance and reason, for
example:

`摘要降级 · 浏览器页面持续变化 · HTTP 未识别章节`

Stable internal error codes remain in structured data and audit logs. The
default answer uses concise Chinese labels; technical codes may appear in a
single optional diagnostic summary rather than dominating the result.

Long URLs are rendered as source links instead of repeated raw text. Full JD
sections keep bounded item counts and readable line breaks. Search statistics
are condensed to one line. The final job-selection prompt appears last.

## Observability

Each attempted candidate retains:

- rank, score, page kind, and reason codes;
- matched queries and search engines;
- browser attempts and normalized stability diagnostics;
- HTTP extraction method and failure codes;
- final validation state and retrieval duration;
- whether the candidate started before the total budget deadline.

Batch statistics distinguish filtered collections, attempted candidates,
complete JDs, partial evidence, inaccessible pages, and budget exhaustion.

## Testing

TDD coverage must prove:

- LinkedIn `1,000+ ... jobs` titles are filtered even without whitespace before
  `jobs`;
- `-jobs-worldwide` paths are collection pages;
- filtered collections do not consume the ten-candidate budget;
- Randstad-style inline JSON-LD yields responsibilities and requirements;
- Schneider-style “核心职责” yields responsibilities;
- dynamic non-JD snapshot changes do not cause `page_not_stable`;
- materially changed JD content still causes `page_not_stable`;
- partial evidence does not count toward the target of three complete JDs;
- retrieval stops at three complete JDs, ten candidates, or the soft deadline;
- complete, partial, and inaccessible outputs contain no duplicate URL;
- the selection prompt is the final output line;
- mixed success retains every complete JD and concise per-source reasons.

Focused tests run first, followed by the complete test suite and a public,
content-redacted smoke against the diagnosed Randstad and Schneider URLs.

## Acceptance Criteria

- A repeat of the diagnosed Beijing result excludes both LinkedIn collection
  pages before retrieval.
- The current Randstad page is eligible for a complete HTTP JSON-LD result.
- A Schneider JSON-LD page using “核心职责” and “任职要求” is eligible for a
  complete result.
- A normal run targets three complete JDs before presenting snippets, subject
  to the ten-candidate and 180-second limits.
- No URL appears in more than one final-answer section.
- Full JD content is the most prominent part of the answer.
- Existing Gate, network safety, robots, privacy, audit, retry, and redaction
  guarantees remain intact.
