# Two Postgres services in compose — db has broken dev role, is default depends_on for app

**Type:** config
**Severity:** high
**Area:** docker-compose.yml

## Problem
`docker-compose.yml` has two PostgreSQL services: `db` (original, port 5432) and `db-dev` (new, port 5434, user=dev). The `app` service still has `depends_on: db` pointing to the broken original. The `db` volume was initialized without the `dev` role so the app fails to connect when using the stack.

## Impact
`make stack-up` starts the app pointing to a broken database. Only `make dev` (which uses `db-dev` directly) works correctly.

## Suggested fix
Update `app` service `depends_on` in `docker-compose.yml` to reference `db-dev`. Long-term: consolidate to a single Postgres service with the correct credentials and remove `db` once its volume is no longer needed.
