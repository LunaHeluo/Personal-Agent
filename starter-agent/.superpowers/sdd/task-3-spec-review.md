<<<<<<< HEAD
# Task 3 Security Specification Review
=======
# Task 3 Final Specification Re-review
>>>>>>> codex/search-job-description

## Verdict

**PASS**

<<<<<<< HEAD
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
=======
Reviewed `HEAD e3ac05a` plus the current uncommitted Task 3 changes in:

- `src/starter_agent/tools/adapters/safe_web_fetcher.py`
- `tests/unit/test_safe_web_fetcher.py`

The review compared the working tree with Task 3 in
`docs/superpowers/search-job-description.md` and the contracts in
`docs/superpowers/search_job_description_design.md`. No implementation was
changed or committed by this review.

## Public contract compatibility

| Contract | Result | Evidence |
|---|---|---|
| `FetchedPage` | PASS | The frozen dataclass and its six fields remain unchanged at `safe_web_fetcher.py:134-141`. |
| `FetchFailure` | PASS | Constructor signature and public `code`, `display`, and `retryable` attributes remain unchanged at lines 144-155. |
| `SafeWebFetcher.fetch(url)` | PASS | Public signature and `FetchedPage` success result are unchanged at lines 356-361 and 439-446. |
| `SafeWebFetcher.from_config(config)` | PASS | Public signature is unchanged; all five configuration values are still copied exactly at lines 329-350. The owned client still disables automatic redirects and environment proxies. |
| Resolver/robots injection | PASS | Resolver behavior is unchanged; its annotation is narrowed from the private `ipaddress._BaseAddress` to the equivalent public IPv4/IPv6 union. The injected robots checker keeps the same two-argument callable contract. |
| Constructor compatibility | PASS | Existing parameters remain keyword-only and unchanged. The new `require_peer_metadata=False` option is additive; injected test transports retain the previous default behavior, while owned production transports fail closed. |

## Error-code compatibility

No Task 3 error code was removed or renamed.

| Condition | Required result | Working-tree result |
|---|---|---|
| Unsafe initial/redirect URL | `unsafe_url`, not retryable | Preserved; defensive HTTPX encoding failures now map to the same code. |
| Robots denial/unverifiable policy | `robots_blocked`, not retryable | Preserved; safe robots redirects remain fail-closed. |
| 401 | `authentication_required`, not retryable | Preserved at `safe_web_fetcher.py:899-903`. |
| 403 | `access_blocked`, not retryable | Preserved at lines 904-908. |
| 404/410 | `job_not_found`, not retryable | Preserved at lines 909-913. |
| 429 | `rate_limited`, retryable | Preserved at lines 914-919. |
| Whole-call or HTTP timeout | `fetch_timeout`, retryable | Preserved and broadened to DNS, robots, redirects, and streaming at lines 356-375. |
| Unsupported media type | `unsupported_content_type`, not retryable | Preserved at lines 799-809. |
| Oversized declared/streamed body | `response_too_large`, not retryable | Preserved at lines 824-847. |
| Transport/decoding/temporary stream failure | `fetch_failed`, retryable | Preserved at lines 378-395. |
| Non-identity response encoding after requesting identity | stable `fetch_failed` | Fails before decompression at lines 811-822. This is a permanent policy rejection, not an escaped `httpx.DecodingError`; actual decoding exceptions retain retryable `fetch_failed`. |

The redirect-count and missing-location paths continue to use the planned
`fetch_failed` code with their existing non-retryable default.

## Task 3 requirement matrix

| Area | Result | Evidence |
|---|---|---|
| HTTP/HTTPS only, no userinfo/fragment/non-standard port | PASS | `safe_web_fetcher.py:461-497`; control/surrogate URL input now also fails closed. |
| IPv4/IPv6/IDNA/confused-address handling | PASS | Lines 499-615 retain canonicalization, all-answer checks, explicit cloud endpoints, and add IPv6 site-local rejection. |
| DNS rebinding and peer verification | PASS | Requests remain pinned to a validated IP with canonical Host/SNI at lines 631-657; production now requires exact peer metadata at lines 762-797. |
| Every page redirect revalidated | PASS | Lines 405-427 retain bounded manual redirects and call `_validate_url` for each target. |
| Robots respected | PASS | Lines 659-760 retain fail-closed policy evaluation and now apply the same redirect, deadline, URL, peer, encoding, type, and size controls to robots retrieval. |
| Single-page timeout budget | PASS | One deadline now covers the complete fetch, including DNS, robots, redirects and body streaming, rather than resetting per hop. |
| HTML/plain only and 1 MB budget | PASS | Media-type checks are unchanged; identity encoding plus `aiter_raw()` ensures the configured byte budget is enforced before accumulation/decompression. |
| URL sanitization | PASS | The public helper signature is unchanged; required token/key/signature/auth fields are still removed, with additional normalized cloud/OAuth credential families and bounded parsing. |
| Traceability | PASS | Source URL, final URL, status, content type, decoded text, and SHA-256 fields remain unchanged. |
| Offline deterministic tests | PASS | All fetch tests continue to use injected resolver/robots functions and `httpx.MockTransport`; no real DNS, TLS, proxy, or public network was used. |

The quality hardening is conservative: it tightens validation, bounds the
whole operation, makes production peer verification fail closed, improves
text decoding, and extends secret removal. It does not expand the tool beyond
one public HTTP(S) page, bypass access controls, execute JavaScript, or change
the Task 3 public result/error schema.

## Verification

- Focused safe-fetcher suite: **102 passed** (exit `0`).
- Safe-fetcher + extractor + registration suite: **139 passed** (exit `0`).
- `python -m compileall -q` for the two Task 3 files: exit `0`.
- `git diff --check -- <Task 3 files>`: exit `0` (Git emitted only the
  repository's existing LF-to-CRLF checkout warning).
- Static comparison of public signatures, dataclass fields, error-code
  inventory, retryability, and every changed fetch/robots/redirect path.

No Task 3 specification finding remains.
>>>>>>> codex/search-job-description
