# Makefile dev target: uvicorn fail causes wait to hang on langgraph

**Type:** debt
**Severity:** low
**Area:** Makefile

## Problem
`make dev` runs `uvicorn ... & uv run langgraph dev ...; wait`. If uvicorn exits immediately (e.g. import error, port already in use), the shell continues to wait for `langgraph dev` indefinitely. Ctrl+C may not cleanly kill both processes.

## Impact
A broken uvicorn startup (e.g. syntax error in app code) leaves a dangling `langgraph dev` process that must be killed manually. Confusing developer experience during debugging.

## Suggested fix
Use a process group approach: `trap 'kill 0' SIGINT SIGTERM; uvicorn ... & langgraph dev ... & wait`. The `trap 'kill 0'` ensures both child processes are killed when the parent receives a signal. Alternatively, use a tool like `overmind` or `honcho` for multi-process dev.
