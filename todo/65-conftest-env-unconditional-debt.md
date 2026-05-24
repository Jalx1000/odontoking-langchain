# tests/conftest.py sets env vars unconditionally — import order fragility

**Type:** debt
**Severity:** low
**Area:** tests/conftest.py

## Problem
`conftest.py` sets env vars at import time, but several modules read env at their own import — leading to order-dependent failures.

## Impact
Tests pass locally, fail in CI (or vice versa), depending on collection order.

## Suggested fix
Set env vars in `pytest_configure` before any app modules are imported, or use a session-scoped autouse fixture that asserts no app modules were imported yet.
