# Auth and chat REST endpoints exist but unused by production tenant — dead code

**Type:** debt
**Severity:** low
**Area:** app/api/v1/auth.py

## Problem
`app/api/v1/auth.py` and the chat REST endpoints exist alongside the WhatsApp webhook flow but are not used by any production tenant. They still get rate limited, logged, security-reviewed, and maintained.

## Impact
Maintenance burden. Security surface area is larger than necessary. Future developers may be confused about whether these endpoints are in use.

## Suggested fix
Document explicitly whether these endpoints are intended for a future web chat feature or can be removed. If unused and unplanned, remove them. If planned, add a TODO comment linking to the planning document.
