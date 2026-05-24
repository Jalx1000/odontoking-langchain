# Async tests silently skipped — pytest-asyncio not installed

**Type:** test
**Severity:** critical
**Area:** tests/

## Problem
`pytest-asyncio` is not installed in the active venv. All async test functions are silently collected and skipped rather than failing. The test suite reports a passing run while no async tests actually execute.

## Impact
Zero coverage on all async paths (tools, broker, buffer, LangGraph graph). Regressions in async code are invisible until production.

## Suggested fix
Add `pytest-asyncio` to dev dependencies: `uv add pytest-asyncio --dev`. Set `asyncio_mode = "auto"` in `pyproject.toml` under `[tool.pytest.ini_options]`. Verify all previously-skipped async tests now run and pass.
