# gpt-4o-mini not in LLMRegistry — silent fallback to gpt-5-mini

**Type:** bug
**Severity:** high
**Area:** app/services/llm/registry.py

## Problem
`DEFAULT_LLM_MODEL=gpt-4o-mini` in `.env.development` but `LLMRegistry` does not contain that model name. At startup `LLMService` logs `default_model_not_found_using_first` and silently uses `gpt-5-mini` instead.

## Impact
The model actually used differs from the configured one. Cost and behavior are unpredictable. Any environment where `gpt-4o-mini` is expected (evals, prompts tuned for it) will silently use a different model.

## Suggested fix
Either add `gpt-4o-mini` to `LLMRegistry` or update `DEFAULT_LLM_MODEL` in `.env.development` to match the first model in the registry. Add a startup assertion that raises if `DEFAULT_LLM_MODEL` is not in the registry.
