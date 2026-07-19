# Task 2 implementation report

## RED

- Added `tests/unit/test_job_description_extractor.py` before creating the adapter.
- Ran `python -m pytest tests/unit/test_job_description_extractor.py -p no:cacheprovider --basetemp .pytest-task2-red`.
- Observed the expected collection failure: `ModuleNotFoundError: No module named 'starter_agent.tools.adapters'`.
- Added a salary/location-shape test before extending numeric Schema.org salary
  normalization; it failed as expected because the numeric range was omitted.

## Implementation

- Added the `adapters` package and `JobDescriptionExtractor` with the immutable
  `ExtractedJobDescription` contract.
- JSON-LD handling accepts single objects, lists, and `@graph` entries and selects
  `JobPosting` only.
- HTML fallback removes navigation, footer, scripts, styles, forms, and common
  cookie/banner/modal elements before extracting visible content.
- Plain text and HTML share section normalization, duplicate removal, and
  completeness classification. Extracted text is returned only as inert data.

## GREEN

- Ran `python -m pytest tests/unit/test_job_description_extractor.py -p no:cacheprovider --basetemp .pytest-task2-green`.
- Result: `8 passed in 0.47s` after the additional numeric-salary test.
- Quality-review follow-up added five regression tests first. The expected RED
  run exposed wrapper-based section loss and unsupported metadata shapes.
- Ran `python -m pytest tests/unit/test_job_description_extractor.py -p no:cacheprovider --basetemp .pytest-task2-quality-green`.
- Result: `13 passed in 0.49s`; `python -m compileall -q` and `git diff --check`
  also pass.

## Commit

- `df44992 feat: add job description extractor`.
- `3028c27 fix: harden job description extraction`.

## Risks / follow-up

- The extractor intentionally does not decide whether an empty document is a
  JavaScript-only page; the fetcher/tool layer owns mapping that condition to the
  user-facing `dynamic_page_unsupported` error.
- HTML labels are limited to the aliases specified by the design document; future
  localization expansion should add tests first.
- JSON-LD traversal now has explicit node/depth budgets. A valid JobPosting beyond
  that budget is deliberately treated as unavailable structured data and falls
  back to HTML rather than consuming unbounded resources.

## Linear HTML extraction follow-up

- Added four tests before the second re-review fix: nested text-only blocks emit
  leaf items only, bare text inside a heading wrapper is retained, rich `p`/`li`
  content remains atomic, and 2,000 wrappers reject the old recursive container
  search path deterministically.
- RED: `3 failed, 14 passed`; the expected failures were nested aggregate output,
  dropped wrapper bare text, and use of `_contains_semantic_content`.
- Replaced repeated descendant queries with two explicit-stack traversals: one
  post-order pass marks terminal structural blocks; one document-order event pass
  maintains section and atomic-content state. Both passes are linear in DOM nodes
  and edges.
- JSON-LD list expansion now only enqueues entries within the remaining traversal
  node budget.
- GREEN: `17 passed in 0.40s` using `.pytest-task2-linear-green`.
- `daee4e7 fix: make html extraction linear`.
