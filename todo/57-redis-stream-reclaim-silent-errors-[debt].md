# RedisStreamBroker._reclaim_pending catches all errors with debug log — masks XAUTOCLAIM errors

**Type:** debt
**Severity:** low
**Area:** app/core/broker.py

## Problem
`_reclaim_pending` catches all exceptions with a `logger.debug` log. `XAUTOCLAIM` can fail due to Redis version incompatibility (requires Redis 6.2+) or permission errors — these are operational problems that should be surfaced loudly.

## Impact
If `XAUTOCLAIM` is silently failing, stuck messages (from crashed workers) are never reclaimed. They accumulate in the PEL (Pending Entry List) indefinitely, causing memory growth and message loss.

## Suggested fix
Catch specific Redis errors separately: log `ResponseError` (version/permission issue) at ERROR level with the full exception. Only use DEBUG for expected transient errors.
