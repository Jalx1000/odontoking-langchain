# crm.py uses module-level _HEADERS with settings.ODONTOKING_API_TOKEN — multi-tenant blocker

**Type:** bug
**Severity:** critical
**Area:** app/core/langgraph/tools/crm.py

## Problem
Same issue as #21: `crm.py` defines `_HEADERS` and `_BASE` at module level using `settings.ODONTOKING_API_TOKEN`. All CRM operations (update_crm, sync_transcript_to_crm, verify_insurance) use the same hardcoded token.

## Impact
Any second tenant using CRM tools would write to Odontoking's CRM. Patient appointments and data would be mixed across tenants.

## Suggested fix
Same as #21: pass tenant CRM credentials through the tool call context. Use a `contextvars.ContextVar` set per request, or pass credentials explicitly as tool parameters.
