# memory_service.add() is fire-and-forget asyncio.create_task — can be GC'd before completing

**Type:** bug
**Severity:** medium
**Area:** app/core/langgraph/odontoking_graph.py

## Problem
`asyncio.create_task(memory_service.add(...))` creates a task with no reference kept to it. Python's garbage collector can collect the task before it completes, silently cancelling the memory write.

## Impact
Long-term memory (pgvector) entries may not be written for some conversations. The agent loses personalization context for returning patients.

## Suggested fix
Store the task reference: `task = asyncio.create_task(...); _background_tasks.add(task); task.add_done_callback(_background_tasks.discard)`. The same `_background_tasks` set pattern already used in `whatsapp.py` for background tasks.
