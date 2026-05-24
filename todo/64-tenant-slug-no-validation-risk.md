# tenant_slug path param has no structured input validation — arbitrary chars in queue names and log keys

**Type:** risk
**Severity:** medium
**Area:** app/api/v1/whatsapp.py

## Problem
`tenant_slug` flows into queue names, Redis keys, and log fields without regex validation.

## Impact
Injection or accidental collisions; ops-only chars in queue names break management UIs.

## Suggested fix
Add a Pydantic constraint `pattern=r"^[a-z0-9-]{2,32}$"` on the path param and reject non-matches with 400.
