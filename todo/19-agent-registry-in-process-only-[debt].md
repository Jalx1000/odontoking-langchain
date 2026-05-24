# _AGENT_REGISTRY is in-process only — second tenant requires code change

**Type:** debt
**Severity:** high
**Area:** app/api/v1/whatsapp.py

## Problem
`_AGENT_REGISTRY` is a hardcoded dict built at module import. Adding a new tenant agent requires modifying Python code and redeploying. There is no way to register a new agent type at runtime via the Admin API.

## Impact
Onboarding a new tenant with a different agent type always requires a code change and full redeploy. This contradicts the multi-tenant platform design goal.

## Suggested fix
Move agent instantiation to a factory that reads from the `Tenant.agent_type` field and maps to agent classes registered in a config or plugin pattern. Long-term: use `tenant.agent_endpoint_url` to route externally (already planned in sprint 4).
