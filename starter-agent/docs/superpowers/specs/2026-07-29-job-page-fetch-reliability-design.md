# Job Page Fetch Reliability Design

**Date:** 2026-07-29

**Status:** Approved for specification review

## Goal

Make resume-driven job research return every usable job description it can obtain,
while preserving a concrete per-URL outcome when Playwright, extraction, or a fallback
fails. The workflow must no longer collapse upstream failures into
`mcp_tool_error` or discard successful jobs because another candidate failed.

## Confirmed Baseline

The 2026-07-29 live diagnostic established three distinct failure classes:

- The HERE iCIMS page produced an 8,406-character Playwright snapshot and failed in
  JD extraction, not navigation.
- The Agently careers page produced a 7,138-character snapshot containing three jobs,
  but the single-JD extractor rejected the collection page.
- The Analog Devices Workday page produced an empty 141-character snapshot after
  about 5.5 seconds, while search-engine structured content contained the complete
  job. The current workflow did not wait 15 seconds or retry.

A later SerpAPI candidate in the same workflow produced a verified Randstad JD. The
evidence therefore does not support treating all candidates as uncrawlable. It shows
that candidate classification, browser readiness, extraction, and error reporting
must be handled independently.

## Constraints

- Each URL gets at most two Playwright attempts: one initial attempt and one retry.
- Each attempt waits at least 15 seconds before extracting content.
- The implementation must not enable Playwright MCP's
  `browser_run_code_unsafe`, which the upstream project describes as equivalent to
  arbitrary code execution in the MCP server process.
- Network readiness uses the approved `browser_wait_for` capability plus stable-page
  evidence: two consecutive snapshots with the same final URL and stable normalized
  content signals. This is the approved safe substitute for invoking an unsafe raw
  `page.waitForLoadState("networkidle")` code snippet.
- A retry increases the wait budget to 30 seconds. It may restart the isolated MCP
  browser with the configured alternate User-Agent when the runtime supports a safe
  restart; otherwise the increased wait is the required changed retry condition.
- All HTTP fallback requests continue to use `SafeWebFetcher`, including DNS pinning,
  redirect validation, peer validation, response limits, robots policy, and URL
  sanitization.
- Search snippets are evidence of last resort. They must be labeled as fallback
  evidence and cannot silently become a Playwright-verified JD.
- Existing Gate, approval, audit, redaction, and public-web scope rules remain in
  force. No fallback may bypass them.

## Architecture

The job-research orchestrator keeps ownership of candidate iteration but delegates
one URL to a focused retrieval pipeline:

1. `PlaywrightJobPageReader` navigates, waits, observes stable snapshots, retries once,
   and returns either a snapshot or a classified browser failure.
2. `JobDescriptionExtractor` converts a single job page or a bounded collection page
   into one or more source-backed JD records.
3. `HttpJobPageFallback` uses `SafeWebFetcher` and the same extractor when Playwright
   navigation, readiness, snapshot, or extraction fails.
4. `SearchEvidenceFallback` converts structured SerpAPI job fields, JSON-LD retained
   in the candidate, or a search snippet into an explicitly partial JD only when the
   available fields meet the minimum evidence contract.
5. The candidate accumulator preserves every verified or partial result and one final
   diagnostic record for every attempted URL.

The browser reader returns data; it does not validate whether the data is a JD. The
extractor parses content; it does not decide candidate ordering. The orchestrator
selects fallbacks and aggregates results; it does not interpret raw MCP error text.

## Browser Readiness

For each attempt the reader performs:

1. `browser_navigate(url)` with the existing navigation timeout.
2. `browser_wait_for({"time": 15})` on the initial attempt or
   `browser_wait_for({"time": 30})` on the retry.
3. `browser_snapshot()` twice, separated by a short bounded stability interval.
4. Verification that both snapshots retain the same sanitized final URL.
5. Stability comparison over normalized snapshot length, headings, JD signal samples,
   and content hash. Minor volatile accessibility references are excluded from the
   comparison.

If the first snapshot is already a valid, source-backed JD, the second snapshot still
runs so the workflow retains stable-page evidence. If readiness never stabilizes, the
attempt returns `page_not_stable` and proceeds to retry or fallback.

## Error Classification

`McpToolResultAdapter` retains a bounded, sanitized upstream error summary and maps
known Playwright failures to stable codes:

| Error code | Meaning |
| --- | --- |
| `playwright_timeout` | Navigation, wait, or snapshot exceeded its timeout |
| `access_blocked_403` | The response or error page reports HTTP 403 |
| `authentication_required` | Login or authentication is required |
| `selector_unmatched` | Expected page content or extraction selectors were absent |
| `browser_crashed` | Browser/page/context closed or the MCP browser process crashed |
| `snapshot_mismatch` | Snapshot source does not match the immediately preceding navigation |
| `page_not_stable` | Stable readiness evidence was not reached within the attempt budget |
| `job_not_found` | HTTP 404/410 or an explicit expired-job page was returned |
| `mcp_unknown_error` | Sanitized upstream failure does not match a known class |

