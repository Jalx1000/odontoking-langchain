"""Standalone worker process — consumes messages from Redis Streams and invokes the agent.

Run via:
    python -m app.worker

Required environment variables:
    WORKER_TENANT   — tenant slug to serve (e.g. "odontoking", "kohlberg")
    APP_ENV         — environment (development / staging / production)
    + all standard app env vars (Postgres, Redis, OpenAI, WhatsApp, etc.)

In Railway, deploy one service per tenant:
    service worker-odontoking: WORKER_TENANT=odontoking
    service worker-kohlberg:   WORKER_TENANT=kohlberg  (replicas=2)
"""

import asyncio
import os
import signal
import sys
import time
from hashlib import sha256

# Resolve env file before importing any app module
_APP_ENV = os.getenv("APP_ENV", "development")

from app.core.broker import RedisStreamBroker, create_broker
from app.core.config import settings
from app.core.logging import logger
from app.core.tenant import get_tenant
from app.schemas import Message
from app.services.gateway import Destination, get_gateway

# ── Agent registry ──────────────────────────────────────────────────────────
# Phase 3 will replace this with a BaseAgent factory.
def _build_agent_registry():
    from app.core.langgraph.imprimir_graph import ImprimirAgent
    agent = ImprimirAgent()
    return {
        "odontoking": agent,
        "imprimir": agent,
    }


# ── Message handler ──────────────────────────────────────────────────────────

async def _handle_message(payload: dict, agent, tenant_slug: str) -> None:
    """Process one buffered message: call agent, send WhatsApp reply."""
    wa_id = payload.get("wa_id", "")
    text = payload.get("text", "")
    message_id = payload.get("message_id", "")
    patient_ctx = payload.get("patient_ctx") or {}

    if not wa_id or not text:
        logger.warning("worker_invalid_payload", tenant=tenant_slug, payload=str(payload)[:200])
        return

    messages = [Message(role="user", content=text)]
    turn_id = sha256(f"{tenant_slug}:{wa_id}:{message_id}:{time.monotonic_ns()}:{text}".encode()).hexdigest()[:16]
    gateway = get_gateway()
    dest = Destination(wa_id=wa_id)
    agent_task: asyncio.Task | None = None
    try:
        asyncio.create_task(gateway.send_typing(dest))
        logger.info(
            "worker_agent_turn_started",
            tenant=tenant_slug,
            wa_id=wa_id,
            message_id=message_id,
            turn_id=turn_id,
            route="worker_broker",
            text_preview=text[:120],
        )
        agent_task = asyncio.create_task(
            agent.get_response(
                messages,
                wa_id,
                nombre_registrado=patient_ctx.get("nombre_registrado"),
                nombre_whatsapp=patient_ctx.get("nombre_whatsapp"),
            )
        )
        try:
            response_text = await asyncio.wait_for(
                asyncio.shield(agent_task),
                timeout=settings.LLM_TOTAL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("worker_agent_soft_timeout", tenant=tenant_slug, wa_id=wa_id, turn_id=turn_id)
            await gateway.send_text(
                dest,
                "La consulta está tardando un poco más de lo normal. Sigo revisando y le responderé en este mismo chat en un momento 🙏.",
            )
            response_text = await asyncio.wait_for(
                asyncio.shield(agent_task),
                timeout=settings.LLM_TOTAL_TIMEOUT + 30,
            )
        await gateway.send_response(dest, response_text)
        logger.info(
            "worker_response_sent",
            tenant=tenant_slug,
            wa_id=wa_id,
            message_id=message_id,
            turn_id=turn_id,
            route="worker_broker",
            preview=response_text[:120],
        )
    except asyncio.TimeoutError:
        logger.warning("worker_agent_hard_timeout", tenant=tenant_slug, wa_id=wa_id, turn_id=turn_id)
        if agent_task is not None:
            agent_task.cancel()
        try:
            await gateway.send_text(
                dest,
                "Disculpe, la consulta no pudo completarse en este momento. Por favor intente de nuevo en unos minutos 🙏.",
            )
        except Exception:
            pass
    except Exception as e:
        logger.exception("worker_agent_error", tenant=tenant_slug, wa_id=wa_id, turn_id=turn_id, error=str(e))
        try:
            await gateway.send_text(dest, "Disculpe, ocurrió un error. Por favor intente de nuevo en un momento 🙏.")
        except Exception:
            pass


# ── Main ─────────────────────────────────────────────────────────────────────

async def run_worker(tenant_slug: str) -> None:
    tenant = get_tenant(tenant_slug)
    if tenant is None:
        logger.error("worker_unknown_tenant", tenant=tenant_slug)
        sys.exit(1)

    logger.info("worker_starting", tenant=tenant_slug, environment=settings.ENVIRONMENT.value)

    # Build agent for this tenant
    registry = _build_agent_registry()
    agent = registry.get(tenant_slug)
    if agent is None:
        logger.error("worker_no_agent", tenant=tenant_slug)
        sys.exit(1)

    # Warm up the agent graph (creates Postgres connection pool + LangGraph graph)
    try:
        await agent.create_graph()
        logger.info("worker_agent_warmed", tenant=tenant_slug)
    except Exception as e:
        logger.exception("worker_agent_warmup_failed", tenant=tenant_slug, error=str(e))
        sys.exit(1)

    # Create broker and ensure consumer group exists
    the_broker = create_broker()
    await the_broker.setup(tenant_slug)

    if not isinstance(the_broker, RedisStreamBroker):
        logger.error("worker_requires_redis", tenant=tenant_slug)
        sys.exit(1)

    # Handler closure captures agent and tenant_slug
    async def handler(payload: dict) -> None:
        await _handle_message(payload, agent, tenant_slug)

    # Graceful shutdown on SIGTERM / SIGINT
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown(sig: signal.Signals) -> None:
        logger.info("worker_shutdown_signal", signal=sig.name, tenant=tenant_slug)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown, sig)

    consume_task = asyncio.create_task(the_broker.consume(tenant_slug, handler))

    await stop_event.wait()
    consume_task.cancel()
    try:
        await consume_task
    except asyncio.CancelledError:
        pass

    await agent.close()
    await the_broker.close()
    logger.info("worker_stopped", tenant=tenant_slug)


def main() -> None:
    tenant_slug = os.getenv("WORKER_TENANT", "").strip()
    if not tenant_slug:
        print("ERROR: WORKER_TENANT environment variable is required.", file=sys.stderr)
        print("Example: WORKER_TENANT=odontoking python -m app.worker", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run_worker(tenant_slug))


if __name__ == "__main__":
    main()
