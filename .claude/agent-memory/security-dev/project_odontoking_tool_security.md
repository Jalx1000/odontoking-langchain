---
name: odontoking-tool-security-review-2026-05
description: Security review of get_doctor_schedule tool and related odontoking.py tools — findings and mitigations
metadata:
  type: project
---

Security review completed 2026-05-25 for `get_doctor_schedule` tool in `app/core/langgraph/tools/odontoking.py`.

**Key findings:**
- `_DOCTOR_DETAIL_HEADERS` used in `_fetch_doctor_detail` is never defined — NameError at runtime (critical bug, not just security)
- No input bounds validation on `id_doctor` (negative/zero values accepted)
- Retry decorator (`@retry` from tenacity) is imported but **not applied** to `get_doctor_schedule` — the spec called for 429/5xx retry, helper `_is_retryable_slots_error` exists but goes unused
- Doctor schedule response is passed raw to the LLM without PII filtering (patient slot data, doctor name)
- `get_doctors` and `get_disponibilidad` have no input validation either — pattern is consistent (no validation anywhere)
- `ODONTOKING_API_URL` has a hardcoded default `https://odontoking.sofopolis.com` — SSRF via env var is low risk given it's operator-controlled
- CRM endpoint for `doctors/{id}/slots` is unauthenticated; doctor ID enumeration is realistic via the WhatsApp bot

**Why:** Understanding recurring patterns helps future reviews know what to look for in new tools.
**How to apply:** Any new tool added to `_ODONTOKING_TOOLS` should be checked for: input bounds, `_DOCTOR_DETAIL_HEADERS`-style undefined references, retry decoration, and PII in responses.