Raw credentials, query secrets, cookies, authorization headers, complete HTML, and
complete snapshots are never placed in the error summary. The generic
`mcp_tool_error` code is removed from user-facing candidate outcomes.

Extraction failures are distinct from browser failures. A page that loaded but could
not be parsed returns `selector_unmatched`, `collection_split_failed`, or the existing
field-level validation reason codes.

## Retry and Fallback Flow

The pipeline evaluates one URL as follows:

1. Run the initial Playwright attempt with a 15-second wait.
2. If it fails or yields unusable content, retry once with a 30-second wait and, when
   safely supported, an alternate configured User-Agent.
3. If Playwright still fails, call `SafeWebFetcher.fetch(url)` and extract JSON-LD or
   HTML.
4. If HTTP fails or produces no usable JD, evaluate retained structured search fields
   and snippet evidence.
5. Store the best result and the full ordered attempt summary.

A successful HTTP fallback is labeled `http_json_ld` or `http_html`. A search fallback
is labeled `search_structured` or `search_snippet` and has validation state
`partial_verified` unless it includes source-backed title, responsibilities, and
requirements under the existing validation contract.

Fallback failures use the existing safe HTTP codes (`fetch_timeout`,
`access_blocked`, `authentication_required`, `job_not_found`, `rate_limited`, or
`fetch_failed`) and remain attached to the candidate attempt.

## Collection Pages

A collection page such as Agently may yield multiple jobs when it has repeated,
bounded blocks containing:

- a job title heading;
- a responsibilities section;
- a requirements section; and
- an optional location section.

Each extracted job retains the collection URL plus a deterministic source-fragment
identifier derived from the job heading and block ordinal. A collection page without
complete repeated boundaries is not guessed into separate jobs; it proceeds to HTTP
or search fallback and records `collection_split_failed` if still unusable.

## Partial Success and API Output

The orchestrator never treats one URL failure as a batch failure. It continues until
the configured verified-JD target is reached or all candidates are exhausted.

The API response contains:

- all verified JDs, including extraction method and final source URL;
- partial JDs in a separate section, including missing fields and fallback provenance;
- one line per failed URL with final failure code and safe reason;
- attempted, verified, partial, and failed counts;
- a batch error code only when no verified or partial JD exists.

The display must not claim `0/5` without also listing the outcome for each attempted
URL. Successful JD content remains visible even when other URLs fail.

## SerpAPI Candidate Quality

SerpAPI remains a discovery provider, not proof that a page is crawlable. Candidate
ranking uses the existing provider fields and page classifier to:

- prefer direct employer or structured apply URLs;
- prefer candidates with a job title, company, location, and job-specific snippet;
- penalize collection, login, expired-job, generic careers, and error-page signals;
- retain low-confidence organic results only after higher-confidence candidates;
- record provider position, candidate score, page kind, and reason codes.

No employer domain is permanently banned from one failed request. Crawlability is a
per-attempt observation, and retry/fallback evidence determines the final outcome.

## Observability

Each candidate record includes:

- requested and final sanitized URLs;
- attempt number and wait duration;
- navigation, stability, extraction, HTTP fallback, and search fallback outcomes;
- normalized error code and bounded reason;
- snapshot length/hash diagnostics without full snapshot content;
- extraction method, validation state, missing fields, and duration;
- linked Tool call, Gate, approval, and audit identifiers.

Reports aggregate failure codes so a run can distinguish poor SerpAPI candidates from
browser instability or extractor defects.

## Testing

Unit tests prove:

- raw MCP timeout, 403, selector, context-close, and unknown errors map correctly;
- the initial attempt waits 15 seconds and a retry waits 30 seconds;
- snapshots must be stable and source-matched before extraction;
- an empty Workday-style first snapshot retries and then falls back;
- iCIMS-style content is classified as extraction failure rather than browser failure;
- an Agently-style collection produces multiple source-backed JDs;
- HTTP JSON-LD/HTML and search structured/snippet provenance is preserved;
- partial successes survive failures from other candidates.

Integration tests prove ordered calls and Gate coverage across
`navigate -> wait -> snapshot -> stability snapshot -> retry -> HTTP -> search`, as
applicable. API tests prove mixed success output and per-URL failure reasons.

The real smoke reruns the three observed URLs and a SerpAPI-discovered set. Passing the
smoke requires at least one verified JD, correct classification for every attempted
URL, no `mcp_tool_error` in user-visible output, and evidence that each Playwright
attempt observed its configured minimum wait.

## Acceptance Criteria

- No user-visible candidate outcome contains only `mcp_tool_error`.
- Every Playwright attempt waits at least its configured 15- or 30-second budget
  before extraction.
- Every URL is retried no more than once.
- Playwright failure triggers HTTP extraction and then search evidence when available.
- Mixed batches return successful JDs and independently labeled failures.
- The HERE and Agently pages are not reported as uncrawlable.
- The Workday page either succeeds after stable waiting or returns its search-backed
  partial/full JD with explicit provenance.
- SerpAPI candidate reports separate provider quality, browser failure, extraction
  failure, and fallback outcome.
- Gate, network, privacy, audit, and redaction regressions remain green.
