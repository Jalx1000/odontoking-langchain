---
name: webhook-routing-rule
description: tenant.agent_endpoint_url decides whether webhook publishes to RabbitMQ or runs the in-process agent
metadata:
  type: project
---

In `app/api/v1/whatsapp.py:_handle_webhook_payload`, the routing decision for each WhatsApp message is:

- `tenant.agent_endpoint_url` truthy → `await broker.publish(slug, wa_id, {"text", "message_id"})`. The external agent (e.g. `06.odontoking-agent`) consumes from `wa.{slug}.messages` and replies via its own WhatsApp credentials.
- `tenant.agent_endpoint_url` empty / None → legacy path: `buffer.enqueue(wa_id, text, process_fn)` invoking `_AGENT_REGISTRY[agent_type]` in-process.

**Why:** This is the discriminant that lets the platform stay backwards-compatible with the odontoking tenant (env-var fallback, no endpoint URL) while peeling new tenants off into their own externally-deployed agents. A broker.publish failure must NOT fall back to the internal path — that would split-brain the routing and double-send.

**How to apply:** When adding a new tenant, the Admin API sets `agent_endpoint_url` to indicate the agent is externally deployed. If you ever add a third routing destination (HTTP webhook, gRPC, etc.) extend this conditional but keep the rule that exactly one destination handles a given message. See [[broker-wire-format]] for the publish contract.
