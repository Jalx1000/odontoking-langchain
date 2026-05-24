# psycopg2-binary AND psycopg both in deps — psycopg2-binary is dead weight

**Type:** debt
**Severity:** low
**Area:** pyproject.toml

## Problem
Both `psycopg2-binary` and `psycopg[binary]` are listed as dependencies. The codebase only uses `psycopg` (asyncpg-style async via `psycopg_pool`). `psycopg2-binary` is never imported.

## Impact
Unnecessary build time and image size. On some platforms `psycopg2-binary` fails to install, causing confusing CI failures unrelated to the actual codebase.

## Suggested fix
Remove `psycopg2-binary` from `pyproject.toml` and run `uv sync` to verify nothing breaks.
