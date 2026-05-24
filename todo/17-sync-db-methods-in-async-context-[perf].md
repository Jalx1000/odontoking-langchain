# create_user/get_user/get_session declared async def but use sync SQLModel Session

**Type:** perf
**Severity:** high
**Area:** app/services/database.py

## Problem
Methods like `create_user`, `get_user`, `get_session` are declared `async def` but internally use synchronous SQLModel `Session` calls. The `async def` declaration is misleading — these methods block the event loop during every database call.

## Impact
Under load, database calls block the FastAPI event loop, preventing other requests from being processed. Response times degrade non-linearly with concurrent users.

## Suggested fix
Either: (a) convert to true async using `asyncpg` or `sqlalchemy[asyncio]`, or (b) keep them as sync `def` and call them via `await asyncio.to_thread(...)` at the call site. Option (b) is the lower-risk change given the current codebase.
