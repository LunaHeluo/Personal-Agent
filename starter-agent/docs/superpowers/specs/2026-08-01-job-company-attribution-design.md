# Job Company Attribution Design

**Date:** 2026-08-01

**Status:** Approved direction, pending written-spec review

## Goal

Reduce misleading `未知公司` results without inventing employers. Preserve and
enrich company attribution from trustworthy evidence, rank attributable jobs
ahead of otherwise equivalent unattributed results, and label unresolved
partial evidence as unverified rather than as a known unknown company.

## Approaches Considered

1. **Drop every result without a company.** Simple, but it would discard useful
   direct-employer pages and complete JDs whose employer is omitted from the
   visible page.
2. **Treat the SerpAPI organic `source` or hostname as the employer.** High
   recall, but frequently wrong because the value may identify Liepin,
   LinkedIn, a staffing agency, or another publisher rather than the employer.
3. **Evidence-tiered attribution (selected).** Preserve structured company
   fields, recover only explicit high-confidence names from organic evidence,
   carry provenance and confidence through ranking and fallback, and leave the
   field unresolved when evidence is ambiguous.

## Attribution Rules

Company evidence is selected in descending order:

1. page JSON-LD `hiringOrganization.name`;
2. Google Jobs `company_name`;
3. explicit page employer/company fields and supported page-title patterns;
4. conservative organic title or snippet patterns that explicitly connect one
   organization to one job, such as `职位名_公司招聘` or `公司招聘职位名`;
5. a small verified employer-careers domain mapping, only if such a mapping
   already exists or is introduced with explicit test fixtures.

The organic `source`, recruitment-platform hostname, staffing-site hostname,
and arbitrary domain label are not employer evidence. Ambiguous text remains
unattributed. Existing page extraction remains authoritative over search-level
inference.

Each populated company carries internal attribution metadata:

- `company_source`: `page_json_ld`, `google_jobs`, `page_html`,
  `organic_explicit`, or `verified_domain`;
- `company_confidence`: `high` or `medium`.

Search-level metadata may be replaced by stronger page evidence. It must not
replace an already populated, stronger value.

## Search Adapter and Candidate Flow

The organic SerpAPI parser retains the current title, link, and snippet while
running a focused company-attribution helper. The helper returns an empty
company for platform-branded, generic, or ambiguous patterns.

Candidate merging preserves the strongest non-empty attribution when the same
canonical URL appears across query variants or both search engines. Google Jobs
metadata wins over organic inference for the same URL.

Ranking continues to prioritize location, role relevance, concrete detail
pages, and usable JD evidence. Within otherwise comparable candidates:

- a high-confidence company receives a stronger positive signal;
- a medium-confidence company receives a smaller positive signal;
- an unattributed organic or snippet-only result receives a modest penalty;
- missing company never overrides a substantially better complete-JD signal.

This avoids filtering useful results while making company-attributed, readable
JDs more likely to consume the limited candidate budget first.

## Retrieval and Fallback

Browser and HTTP extraction retain their current order and safety controls.
When page extraction succeeds, its explicit company replaces search-level
metadata. When extraction returns no company, the fallback payload retains a
trusted candidate company and its provenance. Search snippets do not synthesize
a new company during fallback.

A complete, source-backed JD may still be valid when the employer is genuinely
undisclosed. This preserves the existing `company_not_disclosed` validation
contract. The UI distinguishes that case from a verified employer.

## Output

- Verified or confidently attributed company names render normally.
- Unattributed complete JDs render `公司未披露`.
- Unattributed partial/search-snippet evidence renders `公司未核实`.
- The existing generic `未知公司` label is not used for these job-search rows.
- Internal confidence and provenance remain available in structured result and
  diagnostic data but do not clutter the default answer.

## Testing

TDD coverage must prove:

- Google Jobs preserves `company_name` with high-confidence provenance;
- an explicit Chinese organic title such as
  `AI Agent开发工程师招聘_示例科技招聘` recovers `示例科技`;
- a platform title or ambiguous organic result remains unattributed;
- URL merging preserves Google Jobs attribution over organic inference;
- an attributable employer detail ranks before an otherwise equivalent
  unattributed result;
- snippet fallback preserves trusted candidate attribution;
- complete and partial output use `公司未披露` and `公司未核实` respectively;
- existing company extraction, location ranking, deduplication, network safety,
  and mixed-success behavior do not regress.

Focused unit tests run first, followed by the full test suite. No live network
request is required for the regression suite.

## Acceptance Criteria

- Organic results no longer receive an unconditional empty company when their
  title contains an explicit, unambiguous employer relationship.
- Recruitment platforms and publishers are never presented as employers solely
  from SerpAPI `source` or URL hostname.
- Known-company candidates rank ahead of equivalent unknown-company candidates
  without suppressing higher-quality complete JDs.
- Search-snippet degradation retains valid attribution and clearly labels
  unresolved company identity.
- Existing public-job search, extraction, retry, safety, audit, and selection
  behavior remains intact.
