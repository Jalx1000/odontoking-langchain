# cache.py silent in-memory fallback when VALKEY_HOST empty — env typo yields silent in-memory in production

**Type:** risk
**Severity:** medium
**Area:** app/core/cache.py

## Problem
`_create_cache_service` only emits a warning if `VALKEY_HOST` is set AND the redis package is missing. If `VALKEY_HOST` is empty (e.g. a typo in the env file: `VALKEY_HOST =localhost` with a space), the service silently uses in-memory cache with no warning at all.

## Impact
A simple env var typo causes production to run with in-memory cache silently. Memory usage grows unbounded. Cache TTLs are not shared across replicas. The issue is invisible until memory is exhausted.

## Suggested fix
Add a startup check: if `APP_ENV=production` and cache backend is in-memory, log at ERROR level (not warning). In production, in-memory cache should be an explicit deployment choice, not a silent fallback.
