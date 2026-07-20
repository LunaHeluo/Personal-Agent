# Task 4 final-review remediation report

## Scope

- `src/starter_agent/agent/tool_result_guard.py`
- `src/starter_agent/tools/builtin/job_description_search.py`
- `tests/unit/test_context_tokens.py`
- `tests/unit/test_search_job_description.py`

## TDD evidence

Added the three final-review regressions before production edits. The initial
focused run failed in the expected four places:

1. A metadata-first source result copied the attacker sentinel into
   `partial_content`.
2. The generic fallback reported 330 context tokens with a 300-token budget.
3. `C++-Developer` did not match `C++ Developer`.
4. `C++/C Developer` incorrectly matched `C Developer`.

The follow-up focused run is green.

## Remediation

- Generic truncation serializes a payload with the source envelope's top-level
  `metadata` removed, so copied partial content cannot replay source metadata.
- The fallback has finite reduction steps: bounded partial content,
  metadata-only, compact metadata, then empty content when tool-message
  framing itself is the smallest viable value.
- Final `context_result_tokens` is inserted and re-counted before accepting a
  generic or structured truncated result.
- Token matching preserves `C++` and `C#` suffixes before punctuation and
  treats `C++/C` as one compound token.

## Verification

```text
python -m pytest tests/unit/test_context_tokens.py \
  tests/unit/test_search_job_description.py -p no:cacheprovider -q
40 passed

python -m compileall -q src/starter_agent/agent/tool_result_guard.py \
  src/starter_agent/tools/builtin/job_description_search.py

git diff --check -- <four scoped files>
```

All commands completed successfully. The empty-content tiny-budget regression
uses an 80-token limit, where even the metadata-only envelope exceeds budget.
