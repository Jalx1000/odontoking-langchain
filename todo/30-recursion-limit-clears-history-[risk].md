# recursion_limit=50 is high — on hit clears entire user history

**Type:** risk
**Severity:** high
**Area:** app/core/langgraph/odontoking_graph.py

## Problem
When the LangGraph graph hits the recursion limit (50 steps), the error handler calls `clear_history(wa_id)` which deletes the entire conversation history and LangGraph checkpoints for that user.

## Impact
A patient mid-conversation loses all context. If the recursion was caused by a tool loop bug, the user must start the entire appointment booking flow from scratch. Repeat triggers would repeatedly wipe the conversation.

## Suggested fix
On recursion limit hit: (1) stop the graph and send an apology message to the user, (2) log at ERROR level with the full state for debugging, (3) do NOT clear history automatically — let an operator review and manually clear if needed. Reduce `recursion_limit` to 20-25 for a tighter safety net.
