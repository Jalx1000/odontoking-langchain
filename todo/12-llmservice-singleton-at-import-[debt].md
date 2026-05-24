# LLMService singleton instantiated at import — reads env before override possible

**Type:** debt
**Severity:** high
**Area:** app/services/llm/service.py

## Problem
`LLMService` is instantiated at module import time, reading `OPENAI_API_KEY` and constructing all `ChatOpenAI` clients before any test fixture or env override can run. Tests that need a different API key or model cannot override it.

## Impact
Tests that import anything from the app inadvertently initialize the LLM client with production credentials. Mocking requires patching at the class level before import, which is fragile and order-dependent.

## Suggested fix
Use lazy initialization: instantiate `LLMService` inside a `get_llm_service()` function cached with `functools.lru_cache`, called only when a request arrives. This is the same pattern used by FastAPI's `Depends`.
