# Task 4 implementation report

## Scope

- Added `SearchJobDescriptionTool` in `src/starter_agent/tools/builtin/job_description_search.py`.
- Added focused orchestration tests in `tests/unit/test_search_job_description.py`.

## Contract implemented

- Fixed tool name `search_job_description`, read risk level, and strict single-URL input schema.
- Calls only the injected fetcher and extractor; it does not save jobs, write memory, or call other tools.
- Returns extracted structured fields plus source/final URL, UTC retrieval time, page SHA-256, provenance, and the untrusted external-content marker.
- Maps `FetchFailure` code/display/retryability unchanged.
- Distinguishes invalid arguments, title/company mismatches, static-content-empty dynamic shells, and missing core JD sections.

## TDD evidence

1. `tests/unit/test_search_job_description.py` was added before production code; its first run failed at collection with `ModuleNotFoundError: starter_agent.tools.builtin.job_description_search`.
2. After minimal implementation, the focused suite passed: `13 passed`.
3. A follow-up provenance assertion required blank `source_ref` to be preserved. It failed first, then passed after the metadata change.

## Verification

```text
pytest tests/unit/test_search_job_description.py -p no:cacheprovider
13 passed

pytest tests/unit/test_job_description_extractor.py tests/unit/test_safe_web_fetcher.py -p no:cacheprovider
119 passed

python -m compileall -q src tests
exit 0

git diff --check
exit 0
```

## Notes for review

- Matching is only evaluated when the corresponding selected title/company is non-empty; comparison is Unicode-normalized, case-insensitive, and based on contiguous token sequences.
- The SHA-256 is retained exactly from the fetcher, whose hash is calculated over the fetched response bytes. `raw_text` stays the extractor output derived from the fetched response text.

## Quality-review remediation

The review identified three runtime-contract gaps. They were fixed with focused
regressions:

- The generic and structured `ToolResultGuard` truncation paths now rebuild
  metadata from a strict allowlist. They preserve only a boolean
  `is_untrusted_external_content=True` classification and guard-generated
  trace metadata; arbitrary source metadata (including `source_ref`) is not
  copied. An oversized `search_job_description` payload with external text
  verifies the marker remains visible after truncation.
- Job identity matching now applies Unicode NFKC plus case folding, tokenizes
  punctuation boundaries, preserves language tokens such as `C++`, and only
  accepts contiguous token sequences. This rejects `AI`/`Paid` and
  `AB`/`Grab` false matches while accepting punctuation and prefix/suffix
  variants.
- Fetch-failure tests now cover every stable fetcher error code and retryable
  flag. Tests also verify that extractor defects propagate unchanged and that
  `retrieved_at` is parseable and UTC-aware.

Remediation verification:

```text
pytest tests/unit/test_search_job_description.py tests/unit/test_context_tokens.py \
  tests/unit/test_safe_web_fetcher.py tests/unit/test_job_description_extractor.py \
  -p no:cacheprovider
153 passed

python -m compileall -q src tests
exit 0

git diff --check
exit 0
```
