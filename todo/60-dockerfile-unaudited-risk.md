# Dockerfile not audited — confirm Python 3.13 pinned and runs as non-root

**Type:** risk
**Severity:** low
**Area:** Dockerfile

## Problem
The Dockerfile hasn't been verified for: Python 3.13 pin, non-root user, multi-stage cleanup, no leaked env files.

## Impact
Inconsistent local/CI/prod images; security smell.

## Suggested fix
Audit the Dockerfile; pin Python; add `USER app`; verify `.dockerignore` excludes `.env*` and `tests/`.
