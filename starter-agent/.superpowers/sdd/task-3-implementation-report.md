# Task 3 Implementation Report: Safe Public Web Fetcher

## Status

<<<<<<< HEAD
Implemented `SafeWebFetcher`, `FetchedPage`, `FetchFailure`,
`default_resolver`, and `sanitize_public_url` with deterministic offline
tests.

## Threat model and controls

- SSRF through alternate schemes, userinfo, fragments, non-standard ports,
  local names, cloud metadata names, private/loopback/link-local/multicast/
  reserved/unspecified addresses: rejected before any page request.
- Parser disagreement through decimal, shortened, octal, or hexadecimal IPv4,
  malformed IPv6, invalid DNS labels, Unicode/IDNA, and backslash/percent host
  confusion: canonicalized once or rejected closed.
- DNS answers containing a mixture of public and non-public addresses: every
  answer is checked and the whole URL is rejected when any answer is unsafe.
- DNS rebinding/TOCTOU: the HTTP request connects to a validated IP literal
  rather than resolving the hostname again. The original canonical hostname is
  retained in `Host` and HTTPS SNI. Production disables environment proxies,
  asks for connection close, and fails closed unless the transport exposes the
  exact connected peer.
- Redirect SSRF: redirects are manual, bounded to three, URL-joined against the
  canonical public URL, fully re-resolved/revalidated/re-pinned, and checked
  against robots again before the next request.
- Robots bypass: an injected checker supports deterministic tests; the
  production checker reads `/robots.txt` through the same validated,
  IP-pinned, timed, bounded transport, follows only bounded fully validated
  redirects, and fails closed when policy cannot be safely determined.
- Response abuse: only `text/html` and `text/plain` are accepted; declared and
  raw streamed body sizes are bounded; compression is not accepted; valid
  HTTP/BOM/meta charsets are decoded in a fixed order; timeout/transport/status
  failures map to stable error codes.
- Secret reflection: source/final URLs remove credential-like query keys and
  fragments before being returned.

## TDD evidence

- RED:
  `python -m pytest tests/unit/test_safe_web_fetcher.py ...`
  failed during collection with
  `ModuleNotFoundError: starter_agent.tools.adapters.safe_web_fetcher`.
- GREEN:
  focused fetcher tests: **91 passed** after the security-quality fixes.
- Related regression:
  fetcher + extractor + registration settings: **128 passed**.
- `python -m compileall -q src tests`: exit 0.
- `git diff --check` for Task 3 files: exit 0.

All fetch tests use `httpx.MockTransport` plus injected DNS and robots
functions. No test reaches real DNS, TLS, or the public internet.

## Remaining risks and deliberate trade-offs

- HTTPS pinning uses httpx's request-extension path for the original SNI. The
  offline suite proves the pinned request URL, `Host`, and peer checks, but does
  not perform a real certificate handshake. A controlled TLS fixture is
  appropriate for later acceptance testing.
- Only the first validated public DNS address is attempted. This avoids a
  second resolution and keeps the trust decision deterministic, at the cost of
  not failing over to another already validated address.
- Robots retrieval fails closed and is not cached. Redirect chains can perform
  repeated bounded `/robots.txt` reads, favoring policy correctness over
  availability and request minimization.
- `from_config` owns a long-lived `AsyncClient`; the later registry/runtime
  integration should call `aclose()` during application shutdown if lifecycle
  cleanup is added.

## Commit

`322cc77 feat: add safe job page fetcher`

Specification-review follow-up:
`e3ac05a fix: close safe fetcher gaps`.

Quality-review follow-up: pending commit.

## Specification-review fixes

- Added an offline malformed-gzip stream regression that first reproduced a
  raw `httpx.DecodingError`. Response decoding failures now map to retryable
  `FetchFailure("fetch_failed")`.
- Added an explicit, exact cloud platform endpoint deny policy for the shared
  AWS/GCP/Azure/Oracle metadata address, Azure platform virtual IP, Alibaba
  metadata address, and AWS IPv6 IMDS address. The policy is applied equally
  to literal URLs, every DNS answer, redirect targets, and peer validation.
- Added direct, DNS-answer, and redirect regressions for
  `168.63.129.16`; all remain offline.

## Quality-review fixes

- RED evidence: the expanded suite initially reported **29 failed, 54 passed**,
  including a resolver that outlived the configured timeout.
- Added explicit IPv6 site-local rejection to the common literal/DNS/redirect/
  peer predicate.
- Enforced `Accept-Encoding: identity`, rejected any non-identity response
  encoding before body iteration, and switched bounded reads to raw bytes.
- Added one end-to-end `asyncio.timeout` deadline covering DNS, injected or
  production robots checks, every redirect, transport operations, and body
  streaming; cancellation closes the active response stream.
- Rejected DEL, Unicode control characters, and surrogate code points before
  URL construction, with defensive HTTPX invalid-URL error mapping.
- Normalized and filtered generic, OAuth, AWS, and Azure credential query-key
  families, repeated/encoded keys, and semicolon-separated fields.
- Added safe bounded robots redirects and an offline robots status matrix.
- Implemented byte-aware HTTP charset, BOM, early HTML meta charset, then
  UTF-8 fallback decoding, including a GBK fixture.
- Removed private `ipaddress` types; asserted Host/SNI, identity encoding,
  `trust_env=False`, owned-client closure, early response closure, and
  production peer-metadata fail-closed behavior.
=======
Quality-review hardening and the final sanitizer review are implemented in the
working tree. Per coordinator instruction, the changes are not committed.

## Security controls

- URL policy rejects unsafe schemes, userinfo, fragments, non-standard ports,
  local/metadata hosts, confused numeric IP forms, cloud platform endpoints,
  IPv6 site-local, and every other non-public literal or DNS answer.
- Every page and robots redirect is resolved, validated, IP-pinned, and peer
  checked again. Production requires peer metadata and disables environment
  proxies; injected mock clients opt out explicitly.
- One end-to-end deadline covers DNS, robots, redirects, requests, and body
  streaming. Cancellation closes the active response stream.
- Requests require identity encoding and raw response bytes are bounded before
  accumulation. Non-identity encodings are rejected without decompression.
- Decoding order is valid HTTP text charset, BOM, early HTML meta charset, then
  UTF-8 replacement. Non-text codecs such as `base64_codec`, `hex_codec`, and
  `rot_13` are rejected by a real `bytes.decode` capability check, and codec
  lookup/type/value errors fall back without escaping the stable contract.
- Returned URLs remove normalized generic, OAuth, AWS, Google, and Azure
  credential/signed-URL key families, including repeated, encoded, and
  semicolon-separated fields. Benign keys such as `keyword` and `monkey`
  remain.

## TDD evidence

- Final sanitizer RED: **7 failed, 14 passed** in the targeted set. Failures
  reproduced `x-api-key`, `access_key`, `subscription-key`,
  `AWSAccessKeyId`, `X-Goog-Api-Key`, `X-Goog-Algorithm`, and
  `GoogleAccessId` leakage.
- Final sanitizer GREEN: **21 passed** in the targeted set.
- Full safe-fetcher suite: **102 passed**.
- Fetcher + extractor + registration related regression: **139 passed**.
- `python -m compileall -q src tests`: exit 0.
- `git diff --check` for Task 3 source/tests: exit 0.
- All fetch tests use injected DNS/robots and `httpx.MockTransport`; no real
  DNS, TLS, or internet access occurs.

## Working-tree files

- `src/starter_agent/tools/adapters/safe_web_fetcher.py`
- `tests/unit/test_safe_web_fetcher.py`

No commit was created.
>>>>>>> codex/search-job-description
