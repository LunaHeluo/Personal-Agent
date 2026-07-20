# Task 3 Implementation Report: Safe Public Web Fetcher

## Status

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
