# DatabaseService uses logger.error instead of logger.exception

**Type:** debt
**Severity:** medium
**Area:** app/services/database.py

## Problem
`DatabaseService` calls `logger.error(...)` in exception handlers instead of `logger.exception(...)`. This drops the traceback from structured logs, making it much harder to diagnose database errors in production.

## Impact
Database errors appear in logs without stack traces. Root cause analysis requires reproducing the error locally.

## Suggested fix
Replace all `logger.error(...)` in `except` blocks within `app/services/database.py` with `logger.exception(...)`. This is AGENTS.md commandment 4.
