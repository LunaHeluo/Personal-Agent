<<<<<<< HEAD
# Task 3 Code Quality and Security Review

## Verdict

**CHANGES_REQUIRED**

Reviewed `daee4e7..e3ac05a` without modifying the implementation. The focused
suite is green, and the hostname/IP pinning, Host/SNI handling, manual page
redirects, `trust_env=False`, peer comparison, stream context management,
status mapping, robots size/type checks, and decoded-byte counting are
generally sound. Two reproducible P1 security/resource-boundary defects remain.

## P0 findings

None.

## P1 findings

### P1 — Deprecated IPv6 site-local addresses pass the SSRF allow policy

**Location:** `src/starter_agent/tools/adapters/safe_web_fetcher.py:447-457`

`_is_public_address()` checks `is_global`, `is_private`, loopback, link-local,
multicast, reserved, and unspecified, but not IPv6 `is_site_local`. In the
supported Python runtime, an address in the deprecated site-local block is
classified as:

```text
fec0::1  is_global=True  is_private=False  is_reserved=False
```

Therefore both a literal URL such as `https://[fec0::1]/job` and a DNS answer
in `fec0::/10` are accepted, pinned, and requested. The offline `MockTransport`
probe completed successfully for that literal. A routed site-local address is
an internal destination, so this is an SSRF gap even though the address class
is deprecated.

**Required correction:** explicitly reject IPv6 `is_site_local` (and add
literal plus DNS-answer regression cases). Keep the common predicate on the
literal, DNS, redirect, and connected-peer paths.

### P1 — Automatic content decoding allocates beyond the response budget

**Location:** `src/starter_agent/tools/adapters/safe_web_fetcher.py:473-497,
600-623`

The request allows HTTPX's automatic compression (`Accept-Encoding` was
observed as `gzip, deflate`), and `_read_limited()` iterates
`response.aiter_bytes()`. HTTPX decompresses each raw transport chunk before
the loop can inspect its length. The limit prevents appending an oversized
decoded chunk to `body`, but it cannot prevent that chunk from already being
allocated by the decoder.

An offline adversarial response demonstrated that a 4,892-byte gzip payload
was delivered by `aiter_bytes()` as one 5,000,000-byte decoded chunk before
the 1 MB check ran. Nested or higher-ratio content encodings can amplify the
peak further. Thus the advertised response budget is not a memory bound and a
public page can cause substantial worker memory pressure.

**Required correction:** for this small read-only tool, send
`Accept-Encoding: identity` and reject any non-identity `Content-Encoding`, or
implement a genuinely bounded incremental decoder that caps output before
allocation. Add a valid compressed expansion regression test; the current
invalid-gzip test does not cover this case.

## P2 findings

### P2 — `fetch_timeout_seconds` is not an end-to-end deadline

**Location:** `src/starter_agent/tools/adapters/safe_web_fetcher.py:161-179,
243-272, 490-496`

The configured timeout is passed only to each HTTPX stream request. DNS
resolution is outside it, robots and the page each receive a fresh timeout,
every redirect resets it again, and an HTTPX float timeout is a per-operation
timeout rather than a wall-clock deadline. An injected resolver that waits
forever caused `fetch(timeout=0.01)` to remain pending until an external
`asyncio.wait_for(..., 0.05)` cancelled it. A slow peer can likewise keep
streaming chunks just inside each read timeout.

**Correction:** enforce one monotonic/`asyncio.timeout` deadline around the
whole fetch (including DNS, robots, redirects, and body streaming), passing the
remaining budget to individual operations. Decide separately whether robots
may have a small bounded sub-budget.

### P2 — Malformed user URLs escape the stable `FetchFailure` contract

**Location:** `src/starter_agent/tools/adapters/safe_web_fetcher.py:309-340,
473-497`

The precheck rejects code points below 32 but not DEL (`\x7f`) or unpaired
Unicode surrogates, and the exception mapping covers transport errors only
after request construction. Offline probes produced raw exceptions:

