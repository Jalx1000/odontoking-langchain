# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Additional Commands

```bash
# Database migrations (requires APP_ENV to be set via scripts/set_env.sh)
make migrate                     # Apply all pending migrations
make migration MSG="description" # Generate a new migration from model changes
make migrate-downgrade           # Roll back the last migration

# Docker alternatives
make docker-up                   # Start only API + DB (no monitoring stack)
make stack-up ENV=development    # Full stack including Prometheus + Grafana

# Run tests (no Makefile target — use directly)
uv run pytest                    # Run all tests
uv run pytest tests/path/test_file.py::test_name  # Run a single test
```

## Architecture: How the Pieces Connect

### Request flow

`FastAPI route` → `LangGraphAgent.get_response()` (or `get_stream_response()`) → compiled `StateGraph` → two nodes: **`chat`** (LLM call) and **`tool_call`** (tool execution). The `chat` node routes to `tool_call` when the LLM returns tool calls, otherwise routes to `END`. `tool_call` always routes back to `chat`.

The `LangGraphAgent` singleton is instantiated at module level in `app/api/v1/chatbot.py` and pre-warmed in the FastAPI lifespan (`app/main.py`).

### Two separate database connections

This project uses two distinct database connection strategies — do not mix them:

1. **SQLModel sync engine** (`app/services/database.py` → `DatabaseService`) — used for user and session CRUD (SQLModel `Session` / `create_engine`). Despite `async def` signatures, the underlying engine is synchronous.

2. **asyncpg connection pool** (`app/core/langgraph/graph.py` → `LangGraphAgent._connection_pool`) — used exclusively for LangGraph's `AsyncPostgresSaver` checkpointing. Built with `AsyncConnectionPool` from `psycopg_pool`.

### LLM service structure

`app/services/llm/` is split into two modules:
- `registry.py` — `LLMRegistry`: ordered list of available models; edit this to add/reorder models
- `service.py` — `LLMService`: call logic, tenacity retries, circular model fallback, structured output support

The service retries on rate limit / timeout errors, then falls back to the next model in registry order. A global `LLM_TOTAL_TIMEOUT` budget (default 60s) caps the entire retry+fallback loop.

### Memory and caching

`MemoryService` (`app/services/memory.py`) wraps mem0's `AsyncMemory` with a cache layer. Before querying pgvector, it checks the cache; results are stored on success only.

Cache backend is chosen at startup (`app/core/cache.py`): **Valkey/Redis** if `VALKEY_HOST` is set and the `redis` optional dependency is installed, otherwise **in-memory TTL**. Install with `uv add redis --optional cache` to enable the distributed backend.

During chat, a state-check and a memory search run concurrently via `asyncio.gather` to avoid sequential latency before each LLM call.

### System prompts

Prompts are markdown files in `app/core/prompts/`: `system.md` is the main agent prompt, `session_title.md` is used by `SessionNamingService`. Load them with `load_system_prompt()` from `app/core/prompts/__init__.py`.

### LangGraph interrupts

`ask_human` tool (`app/core/langgraph/tools/ask_human.py`) uses `NodeInterrupt` to pause the graph and return a question to the user. On the next request, if `state.next` is set, the graph is resumed with `Command(resume=<new_message>)` instead of a fresh invocation.

### Alembic migrations

Models use SQLModel. After changing a model in `app/models/`, run `make migration MSG="..."` to autogenerate a revision, then `make migrate` to apply it. The alembic env (`alembic/env.py`) imports all SQLModel metadata.
