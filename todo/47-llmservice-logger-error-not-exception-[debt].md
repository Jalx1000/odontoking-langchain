# LLMService uses logger.error for OpenAIError instead of logger.exception

**Type:** debt
**Severity:** low
**Area:** app/services/llm/service.py

## Problem
`LLMService._invoke_with_retry` calls `logger.error(...)` when catching `OpenAIError`. This drops the traceback from structured logs, violating AGENTS.md commandment 4 ("use logger.exception() instead of logger.error() to preserve tracebacks").

## Impact
OpenAI API errors appear in logs without stack traces. The exact error type (rate limit vs auth vs server error) is harder to diagnose.

## Suggested fix
Replace `logger.error(...)` with `logger.exception(...)` in all `except` blocks in `app/services/llm/service.py`.
