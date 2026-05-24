# _handle_webhook_payload always returns ok even on parse failure — masks bugs

**Type:** debt
**Severity:** low
**Area:** app/api/v1/whatsapp.py

## Problem
`_handle_webhook_payload` returns `{"status": "ok"}` for all outcomes, including parse failures and unsupported message types. This is correct for Meta (which would retry on non-200), but means bugs in payload parsing are invisible without checking logs.

## Impact
Parse errors silently succeed from Meta's perspective. Without a Prometheus counter or structured log metric, parse failure rate is unobservable.

## Suggested fix
Add a Prometheus counter `whatsapp_webhook_parse_errors_total` incremented on each parse failure. Keep returning 200 to Meta, but make the error observable via metrics dashboard.
