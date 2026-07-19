# Task 3 Security Specification Review

## Verdict

**PASS**

Re-reviewed the latest Task 3 range `daee4e7..e3ac05a`, including fix commit
`e3ac05a`, against Task 3 in
`docs/superpowers/search-job-description.md`, and the security/error contract
in `docs/superpowers/search_job_description_design.md`.

The main SSRF design is materially stronger than a hostname-only check: it
validates every DNS answer, pins the request to one validated address, disables
environment proxies in production, preserves the canonical hostname in
`Host`/SNI, revalidates every redirect hop, and rechecks robots before each
page request. The two previously blocking findings are closed.

## Closed findings

### CLOSED P1 — Invalid compressed responses now preserve the failure contract

**Evidence**

- `safe_web_fetcher.py:290-296` catches `httpx.DecodingError` before the
  transport-error branch and raises
  `FetchFailure("fetch_failed", ..., retryable=True)`.
- The timeout branch remains distinct and continues to return
  retryable `fetch_timeout`; transport failures remain retryable
  `fetch_failed`.
- `tests/unit/test_safe_web_fetcher.py:497-523` uses a streaming invalid gzip
  payload and verifies both the stable code and retryable flag.
- The exact offline reproduction that previously emitted raw
  `DecodingError` is now covered by the focused suite.

This satisfies the design's retryable parsing-failure contract
(`docs/superpowers/search_job_description_design.md:290`).

### CLOSED P1 — Cloud platform endpoint deny policy covers every URL path

**Evidence**

- `safe_web_fetcher.py:42-62` defines exact metadata host and platform-address
  sets for the known endpoints in scope: shared AWS/GCP/Azure/Oracle IMDS
  `169.254.169.254`, Azure WireServer `168.63.129.16`, Alibaba
  `100.100.100.200`, and AWS IPv6 IMDS `fd00:ec2::254`.
- The deny set is exact-address based; it does not over-block adjacent public
  ranges.
- `_is_public_address` applies the explicit deny set before generic public
  address checks (`safe_web_fetcher.py:447-458`).
- Both literal URLs and every DNS answer converge on the same address tuple
  and common predicate in `_validate_url`
  (`safe_web_fetcher.py:348-368`).
- Every redirect target re-enters `_validate_url`
  (`safe_web_fetcher.py:253-266`), and connected-peer validation also reuses
  `_is_public_address` (`safe_web_fetcher.py:579-582`).
- Offline coverage verifies direct literals
  (`tests/unit/test_safe_web_fetcher.py:41-89`), a DNS answer
  (`tests/unit/test_safe_web_fetcher.py:157-180`), and a redirect target
  (`tests/unit/test_safe_web_fetcher.py:219-246`) are rejected before an
  unsafe request.

The deny policy therefore covers literal, DNS, redirect, and exposed-peer
paths uniformly and satisfies the cloud-endpoint requirement
(`docs/superpowers/search_job_description_design.md:210,385`).

## Specification matrix

| Area | Result | Evidence |
|---|---|---|
| Schemes, userinfo, fragments, standard ports | PASS | `safe_web_fetcher.py:309-345`; unsafe inputs are rejected before requests. |
| IPv4/IPv6, IDNA, numeric/confused addresses | PASS | `safe_web_fetcher.py:347-466`; canonicalization and mixed/alternate numeric forms fail closed. |
| All DNS answers and rebinding defense | PASS | `safe_web_fetcher.py:353-387,472-496,559-586`; every answer must be public, requests use a validated IP literal, and exposed peers are checked. |
| Cloud metadata/platform endpoints | PASS | Exact deny set at lines 42-62 is used by the common literal/DNS/redirect/peer predicate. |
| Redirect validation and budget | PASS | `safe_web_fetcher.py:247-267`; redirects are manual, bounded, URL-joined, and fully revalidated before the next request. |
| Robots | PASS | `safe_web_fetcher.py:248,498-557`; checked on every hop; production retrieval is validated, pinned, timed, type/size bounded, parsed with `RobotFileParser`, and fails closed. |
| Content type and response budget | PASS | `safe_web_fetcher.py:588-623`; exact HTML/plain types, declared-length early rejection, and streamed/decompressed byte budget. |
| Status/timeout/error mapping | PASS | `safe_web_fetcher.py:284-302,632-660`; timeout, decoding, transport, and required status mappings preserve stable codes/retryability. |
| URL sanitization | PASS | `safe_web_fetcher.py:31-41,130-158,243-280`; fragment and required sensitive keys are removed from returned source/final URLs; failures do not reflect raw URLs. |
| `FetchedPage` / `FetchFailure` public contract | PASS | Fields and retryable metadata match Task 3 at `safe_web_fetcher.py:75-96`; decoding now stays inside the contract. |
| `from_config` | PASS | `safe_web_fetcher.py:217-238` creates one owned client, disables redirects/environment proxies, injects the production resolver/default robots checker, and copies every config value exactly. Lifecycle closure is exposed through `aclose()` at lines 239-241. |
| Tests are offline | PASS | Focused suite uses `httpx.MockTransport` and injected resolver/robots callbacks; `52 passed`. No real DNS/TLS/network was used in this review. |

## Verification performed

- `python -m pytest tests/unit/test_safe_web_fetcher.py -p no:cacheprovider -q`
  → **52 passed**.
- `python -m compileall -q src tests` → exit `0`.
- `git diff --check daee4e7..e3ac05a` → exit `0`.
- Static re-review of the changed implementation and tests, plus read-only
  offline classification probes for direct, mapped, and translated forms of
  the listed endpoint addresses.

No blocking or non-blocking Task 3 specification findings remain in the
reviewed range. Task 3 is spec-clean.
