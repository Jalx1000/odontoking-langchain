# clear_history admin route has no authentication — anyone can wipe a patient's chat history

**Type:** bug
**Severity:** critical
**Area:** app/api/v1/whatsapp.py

## Problem
The `DELETE /api/v1/whatsapp/odontoking/history/{wa_id}` endpoint has no authentication or authorization check. Any HTTP client that knows the URL can delete a patient's entire conversation history and LangGraph checkpoints.

## Impact
Malicious or accidental calls permanently destroy patient conversation context. An attacker can wipe all patient histories systematically. GDPR/data-protection incident if patient data is destroyed without authorization.

## Suggested fix
Add `Depends(require_admin)` (same pattern as other admin routes) to the clear_history endpoint. Move it to the `/api/v1/admin/` router where it belongs alongside other admin operations.
