# odontoking._HEADERS and _BASE baked at import — cannot change token per tenant

**Type:** bug
**Severity:** critical
**Area:** app/core/langgraph/tools/odontoking.py

## Problem
`_HEADERS` and `_BASE` are module-level constants set at import from `settings.ODONTOKING_API_TOKEN` and `settings.ODONTOKING_API_URL`. Every tenant that uses these tools shares the same CRM credentials.

## Impact
Multi-tenant blocker: a second dental clinic tenant would use Odontoking's CRM token. Patient data cross-contamination between tenants.

## Suggested fix
Remove module-level `_HEADERS` and `_BASE`. Each tool function should accept tenant credentials as a parameter (or read them from a `TenantContext` context var set by the webhook handler before invoking the agent).
