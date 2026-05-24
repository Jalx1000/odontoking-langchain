# MessageBufferService.recover is Redis-only — InMemory backend loses messages on crash

**Type:** risk
**Severity:** medium
**Area:** app/services/buffer.py

## Problem
`MessageBufferService.recover()` is implemented only for the Redis backend. When using the InMemory backend (which is the case when the `redis` package is not installed), a process crash or restart silently loses all buffered messages that hadn't been processed yet.

## Impact
In development and in any production deploy where Redis is unavailable, messages received during high load (when buffer has accumulated messages) are permanently lost on restart. Patients' messages are silently dropped.

## Suggested fix
For the InMemory backend, log all unprocessed buffered messages at ERROR level on shutdown so they can be manually replayed. Long-term: require Redis in production and fail startup clearly if unavailable.
