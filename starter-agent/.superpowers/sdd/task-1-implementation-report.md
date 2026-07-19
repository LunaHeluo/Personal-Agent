# Task 1 implementation report

## Status

Ready to commit after the dependency-lock issue was resolved by the root agent.

## Red test

`tests/unit/test_search_job_description_registration.py` was added and failed
as expected during collection because `JobDescriptionToolConfig` was not yet
exported by `starter_agent.settings`.

## Implementation summary

- Added `JobDescriptionToolConfig` with the specified constrained defaults.
- Added `tools.job_description` to `ToolsConfig`.
- Enabled `search_job_description` and added its configuration to the runtime
  configuration; added the same safe settings to the example configuration
  without enabling the tool there.
- Declared the Beautiful Soup dependency in `pyproject.toml`.

## Dependency lock

The root agent completed `uv --cache-dir .uv-cache lock`, which adds
`beautifulsoup4==4.15.0` and its `soupsieve` dependency to `uv.lock`. It also
installed Beautiful Soup into the existing virtual environment after full sync
was blocked by an in-use agent executable.

## Green test

` .\\.venv\\Scripts\\python.exe -m pytest
tests\\unit\\test_search_job_description_registration.py
tests\\unit\\test_settings.py -p no:cacheprovider
--basetemp .pytest-task1-config-escalated`

Result: **5 passed** in 0.34s. The run was performed outside the sandbox so
pytest could create and clean its temporary directory.

## Commit

`929b77f feat: add job description tool configuration`

The commit contains only the Task 1 configuration, dependency lock, and
registration-test files. `config/config.yaml` also contains pre-existing user
model/provider changes; they were retained as directed because the same file
must include the new tool configuration.

## Follow-up hardening

- `search_job_description` is configured but no longer enabled until the
  registry implementation is available in Task 5.
- User-Agent values are trimmed and reject blank text, ASCII control
  characters, and values outside the configured length limit.
- Tests cover timeout, response-size, redirect, and User-Agent boundaries,
  plus construction of the current `ToolRegistry` from runtime settings.
- Focused configuration, registry, and settings tests passed: **23 passed**.
- Follow-up commit: `98532fa fix: harden job description configuration`.
- Printable-ASCII User-Agent regression tests passed: **20 passed**. The
  final follow-up rejects Chinese, accented, and emoji User-Agent values.
- Final follow-up commit: `07521b6 fix: validate ASCII user agent`.
