# Discard endpoint uses lset+lrem sentinel pattern that races with concurrent dlq_retry

**Type:** bug
**Severity:** low
**Area:** app/api/admin/conversations.py

## Problem
The discard endpoint sets a list element to a sentinel value via `lset` and then removes it with `lrem`. If a concurrent `dlq_retry` call reads the same index between the `lset` and `lrem`, it processes the sentinel value as a real message.

## Impact
Low probability race condition that could cause a malformed message to be republished to the queue, potentially crashing the consumer.

## Suggested fix
Use a Redis transaction (`MULTI/EXEC`) or Lua script to atomically mark-and-remove the entry. Alternatively, use `LINDEX`+`LREM` within a pipeline with optimistic locking.
