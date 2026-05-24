# version: '3.8' in docker-compose.yml is obsolete

**Type:** infra
**Severity:** low
**Area:** docker-compose.yml

## Problem
`docker-compose.yml` declares `version: '3.8'` at the top. Modern Docker Compose ignores this field and emits a warning on every `docker-compose` invocation: "the attribute `version` is obsolete, it will be ignored".

## Impact
Warning noise on every `make stack-up`, `make dev`, `make infra-up`. No functional impact.

## Suggested fix
Remove the `version:` line entirely from `docker-compose.yml`.
