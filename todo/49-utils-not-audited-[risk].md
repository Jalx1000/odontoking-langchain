# process_llm_response and dump_messages in app/utils not audited

**Type:** risk
**Severity:** low
**Area:** app/utils/

## Problem
`process_llm_response` and `dump_messages` are imported and used in the core graph flow but were not reviewed during this audit. They may silently drop `tool_call_id` fields or content blocks, which would cause LangChain to raise errors on tool call/response pairs.

## Impact
Unknown — could cause silent message drops or LangChain validation errors on tool-heavy conversations.

## Suggested fix
Audit `app/utils/` fully. Specifically verify: (1) `tool_call_id` is preserved in all message transformations, (2) multi-content messages (text + tool_call) are not truncated to single content, (3) add unit tests for each utility function.
