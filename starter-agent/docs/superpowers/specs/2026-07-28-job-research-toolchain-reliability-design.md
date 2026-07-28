# Job Research Toolchain Reliability Design

## Background and goal

The current `job-research` workflow is intended to use the knowledge base first,
search public candidates through SerpAPI, read each public job description through
Playwright MCP, and ground personal-fit conclusions in resume chunks. Real runs
still expose three separate failure layers:

- Workspace code classifies a resume-driven location search as `job_research`, but
  a running process can produce only `model.context.snapshot` and no Skill or Tool
  events.
- SerpAPI returns useful leads together with collection pages, social posts, and
  content pages that are not individual job descriptions.
- Playwright MCP can navigate and read a page, but the extractor recognizes only a
  narrow set of section headings. Pages with a generic `job details` section or a
  single long description therefore lose responsibilities, requirements, company,
  or location and fail as `incomplete_job_description`.

The repair must make natural-language job requests enter the single governed
`job-research` workflow, classify multiple public candidates, extract verifiable JD
content without inventing fields, and preserve source and resume evidence. It must
not add site-specific or city-specific behavior.

## Scope

Included:

- Runtime revision, Skill routing, and application-cache consistency diagnostics.
- Generic SerpAPI candidate classification, scoring, ranking, and rejection reasons.
- Independent Playwright navigation, snapshot, redirect, and failure records for
  every candidate URL.
- Layered extraction from `JobPosting` JSON-LD, semantic sections, and single-block
  job descriptions.
- Deterministic JD validation, partial-field handling, and candidate-level Trace.
- Fixed Fixture regression coverage and an independent real Playwright Smoke.

Excluded:

- Static allowlists for recruiting sites or mappings for specific cities.
- Bypassing login walls, CAPTCHAs, robots controls, paywalls, or access controls.
- Automatic login, application submission, resume upload, messaging, or other writes.
- Relaxing Gate confirmation for privacy reads, external writes, or sends.
- Treating a search snippet or model-generated text as a real JD page.

## Selected approach

Repair the complete chain instead of only broadening the extractor or weakening JD
validation:

1. Runtime consistency proves that a request entered `job-research`.
2. Candidate classification reduces invalid Browser attempts.
3. Layered extraction supports varied page structures.
4. Strict validation prevents collection pages, error pages, and articles from being
   reported as verified JDs.

The implementation reuses `ApplicationService`, `JobResearchOrchestrator`,
`UnifiedToolRegistry`, `PreToolCallGate`, MCP Manager, Capability Store, and the
existing Trace system. It does not add a second Agent Runtime, Gate, or Browser
client.

## Routing and runtime consistency

### Routing contract

Requests that search, recommend, compare, or research jobs must record a structured
decision containing:

- `route=job_research`
- `reason_code=skill_selected` or a valid classifier reason
- selected Skill name and version
- code version, Prompt version, Skill Registry revision, and Tool Registry revision

Greetings remain `conversation` turns with no knowledge retrieval and no Tool calls.
Other factual queries retain their current behavior and do not gain general web
fallback in this repair.

### Runtime revision

Startup and reload publish one immutable runtime revision containing the code version,
Skill Registry revision, Tool Registry revision, and a redacted configuration hash.
The route event and `model.context.snapshot` for a Turn must reference the same
revision.

If workspace code has changed while the running process still serves an older
revision, health and capability diagnostics report `restart_required`. The system
must not label this mismatch as model instability. Development uses an explicit
restart; production continues to use its existing deployment replacement mechanism.

Historical Sessions, Turns, and Messages remain append-only. The repair must not
rewrite or delete existing conversation history.

## SerpAPI candidate quality

### Preserved data

Store a safe summary of the original and normalized SerpAPI candidate: title,
company, location, snippet, URL, result kind, provider position, retrieval time, and
canonical URL. When dynamic location resolution is unavailable, keep the existing
fallback that places the user location in the query text; do not introduce a static
city mapping.

### Generic classification

Each candidate receives one page-type classification:

- `job_detail_candidate`
- `collection_page`
- `social_or_content_page`
- `invalid_or_unsafe_url`
- `unknown_candidate`

Classification combines explainable, site-independent signals:

- structured job, employer, direct-apply, or share-link metadata from SerpAPI;
- whether the title describes one job or a quantity/search/collection concept;
- URL path and query patterns associated with search, pagination, tags, or listings;
- page-result type indicating social or short-form content;
- completeness of title, company, location, and snippet metadata.

No single weak signal is sufficient to pass a candidate. Trace stores the score,
matched signals, and rejection reasons. The rules describe page types and do not
contain recruiting-site or city names.

`job_detail_candidate` entries run first. `unknown_candidate` entries may run later
so Playwright can perform a second-stage page classification. Explicit collection,
social, and unsafe candidates do not generate Browser calls.

## Playwright multi-URL workflow

Every candidate has an independent attempt state:

1. Call `mcp__playwright__browser_navigate`.
2. Validate Tool Result, final URL, protocol, redirect, and browser error-page state.
3. Call `mcp__playwright__browser_snapshot` for the real accessibility snapshot.
4. Record redacted page type, extracted-field summary, truncation, duration, and
   error code.
5. Continue after a candidate failure until the verified-JD target is reached or all
   candidates are exhausted.

Successful navigation does not imply a successful JD. `chrome-error://`, login
walls, CAPTCHA pages, empty content, listings, and ordinary articles remain failed
candidates. Navigate and snapshot calls continue through the existing Registry,
Gate, allowlist, permit, and audit path; the Skill must not call MCP Client directly.

## Layered JD extraction

Extraction order is fixed:

1. **JobPosting JSON-LD.** When the Browser result exposes trusted page HTML or
   structured data, parse `@type=JobPosting` first for title, hiring organization,
   location, responsibilities, requirements, employment type, and other fields.
