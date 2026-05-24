# Agent registry only has odontoking — other agent_types silently misrouted

**Type:** bug
**Severity:** high
**Area:** app/api/v1/whatsapp.py

## Problem
`_AGENT_REGISTRY.get(tenant.agent_type, odontoking_agent)` silently falls back to `odontoking_agent` for any tenant whose `agent_type` is not `"odontoking"`. A Kohlberg tenant or any future tenant will have their messages processed by the Odontoking agent without any warning.

## Impact
Multi-tenant routing is broken by default for any tenant that is not Odontoking. Messages get wrong context, wrong tools, wrong prompt.

## Suggested fix
Remove the default fallback. Raise a clear error (or return a user-facing message) when `tenant.agent_type` is not in `_AGENT_REGISTRY`. Log `agent_type_not_found` with the tenant slug so operators are notified immediately.
