# OdontokingAgent._llm ignores per-tenant llm_model field

**Type:** bug
**Severity:** high
**Area:** app/core/langgraph/odontoking_graph.py

## Problem
`OdontokingAgent.__init__` initializes `ChatOpenAI` using `settings.ODONTOKING_LLM_MODEL` (a global setting), ignoring the `tenant.llm_model` field that exists in the `Tenant` model. Every tenant uses the same model regardless of their configured preference.

## Impact
The `tenant.llm_model` column in the database is meaningless. Operators cannot assign different models to different tenants via the Admin API.

## Suggested fix
Pass `tenant` to the agent constructor (or a factory method) and use `tenant.llm_model or settings.ODONTOKING_LLM_MODEL` when building the `ChatOpenAI` instance. Ensure the model is in `LLMRegistry` before using it.
