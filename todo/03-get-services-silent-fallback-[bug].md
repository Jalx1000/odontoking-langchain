# get_services keyword-no-match fallback is silent

**Type:** bug
**Severity:** high
**Area:** app/core/langgraph/tools/odontoking.py

## Problem
When `keyword` is provided but no services match, the tool silently returns all services without telling the LLM the filter failed. The LLM believes it received filtered results and proceeds accordingly, potentially presenting irrelevant services to the patient.

## Impact
LLM context pollution — the agent may present the wrong services or fail to ask the patient to clarify. Silent fallback makes the filter unobservable in Langfuse traces without examining `returned == total && keyword != ""`.

## Suggested fix
Return an explicit field in the JSON response: `"filter_applied": false, "filter_reason": "no services matched keyword 'X'"`. The LLM can then decide to retry with a different keyword or ask the patient to clarify. Alternatively, implement accent/case normalization to reduce fallback frequency (see planning/06-e2e-tests-conversacion-railway.md for context).
