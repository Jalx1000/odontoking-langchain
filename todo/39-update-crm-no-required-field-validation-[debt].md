# update_crm accepts 14 optional kwargs — no validation that required booking fields are present

**Type:** debt
**Severity:** medium
**Area:** app/core/langgraph/tools/crm.py

## Problem
`update_crm` accepts ~14 optional keyword arguments. There is no validation that the combination of provided fields is sufficient for a complete appointment booking (e.g. patient name + service + doctor + datetime are all required together). The LLM can call it with incomplete data and receive a partial success.

## Impact
Incomplete appointments get created in the CRM — missing doctor, missing time, or missing patient name. These are hard to clean up and confuse clinic staff.

## Suggested fix
Add a Pydantic model for the tool input that validates required field groups. If booking an appointment, require `patient_name`, `service_id`, `doctor_id`, and `appointment_datetime` together. Return a clear error message to the LLM if the combination is invalid.
