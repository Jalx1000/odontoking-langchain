---
name: async-test-pattern
description: How to write runnable async tests in this project — pytest-asyncio is not installed, use asyncio.run() wrappers
metadata:
  type: feedback
---

pytest-asyncio is NOT installed in this project.  `@pytest.mark.asyncio` / `asyncio_mode = "auto"` in pyproject.toml have no effect — all async test functions are silently skipped with `PytestUnhandledCoroutineWarning`.

**Rule:** Write sync test functions that call `asyncio.run(coro)` internally.

```python
def _run(coro):
    return asyncio.run(coro)

def test_some_feature():
    with patch("httpx.AsyncClient") as cls:
        ...
        result = _run(some_tool.ainvoke({"arg": "value"}))
    assert ...
```

**Why:** The entire tool test suite (test_odontoking_tools.py, test_crm_tool.py, etc.) uses `@pytest.mark.asyncio` but all those tests are skipped.  The only tests that actually run are sync ones.  Using `asyncio.run()` is the correct pattern until pytest-asyncio is added.

**How to apply:** Any time you write tool tests or any async unit test in this project, use `asyncio.run()` not `@pytest.mark.asyncio`.

**LangChain tool coroutine access:** To test the raw async function underneath a `@tool` decorator (bypassing Pydantic validation), use `tool.coroutine(...)` not `tool.func(...)`.  `tool.func` is `None` for async tools; `tool.coroutine` holds the actual async function.
