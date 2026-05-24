# TenantConfig.from_cache_dict silently drops unknown fields between releases

**Type:** debt
**Severity:** low
**Area:** app/core/tenant.py

## Problem
`TenantConfig.from_cache_dict` filters kwargs to only those in `__dataclass_fields__`. If a new field is added to `TenantConfig` between releases but the cache still holds the old format (without the new field), the new field silently defaults to `None` instead of triggering a cache miss and DB re-fetch.

## Impact
After a deploy that adds a new required tenant field, cached tenants will have `None` for that field until their cache TTL expires. This can cause subtle bugs in production for up to `TENANT_CACHE_TTL` seconds after deploy.

## Suggested fix
Add a version field to the cached tenant dict. On deserialization, if the version doesn't match the current schema version, treat it as a cache miss and re-fetch from DB.
