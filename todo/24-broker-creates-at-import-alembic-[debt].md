# broker = create_broker() at module load causes reconnect spam in alembic runs

**Type:** debt
**Severity:** low
**Area:** app/core/broker.py

## Problem
`broker = create_broker()` runs at the bottom of `broker.py` at module import. Any script that imports anything from the app (including `alembic/env.py`) triggers broker initialization and connection attempts. When RabbitMQ or Redis is not available (e.g. during `make migrate`), this produces connection error spam in the logs.

## Impact
Migration logs are polluted with broker connection errors. `make migrate` is confusing to run in environments without a running broker.

## Suggested fix
Use lazy initialization: return the broker instance from a `get_broker()` function with `lru_cache`, only called when a request actually needs it.
