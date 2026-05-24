# Passwords with @ or : break SQLModel connection string but not LangGraph

**Type:** bug
**Severity:** high
**Area:** app/core/langgraph/graph.py

## Problem
`OdontokingAgent._get_connection_pool` applies `quote_plus(POSTGRES_PASSWORD)` when building the asyncpg DSN. `DatabaseService` (SQLModel) builds its connection string without URL-encoding the password. If `POSTGRES_PASSWORD` contains `@` or `:`, SQLModel fails to parse the DSN while LangGraph works fine.

## Impact
Any password with special characters causes `DatabaseService` to fail at startup with a cryptic DSN parse error. This silently enforces a password character restriction that is not documented anywhere.

## Suggested fix
Apply `urllib.parse.quote_plus` consistently in both connection string builders. Add a note to `.env.example` that passwords with special characters are supported. Add a startup test with a mock connection string containing `@` to verify both builders handle it.