```text
https://example.com/\x7f     -> httpx.InvalidURL
https://example.com/\ud800   -> UnicodeEncodeError
https://example.com/?q=\ud800 -> UnicodeEncodeError
```

These inputs can surface as an internal 500 instead of `unsafe_url`.

**Correction:** reject all non-printable URL code points and Unicode
surrogates before canonicalization, and defensively map `httpx.InvalidURL` /
URL encoding failures to `FetchFailure("unsafe_url", ...)`.

### P2 — URL sanitization leaks common credential-bearing query fields

**Location:** `src/starter_agent/tools/adapters/safe_web_fetcher.py:31-41,
130-158`

The exact-name deny set removes the plan's basic examples, but leaves common
secrets such as `client_secret`, `refresh_token`, `id_token`,
`x-amz-security-token`, and signed-URL credential fields. The exact offline
probe returned all of these unchanged:

```text
?client_secret=s3&x-amz-security-token=T&x-amz-credential=C&safe=1
```

Semicolon-style query material such as `?safe=1;token=secret` is also retained
inside the value (then percent-normalized), even though downstream systems may
interpret it as another field.

**Correction:** centralize a normalized sensitive-key predicate covering
secret/password/credential/token/signature/auth/API-key families and the
major signed-URL names. Add table-driven tests for OAuth, AWS/Azure-style
fields, duplicates, case/encoding variations, and alternate separators. Do
not log the unsanitized request URL through application or HTTPX request logs.

### P2 — The production robots fetcher does not handle safe redirects

**Location:** `src/starter_agent/tools/adapters/safe_web_fetcher.py:517-557`

The page redirect path is manual and validated, but `/robots.txt` treats every
3xx as an unavailable policy and fails closed. This is secure but rejects
otherwise valid sites whose robots policy moves from HTTP to HTTPS, a bare
domain to `www`, or to a public CDN. It also means the configured redirect
budget and per-hop URL checks are not consistently applied to the robots
resource.

**Correction:** follow a small bounded number of robots redirects with the
same URL validation, IP pinning, peer validation, timeout, type, and size
discipline. Continue to fail closed when a redirect target is unsafe or the
budget is exhausted.

### P2 — Charset fallback can irreversibly corrupt legacy job pages

**Location:** `src/starter_agent/tools/adapters/safe_web_fetcher.py:272-280,
625-630`

When the HTTP header omits a charset, `response.encoding` defaults to UTF-8.
The fetcher decodes with replacement and does not consider BOM or an HTML
`<meta charset>`. Because the extractor receives only the already-decoded
string, it cannot recover GBK/Big5 and similar pages; requirements and titles
may become unusable.

**Correction:** make decoding byte-aware: honor a valid HTTP charset, then BOM,
then a bounded early HTML meta-charset scan, and finally UTF-8 with replacement.
Add a non-UTF-8 HTML fixture rather than only the current ASCII body with an
unknown codec name.

## P3 findings

### P3 — Peer verification silently skips missing production metadata

**Location:** `src/starter_agent/tools/adapters/safe_web_fetcher.py:559-586`

Missing `network_stream` or a falsey `server_addr` returns success. That is
necessary for the current mock tests, and the default HTTPX/httpcore transport
does expose `server_addr`, so it is not a present production bypass while the
URL is pinned. However, the fail-open behavior is easy to preserve
accidentally if the transport changes.

**Correction:** make the mock exemption explicit through an injected peer
validator/test mode, while requiring peer metadata for the owned production
client. At minimum add a production-transport contract test and document the
dependency on HTTPX/httpcore's `network_stream` extension.

### P3 — Tests cover outcomes better than transport contracts

The 52 focused tests are fully offline and useful, but they do not assert the
HTTPS `sni_hostname` extension, owned-client `trust_env=False`, response-stream
closure on early failures, valid compressed expansion, DNS/whole-fetch
deadline behavior, robots redirects/status matrix, or the malformed URL cases
above. `ipaddress._BaseAddress` is also a private stdlib type used in source
and tests; use the local public IPv4/IPv6 union instead.

