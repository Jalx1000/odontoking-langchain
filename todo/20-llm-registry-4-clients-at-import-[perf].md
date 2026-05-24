# LLMRegistry instantiates 4 ChatOpenAI clients at import even when only one used

**Type:** perf
**Severity:** low
**Area:** app/services/llm/registry.py

## Problem
`LLMRegistry.__init__` constructs all `ChatOpenAI` client instances at startup regardless of how many will actually be used. Each constructor makes an OpenAI auth check and allocates HTTP connection pools.

## Impact
Increased startup time and memory usage. In tests that import the app, 4 OpenAI clients are created even for tests that never call the LLM.

## Suggested fix
Use lazy initialization — store model configs (model name, temperature, etc.) in the registry and build `ChatOpenAI` instances on first use. Cache the built instance per model name.
