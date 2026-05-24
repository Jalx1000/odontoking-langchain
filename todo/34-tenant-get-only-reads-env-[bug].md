# tenant.py get_tenant only reads env registry — DB tenants invisible to legacy webhook

**Type:** bug
**Severity:** high
**Area:** app/core/tenant.py

## Problem
`get_tenant()` only looks up tenants in the env-based registry (built from `WHATSAPP_PHONE_NUMBER_ID` and similar env vars). Tenants created via the Admin API (`POST /api/v1/admin/tenants`) are stored in the database but are invisible to the webhook handler and agent registry.

## Impact
Any tenant created dynamically via the Admin API cannot receive or process WhatsApp messages. The dynamic tenant creation feature is effectively non-functional.

## Suggested fix
Make `get_tenant()` fall through to a DB lookup when the env registry misses. Cache DB results in Valkey with `TENANT_CACHE_TTL` (already configured). This makes dynamically-created tenants fully functional without a redeploy.