## Checks performed

- `python -m pytest tests/unit/test_safe_web_fetcher.py -p no:cacheprovider -q`
  → **52 passed**
- `python -m compileall -q src tests` → exit `0`
- `git diff --check daee4e7..e3ac05a` → exit `0`
- Offline adversarial probes only:
  - IPv6 site-local literal/DNS classification;
  - HTTPX URL construction with DEL and unpaired surrogates;
  - credential-query sanitization;
  - gzip decoded-chunk allocation;
  - hanging injected resolver versus configured timeout;
  - installed HTTPX `0.28.1` / httpcore `1.0.9` Host/SNI/peer extension paths.
=======
# Task 3 Final Code Quality and Security Re-review

## Verdict

**PASS**

Reviewed the latest uncommitted Task 3 changes in
`safe_web_fetcher.py` and `test_safe_web_fetcher.py` relative to
`HEAD e3ac05a`. No implementation or commit was changed by this review.
All original P1, P2, and P3 findings are closed.

## Closed findings

### CLOSED P1 — IPv6 site-local SSRF

The common address predicate explicitly rejects IPv6 `is_site_local`; literal
and DNS-answer `fec0::1` regressions verify rejection before a request.

### CLOSED P1 — Content-decoding allocation before the response budget

Page and robots requests send `Accept-Encoding: identity`; non-identity
`Content-Encoding` is rejected before iteration; bounded reads use
`aiter_raw()`. The valid high-expansion gzip regression confirms no body chunk
is entered and the response closes.

### CLOSED P2 — Whole-fetch deadline

One `asyncio.timeout` deadline covers DNS, robots, redirects, transport
operations, and body streaming. DNS-hang and slow-stream regressions return
retryable `fetch_timeout` and close the active response.

### CLOSED P2 — Raw malformed-URL exceptions

Control and surrogate Unicode categories are rejected before request
construction, including DEL, C1 controls, and unpaired surrogates. Defensive
HTTPX invalid-URL/Unicode mapping preserves `unsafe_url`.

### CLOSED P2 — Credential URL sanitization

The normalized predicate removes secret/password/credential/token/signature/
auth families, encoded and repeated fields, semicolon-separated fields, OAuth
material, AWS/Azure/Google signed-URL fields, and API/access/subscription-key
families.

Exact offline probes now remove:

```text
x-api-key
access_key
subscription-key
AWSAccessKeyId
X-Goog-Api-Key
```

while preserving these benign keys and values:

```text
keyword=VALUE
monkey=VALUE
hockey=VALUE
```

The parameterized suite covers the positive and negative cases.

### CLOSED P2 — Robots redirects and policy retrieval

Robots redirects are bounded, fully revalidated and IP-pinned, and use the
same deadline/type/encoding/size/peer controls. Unsafe and exhausted chains
fail closed; the status matrix preserves conservative access handling.

### CLOSED P2 — Charset and non-text codec handling

Decoding applies valid HTTP charset, BOM, bounded early HTML meta charset, then
UTF-8 fallback. GBK, BOM, header-priority, unknown-codec, and non-text codec
cases are covered. `base64_codec`, `hex_codec`, and `rot_13` cannot escape as
raw `LookupError`.

### CLOSED P3 — Peer metadata and transport contracts

The owned client requires peer metadata and exact validated-address matching.
Tests assert SNI, Host, identity encoding, `trust_env=False`, owned-client and
response closure, peer success/failure behavior, and public IP annotations.

## Verification

- Focused fetcher suite: **102 passed** (exit `0`)
- Fetcher + extractor + registration suite: **139 passed** (exit `0`)
- `python -m compileall -q src tests`: exit `0`
- `git diff --check -- <Task 3 files>`: exit `0`
- Additional sanitizer adversarial probe: sensitive key families removed and
  `keyword` / `monkey` / `hockey` preserved exactly.
>>>>>>> codex/search-job-description

No real DNS, TLS, proxy, or external network request was made.
