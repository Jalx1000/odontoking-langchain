# admin/conversations.py reaches into broker._r private attr

**Type:** debt
**Severity:** medium
**Area:** app/api/admin/conversations.py

## Problem
The admin conversations endpoint accesses `broker._r` directly to interact with Redis. This is a private implementation detail of the broker class.

## Impact
If the broker implementation changes (e.g. attribute renamed, Redis client swapped), the admin endpoint breaks silently with an `AttributeError` at runtime rather than a compile-time error.

## Suggested fix
Add a public method to the broker class (e.g. `broker.get_redis_client()` or `broker.discard_message(key, index)`) and call that instead of accessing `_r` directly.
