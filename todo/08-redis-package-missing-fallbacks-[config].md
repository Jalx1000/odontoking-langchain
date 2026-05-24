# redis package not installed — rate limiter, broker and cache fall back to in-memory

**Type:** config
**Severity:** high
**Area:** pyproject.toml

## Problem
`VALKEY_HOST` is set in `.env.development` pointing to the local Valkey container, but the `redis` Python package is not installed (it's an optional dep). At startup: rate limiter falls back to in-memory, cache falls back to in-memory. Broker uses RabbitMQ so it's unaffected, but the intent was to use Valkey.

## Impact
Rate limits are per-process not shared. Cache misses on every request (no cross-process caching). In a multi-replica deploy, each replica has independent rate limit counters.

## Suggested fix
Run `uv add redis --optional cache` and install the optional group: `uv sync --extra cache`. Add a startup check that logs a clear error (not just a warning) if `VALKEY_HOST` is set but the redis client is unavailable.
