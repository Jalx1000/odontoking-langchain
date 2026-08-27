"""This file contains the main application entry point."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    Request,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from asgi_correlation_id import CorrelationIdMiddleware

from app.api.admin.router import admin_router
from app.api.v1.api import api_router
from app.api.v1.chatbot import agent
from app.core.broker import broker
from app.core.cache import cache_service
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.core.metrics import setup_metrics
from app.core.middleware import (
    LoggingContextMiddleware,
    MetricsMiddleware,
    ProfilingMiddleware,
)
from app.core.observability import langfuse_init
from app.core.langgraph.c21_graph import c21_agent
from app.services.database import database_service
from app.services.memory import memory_service
from app.services.message_buffer import message_buffer_service

# Load environment variables
load_dotenv()
langfuse_init()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown events."""
    logger.info(
        "application_startup",
        project_name=settings.PROJECT_NAME,
        version=settings.VERSION,
        api_prefix=settings.API_V1_STR,
    )

    # Initialize cache service (connects to Valkey if configured)
    try:
        await cache_service.initialize()
    except Exception as e:
        logger.exception("cache_initialization_failed", error=str(e))

    # Pre-warm the LangGraph agent: create graph + connection pool at startup
    # to avoid cold-start latency on the first request
    try:
        await agent.create_graph()
        await c21_agent.create_graph()
        logger.info("graph_pre_warmed")
    except Exception as e:
        logger.exception("graph_pre_warm_failed", error=str(e))

    # Pre-warm mem0 AsyncMemory only when memory is enabled (requires pgvector extension)
    if settings.ODONTOKING_MEMORY_ENABLED:
        try:
            await memory_service.initialize()
        except Exception as e:
            logger.exception("memory_service_pre_warm_failed", error=str(e))

    # Initialize message buffer (connects to Redis if VALKEY_HOST is configured)
    try:
        await message_buffer_service.initialize()
    except Exception as e:
        logger.exception("message_buffer_initialization_failed", error=str(e))

    # Recover orphaned buffer messages — look up each wa_id's tenant from DB
    # so the correct credentials are used when re-sending. Falls back to a
    # no-op (drops the message) when the tenant can't be resolved, to avoid
    # replying from the wrong phone number (multi-tenant safety).
    try:
        async def _recover_fn(wa_id: str, text: str) -> None:
            logger.warning("buffer_recovery_dropped", wa_id=wa_id, reason="tenant_unknown")

        await message_buffer_service.recover(_recover_fn)
    except Exception as e:
        logger.exception("message_buffer_recovery_failed", error=str(e))

    yield

    # Cleanup on shutdown — order matters: buffer first (drains workers), then agents
    await message_buffer_service.close()
    await c21_agent.close()  # awaits pending persist tasks + closes pool
    await cache_service.close()
    try:
        await broker.close()
    except Exception as e:
        logger.exception("broker_close_failed", error=str(e))
    if agent._connection_pool:
        await agent._connection_pool.close()
        logger.info("connection_pool_closed")
    logger.info("application_shutdown")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# Set up Prometheus metrics
setup_metrics(app)

# Add logging context middleware (must be added before other middleware to capture context)
app.add_middleware(LoggingContextMiddleware)

# Add custom metrics middleware
app.add_middleware(MetricsMiddleware)

# Add profiling middleware (DEBUG only — saves HTML to /tmp on slow requests)
if settings.DEBUG:
    app.add_middleware(ProfilingMiddleware)

# Add correlation ID middleware — must be outermost so request_id is set before all others
app.add_middleware(CorrelationIdMiddleware)

# Set up rate limiter exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]


# Add validation exception handler
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors from request data.

    Args:
        request: The request that caused the validation error
        exc: The validation error

    Returns:
        JSONResponse: A formatted error response
    """
    # Log the validation error
    logger.error(
        "validation_error",
        client_host=request.client.host if request.client else "unknown",
        path=request.url.path,
        errors=str(exc.errors()),
    )

    # Format the errors to be more user-friendly
    formatted_errors = []
    for error in exc.errors():
        loc = " -> ".join([str(loc_part) for loc_part in error["loc"] if loc_part != "body"])
        formatted_errors.append({"field": loc, "message": error["msg"]})

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Validation error", "errors": formatted_errors},
    )


# Set up CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(api_router, prefix=settings.API_V1_STR)

# Include Admin router (protected by X-Admin-Key header)
app.include_router(admin_router, prefix=settings.API_V1_STR)


@app.get("/")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["root"][0])
async def root(request: Request):
    """Root endpoint returning basic API information."""
    logger.info("root_endpoint_called")
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "healthy",
        "environment": settings.ENVIRONMENT.value,
        "swagger_url": "/docs",
        "redoc_url": "/redoc",
    }


@app.get("/health")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["health"][0])
async def health_check(request: Request) -> JSONResponse:
    """Health check endpoint with environment-specific information.

    Returns:
        JSONResponse: Health status payload, with HTTP 503 when the
        database is unreachable so load balancers can drop the instance.
    """
    logger.info("health_check_called")

    # Check all components concurrently
    db_healthy, cache_healthy = await asyncio.gather(
        database_service.health_check(),
        cache_service.health_check(),
        return_exceptions=True,
    )

    db_ok = db_healthy is True
    cache_ok = cache_healthy is True

    # Buffer is healthy if it has a backend initialized
    buffer_ok = message_buffer_service._backend is not None

    overall_healthy = db_ok and buffer_ok
    overall_status = "healthy" if overall_healthy else "degraded"

    response = {
        "status": overall_status,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT.value,
        "components": {
            "api": "healthy",
            "database": "healthy" if db_ok else "unhealthy",
            "cache": "healthy" if cache_ok else "unhealthy",
            "buffer": "healthy" if buffer_ok else "not_initialized",
        },
        "timestamp": datetime.now().isoformat(),
    }

    status_code = status.HTTP_200_OK if overall_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=response, status_code=status_code)
