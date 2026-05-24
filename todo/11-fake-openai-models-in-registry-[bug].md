# LLMRegistry defines gpt-5.4 and gpt-5.4-nano which are not real OpenAI models

**Type:** bug
**Severity:** critical
**Area:** app/services/llm/registry.py

## Problem
`LLMRegistry` includes `gpt-5.4` and `gpt-5.4-nano` as fallback models. These model IDs do not exist in the OpenAI API. Any call that falls back to these models will receive a 404 from OpenAI.

## Impact
When the primary model is rate-limited or times out, the fallback chain reaches a non-existent model and fails hard. The patient receives an error response instead of a degraded but functional reply.

## Suggested fix
Replace with real OpenAI model IDs (e.g. `gpt-4o`, `gpt-4o-mini`, `gpt-3.5-turbo`). Verify each model ID against the OpenAI models list before adding to the registry.
