# SHAREMEDATA_API_KEY looks like bcrypt hash not API key

**Type:** config
**Severity:** high
**Area:** .env.development

## Problem
`SHAREMEDATA_API_KEY` in `.env.development` starts with `$2a$08$` which is a bcrypt hash prefix, not a valid API key format. This suggests a copy-paste error where a hashed password was placed instead of the actual API key.

## Impact
All calls to the Sharemedata calendar API will fail with 401 or 403. Appointment availability checks that depend on this service are broken in development.

## Suggested fix
Obtain the real API key from Sharemedata and replace the value in `.env.development`. Add a config validator in `app/core/config.py` that warns at startup if `SHAREMEDATA_API_KEY` matches the bcrypt pattern `$2[ab]$`.
