# _renew_lock_loop runs forever even if Redis unreachable — silent log spew

**Type:** debt
**Severity:** low
**Area:** app/services/buffer.py

## Problem
`_renew_lock_loop` is an async loop that continuously renews the Redis worker lock. If Redis becomes unreachable, the loop catches the exception, logs a debug message, and continues looping forever — generating log noise without ever surfacing a meaningful error.

## Impact
Redis outage produces thousands of debug log entries per second per active conversation. Log storage fills up. The real Redis connectivity issue is buried under the noise.

## Suggested fix
After N consecutive renewal failures (e.g. 3), log at ERROR level and stop the loop. The lock will expire naturally and another worker (or the same worker after retry) can reacquire it.
