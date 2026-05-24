# sync_transcript_to_crm reads full chat history with no pagination guard

**Type:** perf
**Severity:** low
**Area:** app/core/langgraph/tools/crm.py

## Problem
`sync_transcript_to_crm` reads the full conversation history and sends it to the CRM. The only guard is `max_messages=50` but for long conversations this is still up to 50 messages × average message size. No streaming or chunking is implemented.

## Impact
For long conversations, this tool call blocks the LangGraph node for seconds while serializing and sending large payloads. Memory usage spikes proportionally to conversation length.

## Suggested fix
Add a character limit on the total transcript size (e.g. 10KB max). Truncate from the beginning of the conversation (keep most recent messages). Log a warning when truncation occurs.
