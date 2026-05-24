# broker.dlq_retry republishes with empty wa_id if field absent — message dropped

**Type:** bug
**Severity:** medium
**Area:** app/core/broker.py

## Problem
`RabbitMQBroker.dlq_retry` calls `flat.pop("wa_id", entry.get("wa_id", ""))`. If `wa_id` is absent from both `flat` and `entry`, it republishes with `wa_id=""`. The message enters the queue with an empty routing key and is never consumed.

## Impact
DLQ retry silently drops messages that lack `wa_id` instead of raising an error. Operators see the retry as successful when the message was actually lost.

## Suggested fix
Validate that `wa_id` is non-empty before republishing. If absent, move to a permanent error log (DB or structured log at ERROR level) instead of republishing with an empty key.
