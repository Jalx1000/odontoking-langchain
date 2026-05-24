# scripts/set_env.sh not in PATH check — make migrate fails confusingly if missing

**Type:** debt
**Severity:** low
**Area:** scripts/set_env.sh

## Problem
`make migrate` sources `scripts/set_env.sh` with no preflight check; if file is missing the error is opaque.

## Impact
Frustrating onboarding; "works on my machine" reports.

## Suggested fix
Add a Makefile guard: `@test -f scripts/set_env.sh || { echo "missing scripts/set_env.sh — copy from .example"; exit 1; }`.
