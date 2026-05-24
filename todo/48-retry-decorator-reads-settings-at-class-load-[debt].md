# LLMService retry decorator uses MAX_LLM_CALL_RETRIES evaluated at class-load time

**Type:** debt
**Severity:** low
**Area:** app/services/llm/service.py

## Problem
The tenacity `@retry` decorator on `LLMService._invoke_with_retry` evaluates `settings.MAX_LLM_CALL_RETRIES` when the class is defined (at import time). Changing this setting at runtime or in tests has no effect.

## Impact
Tests cannot override retry count. A runtime config change to reduce retries during an incident requires a full redeploy.

## Suggested fix
Use a `retry_if_exception_type` with a dynamic stop condition: `stop=stop_after_attempt(lambda: settings.MAX_LLM_CALL_RETRIES)` or read `settings.MAX_LLM_CALL_RETRIES` inside the function body and implement retry logic manually with a loop.
