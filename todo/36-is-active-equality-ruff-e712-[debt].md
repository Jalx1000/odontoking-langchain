# Tenant.is_active == True — ruff E712 inconsistent with internal.py

**Type:** debt
**Severity:** low
**Area:** app/models/tenant.py

## Problem
`_db_get` uses `Tenant.is_active == True` in a SQLAlchemy filter. Ruff flags this as E712 (comparison to True should be `is True`). `internal.py` uses `# noqa: E712` to suppress the same pattern, making the codebase inconsistent.

## Impact
Minor: linting inconsistency. `make lint` may fail or pass depending on ruff config.

## Suggested fix
Use `Tenant.is_active.is_(True)` (SQLAlchemy-idiomatic) in both files. Remove the `# noqa: E712` comment from `internal.py`.
