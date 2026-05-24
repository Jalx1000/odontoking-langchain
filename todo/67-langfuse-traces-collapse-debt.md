# langfuse_callback_handler reused for every wa_id with no per-trace context — all traces collapse into single user

**Type:** debt
**Severity:** medium
**Area:** app/core/observability.py

## Problem
A single Langfuse callback handler is reused across requests; without per-trace `user_id`/`session_id`, all conversations collapse into one trace bucket.

## Impact
Cannot debug a single patient's session in Langfuse; analytics are unusable.

## Suggested fix
Create a fresh callback handler per request with `user_id=wa_id`, `session_id=thread_id`, and `tenant=tenant.slug`. Pass via the LangGraph runtime config.
