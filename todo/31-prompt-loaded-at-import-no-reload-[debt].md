# Prompt file loaded at import — no hot reload, no graceful failure if missing

**Type:** debt
**Severity:** low
**Area:** app/core/langgraph/odontoking_graph.py

## Problem
`_load_odontoking_prompt()` opens and reads `odontoking.md` at module import time using a plain `open()` call. If the file is missing, the module fails to import with an unhandled `FileNotFoundError`.

## Impact
A missing prompt file crashes the entire app at startup with a cryptic import error. Hot-reloading prompt changes requires a full process restart.

## Suggested fix
Wrap the file read in a try/except with a fallback default prompt and a clear `logger.error`. For hot reload, read the prompt file inside each graph invocation (cached with a short TTL) rather than at import time.
