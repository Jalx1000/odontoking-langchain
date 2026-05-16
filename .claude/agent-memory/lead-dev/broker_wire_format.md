---
name: broker-wire-format
description: Flat JSON dict is the agreed wire format between platform publisher and all external agent consumers
metadata:
  type: project
---

The platform broker (`app/core/broker.py`, both `RabbitMQBroker` and `RedisStreamBroker`) publishes message bodies as a flat JSON dict: `{"wa_id": "...", "text": "...", "message_id": "...", ...}`. There is NO payload-in-payload nesting (the old shape `{"wa_id": ..., "payload": "<json-string>"}` was eliminated in Sprint 4).

**Why:** The agent-template worker (`06.odontoking-agent/app/worker.py`) reads `data["wa_id"]`, `data["text"]`, `data["message_id"]` from a single parsed dict. Keeping the publisher and consumer in the same shape removes a useless `json.dumps` indirection and lets schema changes be made in one place.

**How to apply:** Any new field added to a published message goes into the top-level dict alongside `wa_id`/`text`/`message_id`. Do NOT reintroduce a `payload` sub-key. When reviewing changes to `broker.publish`, reject anything that wraps the dict in another string. See [[webhook-routing-rule]] for who triggers the publish path.
