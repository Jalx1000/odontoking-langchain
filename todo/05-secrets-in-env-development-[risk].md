# Secrets committed in .env.development

**Type:** risk
**Severity:** critical
**Area:** .env.development

## Problem
`.env.development` contains real credentials: OpenAI API key, WhatsApp access token, JWT secret, Langfuse keys, and CRM API token. This file is tracked in git and visible to anyone with repo access.

## Impact
Any credential leak from the repo exposes production services. The WhatsApp token grants full control over the Odontoking WhatsApp number. The OpenAI key can incur unbounded charges.

## Suggested fix
Rotate all exposed credentials immediately. Add `.env.development` to `.gitignore` if not already there. Use `.env.example` with placeholder values as the only committed env file. Consider using git-secrets or detect-secrets pre-commit hook (`.secrets.baseline` already exists — ensure it's enforced).