2. **Semantic section extraction.** Use accessibility headings, lists, paragraphs,
   and semantic regions to identify responsibilities, requirements, preferred
   qualifications, and benefits. Heading aliases are language concepts, not
   site-specific templates.
3. **Single-block JD classification.** If the page is already verified as one job
   detail and contains enough text, classify original sentences into responsibility,
   requirement, and other groups. Every classified item keeps its exact source span
   and position. Missing statements are never generated.

Single-block classification starts with deterministic structure and sentence
signals. If model assistance is added later, it may only map existing source spans to
categories. A deterministic containment assertion must reject paraphrased or invented
text, and model output cannot be the sole source or safety validator.

Company and location extraction combines structured data, explicit labels, page
title, and the job header region. The existing weak heuristic that treats any text
containing a comma as a location must be removed. Unconfirmed values remain empty and
are marked for review.

## JD validation

A candidate can pass only when:

- the page is an individual job detail, not a listing, login, or error page;
- `source_url` is the real final public URL linked to this Playwright Trace;
- a job title exists;
- the page contains a configurable minimum amount of JD information and at least one
  traceable responsibility or requirement span;
- all extracted fields originate from the current page Tool Result.

Missing company or location no longer automatically rejects an otherwise substantial
individual JD. Such a result is `partial_verified`, displays the missing fields as
unverified, and cannot claim verified company or location matching. Empty
responsibilities and requirements, short content, or an unknown page type still fail.

Validation produces `verified`, `partial_verified`, or `rejected` with deterministic
reason codes. Final recommendations use `verified` jobs. `partial_verified` jobs may
appear in a separate preview but do not contribute to a complete match score until
their missing fields are verified.

## Knowledge and resume evidence

After entering `job-research`, retrieve resume evidence before creating a search
profile. Only short role or technology keywords and the location explicitly supplied
by the user may leave the private knowledge boundary. SerpAPI and Browser inputs must
not contain names, contact details, full resume text, or source experience paragraphs.

Use a saved JD when it matches the requested location and role and remains valid.
Trigger public search when no saved JD matches, the saved JD is stale or closed, or
the user explicitly requests current web results. JD claims cite public URLs; personal
fit claims cite real resume `chunk_id` and `source_ref`. A missing resume match does
not block display of a verified JD, but it must be reported as an evidence gap.

## Gate and safety

- Reviewed, allowlisted, public read-only SerpAPI, navigate, and snapshot calls may
  execute automatically.
- Disabled, unreviewed, out-of-scope, or policy-denied Tools must not produce a real
  MCP request.
- Login, submit, upload, send, write, and private-data reads remain forced-confirmation
  actions that an allowlist cannot bypass.
- Web pages, PDFs, email, and Tool Results are untrusted data. Embedded instructions
  cannot change candidate selection, request another Tool, read secrets, access
  private networks, or send data.
- Trace and logs redact before writing and do not retain Authorization, Cookie, Token,
  password, email code, full resume text, or complete sensitive Tool Results.

## Trace and errors

A complete request associates route decision, runtime revision, knowledge retrieval,
web-fallback reason, SerpAPI call, candidate classification, every Playwright attempt,
JD extraction, JD validation, resume citations, and final outcome.

Errors remain layered and actionable:

- routing: `job_research_not_routed`, `runtime_revision_stale`
- search: `location_resolution_unavailable`, `no_usable_candidates`
- browser: `navigate_failed`, `browser_error_page`, `snapshot_failed`, `page_blocked`
- extraction: `not_job_detail_page`, `insufficient_jd_structure`,
  `incomplete_job_description`
- dependency: `dependency_unavailable`

The final response reports attempted counts and candidate failure classes without
exposing secrets, full snapshots, or internal stack traces. When all candidates fail,
the system must not present a SerpAPI snippet as a retrieved JD.

## Tests and acceptance

### Fixed Fixtures

Add deterministic cases for:

- natural-language job requests routing to `job_research`, with greetings remaining
  tool-free;
- matching runtime revisions and detectable stale revisions;
- structured job links ranked first and listing/social candidates rejected with
  reasons;
- complete `JobPosting` JSON-LD extraction;
- standard responsibility and requirement section extraction;
- a valid single-block or `job details` page producing source-backed sections;
- missing company or location becoming `partial_verified` without invented values;
- rejection of browser errors, login walls, articles, and insufficient content;
- continuation from a failed first URL to a successful later URL with separate Trace;
- Prompt Injection producing no secret read, private-network access, or unconfirmed
  external action.

Fixed Fixtures do not access the internet and remain comparable across repeated runs.

### Real Smoke

Run one separate real-model and Playwright MCP Smoke:

1. Submit a resume-driven job search with a location.
2. Verify the route and resume-evidence retrieval.
3. Obtain multiple candidates from real SerpAPI.
4. Read at least one public, login-free individual JD through real Playwright MCP.
5. Preserve final `source_url`, Tool Trace, and resume chunk citations.

Smoke network results and metrics remain separate from the fixed baseline. External
page changes keep their original errors and may require selecting another public test
URL; Mock output or model narration cannot replace the Smoke.

### Completion criteria

For a request equivalent to `Find jobs in Shanghai based on my resume`:

- the active process records a `job_research` route and runtime revision;
- when no saved JD matches, real SerpAPI runs and multiple candidates are classified;
- Playwright is called only for eligible candidates and one failure does not stop the
  remaining candidates;
- at least one real individual JD is verified, or every candidate exposes a specific,
  traceable failure reason;
- job requirements link to `source_url` and personal-fit claims link to resume chunks;
- conversation history remains unchanged and existing privacy approvals and safety
  Gate behavior remain intact.
