# update_crm _pick_agent_user is random — non-deterministic CRM assignments

**Type:** debt
**Severity:** low
**Area:** app/core/langgraph/tools/crm.py

## Problem
`_pick_agent_user` selects a CRM agent user randomly from the available list. This means the same patient may be assigned to different agents across sessions, making it impossible to track patient ownership in the CRM.

## Impact
CRM reports show random agent distribution. Follow-up workflows break if a specific agent needs to be the owner of a patient record.

## Suggested fix
Use a deterministic assignment strategy: hash `wa_id` modulo the number of available agents, or always assign to the same dedicated "bot agent" user for WhatsApp interactions.
