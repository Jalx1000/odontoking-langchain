# app/main.py lifespan teardown not audited — broker/cache/buffer/agent may not be closed cleanly

**Type:** risk
**Severity:** medium
**Area:** app/main.py

## Problem
The FastAPI lifespan startup is documented but teardown isn't audited for: broker connection close, cache disconnect, buffer flush, agent pool close.

## Impact
On SIGTERM (rolling deploy), unflushed buffers and dangling connections; possible message loss and connection leaks.

## Suggested fix
Implement explicit teardown for every resource in lifespan; log each step; add a `tests/test_lifespan.py` that asserts every component's `close()` is awaited.
