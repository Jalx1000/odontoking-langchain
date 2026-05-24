# LangGraphAgent singleton in chatbot.py runs at import

**Type:** debt
**Severity:** medium
**Area:** app/api/v1/chatbot.py

## Problem
`agent = LangGraphAgent()` is executed at module level in `chatbot.py`. This initializes the LangGraph agent, connects to PostgreSQL, and sets up the checkpoint saver at import time — before the FastAPI lifespan has started.

## Impact
Any test that imports the chatbot module triggers a database connection attempt. Import-time failures (missing env vars, DB not ready) produce confusing errors with no clear stack trace.

## Suggested fix
Move `LangGraphAgent` instantiation into the FastAPI lifespan `startup` event. Store the instance in `app.state.agent` and inject via `Depends`.
