---
name: e2e-test-strategy
description: E2E test harness design for Odontoking — posts synthetic webhook payloads to Railway, reads agent replies via admin conversations API
metadata:
  type: project
---

E2E conversation tests for Odontoking on Railway use a black-box harness in `tests/e2e_railway/` that POSTs synthetic Meta webhook payloads to the live tenant endpoint and polls the admin conversations API to read agent replies. We do NOT intercept Meta egress.

**Why:** the admin endpoint reads `ChatHistoryOdonto` which the agent writes synchronously per turn — it's the ground truth of what the agent said, regardless of whether Meta delivery succeeded. Cleaner than mocking the WhatsApp client or scraping LangGraph checkpoints.

**How to apply:**
- Test trigger: `POST /api/v1/whatsapp/odontoking/webhook` with unique `msg.id` per turn (avoids dedup cache).
- Wait strategy after each user turn: sleep `BUFFER_WINDOW_SECONDS + 1.5s`, then poll `GET /api/v1/admin/tenants/odontoking/conversations/{wa_id}` (X-Admin-Key header) at 2s until latest message is `role=assistant` with `created_at > t_send`. 90s ceiling per turn.
- Three pass criteria: (1) `update_crm` tool was called — requires extending `_parse_message` in `app/api/admin/conversations.py` to surface `tool_names: list[str]`; (2) real CRM has matching person+lead+activity; (3) final assistant message contains confirmation keyword + date + patient name.
- Tests gated by `RUN_E2E_RAILWAY=1` env var; never run in default `uv run pytest`.
- Pre/post cleanup uses existing `DELETE /api/v1/whatsapp/odontoking/history/{wa_id}` which clears both ChatHistory and LangGraph checkpoints.
- Security concern flagged: webhook does not validate Meta `X-Hub-Signature-256`. Recommended mitigation is a separate `odontoking-test` tenant rather than leaving the prod tenant signature-less.

Related: [[broker-wire-format]] (for future external-agent E2E variant), [[webhook-routing-rule]].
