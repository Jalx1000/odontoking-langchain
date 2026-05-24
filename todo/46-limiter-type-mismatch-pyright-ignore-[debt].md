# Limiter default_limits type mismatch masked by pyright-ignore

**Type:** debt
**Severity:** low
**Area:** app/core/limiter.py

## Problem
`Limiter` `default_limits` is typed as `list[str]` in the code but slowapi may expect a comma-separated string. A `# type: ignore` comment suppresses the pyright error, hiding a potential runtime type mismatch.

## Impact
If slowapi receives the wrong type, rate limits silently do not apply. The `# type: ignore` prevents this from being caught by `make typecheck`.

## Suggested fix
Check slowapi docs for the correct type of `default_limits`. Remove the `# type: ignore` and fix the type to match. Add a startup integration test that verifies rate limiting actually triggers.
