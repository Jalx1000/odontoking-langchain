# No migration safety check — make migrate-downgrade untested on latest revision

**Type:** risk
**Severity:** medium
**Area:** alembic/

## Problem
There is no CI check or documented procedure that tests `make migrate-downgrade` on the latest Alembic revision. The `agent_endpoint_url` column addition (latest revision) has not been verified to be reversible.

## Impact
If a bad migration is deployed and needs to be rolled back, `make migrate-downgrade` may fail or corrupt the database schema. Emergency rollback becomes unavailable.

## Suggested fix
Add a CI step that runs `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` on a test database. Run this on every PR that modifies `alembic/versions/`.
