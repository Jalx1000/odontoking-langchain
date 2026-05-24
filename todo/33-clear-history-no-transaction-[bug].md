# OdontokingAgent.clear_history runs 3 DELETEs without transaction — partial deletes possible

**Type:** bug
**Severity:** medium
**Area:** app/core/langgraph/odontoking_graph.py

## Problem
`clear_history(wa_id)` executes three separate `DELETE FROM <table> WHERE thread_id = %s` statements in sequence without wrapping them in a transaction. If a connection error occurs after the first DELETE, the data is left in an inconsistent state.

## Impact
Partial history clears leave orphaned records in some tables but not others. Subsequent conversations may have inconsistent state (e.g. LangGraph checkpoints deleted but chat history preserved, or vice versa).

## Suggested fix
Wrap all three DELETE statements in a single `async with conn.transaction():` block using the asyncpg connection.
