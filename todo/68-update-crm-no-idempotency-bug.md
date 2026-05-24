# No idempotency on update_crm activity creation — LLM retry creates duplicate appointment activities

**Type:** bug
**Severity:** high
**Area:** app/core/langgraph/tools/crm.py

## Problem
`update_crm` has no idempotency key; if the LLM retries the tool call, a duplicate appointment activity is created.

## Impact
Patients get double-booked; CRM noise; reception desk confusion.

## Suggested fix
Pass an idempotency key derived from `(wa_id, appointment_date, service_id)` and have the CRM client refuse duplicates within a window. Cache last successful key in Redis.
