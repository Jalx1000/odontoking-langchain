# update_crm HTTP 422 returns soft error the LLM may misinterpret as success

**Type:** debt
**Severity:** medium
**Area:** app/core/langgraph/tools/crm.py

## Problem
When the CRM returns HTTP 422, `update_crm` returns a JSON with `appointment_registered=False` without a clear retry signal. The LLM may read this as a partial success and tell the patient the appointment was booked.

## Impact
Patients receive confirmation of an appointment that was never actually created in the CRM. Clinic staff see no appointment on their end.

## Suggested fix
On HTTP 422, return a structured error that clearly instructs the LLM to NOT confirm the appointment: `{"error": "appointment_not_created", "reason": "...", "action": "inform_patient_and_retry"}`. Include specific retry instructions in the tool docstring.
