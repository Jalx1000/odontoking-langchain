# MessageBufferService.try_acquire_worker returns True on Redis exception — two workers corrupt LangGraph

**Type:** bug
**Severity:** high
**Area:** app/services/buffer.py

## Problem
`try_acquire_worker` returns `True` (fail-open) when a Redis exception occurs, meaning it grants the lock to the caller even when the lock state is unknown. In a multi-replica setup, two replicas can both believe they hold the worker lock for the same `wa_id`.

## Impact
Two workers process the same user's messages concurrently. Both write to the same LangGraph checkpoint thread, causing state corruption and duplicate or conflicting agent responses sent to the patient.

## Suggested fix
Change to fail-closed: return `False` on Redis exception and log the error. A message delayed is better than a corrupted conversation state. Add a metric counter for lock acquisition failures so the Redis issue is observable.
