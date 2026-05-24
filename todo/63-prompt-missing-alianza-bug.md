# odontoking.md prompt missing Alianza in verify_insurance allowed values

**Type:** bug
**Severity:** medium
**Area:** app/core/prompts/odontoking.md

## Problem
The prompt's allowed insurance list omits Alianza but the code accepts it.

## Impact
LLM refuses Alianza requests despite the system supporting them.

## Suggested fix
Generate the prompt's insurance list from `_INSURANCE_ID_MAP` keys (template render at startup), eliminating drift.
