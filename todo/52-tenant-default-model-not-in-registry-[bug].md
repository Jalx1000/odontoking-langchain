# Tenant.llm_model default is gpt-4o-mini — not in LLMRegistry, new tenants get unrouteable model

**Type:** bug
**Severity:** high
**Area:** app/models/tenant.py

## Problem
`Tenant.llm_model` column has a default value of `"gpt-4o-mini"`. `LLMRegistry` does not contain `gpt-4o-mini`. Any tenant created via the Admin API will have an unrouteable model from the moment of creation.

## Impact
Every dynamically-created tenant fails to process LLM calls until an operator manually updates the `llm_model` field via the Admin API.

## Suggested fix
Change the default to the first model in `LLMRegistry` (whatever that currently is). Add a migration to update existing tenants. Add a startup validation that checks all active tenants have a model that exists in the registry.
