---
name: project-n8n-fastapi-architecture
description: Odontoking has TWO parallel WhatsApp agents — n8n (legacy, ACTIVE) and FastAPI/LangChain (new). Both are live. n8n sends messages with wrong phone_number_id 1143641718822543.
metadata:
  type: project
---

Two separate agents are simultaneously receiving WhatsApp messages for Odontoking:

1. **n8n workflow** ("Odontoking CLOUD API") — currently ACTIVE in production. Uses its own WhatsApp Trigger (credential "WhatsApp OAuth account 3"), its own LangChain AI Agent (GPT-4o-mini + Postgres memory), and sends responses via hardcoded phone_number_id `1143641718822543` (wrong ID). This is the workflow that actually sends the bad messages.

2. **FastAPI/LangGraph service** (Railway, `odontoking-langchain`) — also receiving webhooks. Uses `WHATSAPP_PHONE_NUMBER_ID=1093139473889218` from Railway env. Tenant config built from env vars via `app/core/tenant.py`. Sends messages using the correct ID.

**Why:** Both systems are subscribed to the same Meta webhook URL (or same WhatsApp Business Account), so when a user sends a message, BOTH workflows fire. n8n was the original system; FastAPI is the replacement that hasn't fully taken over yet.

**Bug source:** The 6 send nodes in n8n (`Send Response`, `Send Response1`, `Send button`, `Send button1`, `Send button2`, `Send List`) have `1143641718822543` hardcoded. This is the old phone number ID from before the Meta app was changed or credentials were rotated.

**How to apply:** When diagnosing WhatsApp message delivery issues, check BOTH systems. The fix requires either: (a) updating all 6 nodes in n8n to use the correct ID, or (b) deactivating the n8n workflow entirely if FastAPI has fully replaced it.

**n8n workflow path:** WhatsApp Trigger → Route Types → Aggregate → Call 'buffer' → If3 → Json Parse2 → AI Agent1 → Code in JavaScript → If → Send Response/Send button2/Send List → Call 'CRM_SOFO_AGENT_ODONTOKING'
