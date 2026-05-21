---
name: kohlberg-flow-audit-2026-05-19
description: Audit of ciudad→productos silent-failure bug in Kohlberg WhatsApp agent — 5 bugs found
metadata:
  type: project
---

Audit performed 2026-05-19. Thread 59176616013 had 970 checkpoints (now cleared). Code has pending deploy: agent.py trimming fix + buffer.py logging.

**Bugs found:**
1. CRITICO: _tool_call uses tc["id"] dict syntax but LangGraph ToolCall objects are not plain dicts — raises KeyError silently caught as tool error
2. CRITICO: expected_ids built with `isinstance(tc, dict)` guard → empty set when tool_calls are ToolCall objects → orphan strip never fires but also never builds correct IDs
3. ALTO: get_products fetches up to 100 products with full description field — no token budget cap before returning to LLM
4. ALTO: send_response raises HTTPStatusError which propagates through _worker but _call_agent catches it silently (logs only), user sees nothing
5. MEDIO: _coerce_messages strips ToolMessages that have no preceding AIMessage with tool_calls but scans ALL preceding messages, not just adjacent ones — can incorrectly drop valid ToolMessages after history trimming

**Why:** Silent failures at tool execution layer → LLM gets ToolMessage with error JSON → LLM confused → no response generated or fallback error message sent.

**How to apply:** When testing tool call flows, always verify tc dict access is compatible with the actual ToolCall type emitted by langchain-openai version in use.
