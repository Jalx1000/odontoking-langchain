# In-memory dedup cache and rate-limit are per-process — multi-replica double-processing

**Type:** bug
**Severity:** high
**Area:** app/api/v1/whatsapp.py

## Problem
`_seen_message_ids` (dedup cache) and `_wa_message_times` (per-user rate limit) are plain Python dicts/sets defined at module level. In a multi-replica Railway deployment each replica has its own independent copy.

## Impact
The same WhatsApp message can be processed twice (once per replica) if Meta delivers it to different replicas. Rate limits per user are not enforced across replicas — a user can bypass them by triggering different replicas.

## Suggested fix
Move dedup and rate-limit state to Valkey (Redis). Use `SET NX EX` for dedup (key = message_id, TTL = 5 min) and a Redis sorted set for per-user rate limiting. The `redis` package install (see issue 08) is a prerequisite.
