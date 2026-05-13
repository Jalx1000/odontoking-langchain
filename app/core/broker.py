"""Message broker abstraction — Redis Streams backend.

Provides at-least-once delivery for WhatsApp messages:
  1. Webhook publishes to a Redis Stream (XADD) and returns 200 immediately.
  2. Worker process reads with XREADGROUP — message stays pending until XACK.
  3. If the worker crashes, XPENDING allows another worker to reclaim it.
  4. After MAX_RETRIES failures the message moves to the Dead Letter Queue (DLQ).

Stream key format : wa:{tenant_slug}
DLQ key format    : wa:dlq:{tenant_slug}   (Redis List, LPUSH)
Retry counter     : wa:retry:{stream_id}   (Redis String, INCR)

Consumer group    : "workers"  (one group per stream, multiple consumers allowed)
Consumer name     : set via WORKER_CONSUMER_NAME env var or hostname
"""

import json
import os
import socket
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any, Optional, TYPE_CHECKING

from app.core.logging import logger

if TYPE_CHECKING:
    from redis.asyncio import Redis  # pyright: ignore[reportMissingImports]

try:
    from redis.asyncio import Redis
    _REDIS_AVAILABLE = True
except ImportError:
    Redis = None  # type: ignore[assignment,misc]
    _REDIS_AVAILABLE = False

MessageHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]

_STREAM_PREFIX = "wa"
_DLQ_PREFIX = "wa:dlq"
_RETRY_PREFIX = "wa:retry"
_GROUP = "workers"
_MAX_RETRIES = 3
_BLOCK_MS = 5_000       # XREADGROUP block timeout
_CLAIM_MIN_IDLE_MS = 60_000   # reclaim pending messages idle > 60s


def _stream_key(tenant_slug: str) -> str:
    return f"{_STREAM_PREFIX}:{tenant_slug}"


def _dlq_key(tenant_slug: str) -> str:
    return f"{_DLQ_PREFIX}:{tenant_slug}"


def _retry_key(stream_id: str) -> str:
    return f"{_RETRY_PREFIX}:{stream_id}"


class MessageBroker(ABC):
    """Abstract broker interface — swap Redis Streams for RabbitMQ in Phase 5."""

    @abstractmethod
    async def publish(self, tenant_slug: str, wa_id: str, payload: dict[str, Any]) -> None:
        """Publish a message to the tenant's queue."""

    @abstractmethod
    async def consume(self, tenant_slug: str, handler: MessageHandler) -> None:
        """Block and process messages for the tenant. Runs until cancelled."""

    @abstractmethod
    async def setup(self, tenant_slug: str) -> None:
        """Create stream/consumer-group if they do not exist yet."""

    @abstractmethod
    async def dlq_list(self, tenant_slug: str) -> list[dict[str, Any]]:
        """Return all messages currently in the Dead Letter Queue."""

    @abstractmethod
    async def dlq_retry(self, tenant_slug: str, dlq_index: int) -> bool:
        """Re-enqueue a DLQ message by index. Returns True on success."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources."""


class RedisStreamBroker(MessageBroker):
    """Redis Streams implementation of MessageBroker.

    Guarantees at-least-once delivery via consumer groups and explicit ACK.
    """

    def __init__(self, client: "Redis") -> None:
        self._r = client
        self._consumer = os.getenv("WORKER_CONSUMER_NAME", socket.gethostname())

    # ── Publish ─────────────────────────────────────────────────────────────

    async def publish(self, tenant_slug: str, wa_id: str, payload: dict[str, Any]) -> None:
        """XADD — publish a message. Called from the webhook, must be fast."""
        key = _stream_key(tenant_slug)
        entry = {
            "wa_id": wa_id,
            "payload": json.dumps(payload, ensure_ascii=False),
        }
        try:
            await self._r.xadd(key, entry, maxlen=10_000, approximate=True)
            logger.debug("broker_published", tenant=tenant_slug, wa_id=wa_id)
        except Exception as e:
            logger.exception("broker_publish_failed", tenant=tenant_slug, wa_id=wa_id, error=str(e))
            raise

    # ── Setup ────────────────────────────────────────────────────────────────

    async def setup(self, tenant_slug: str) -> None:
        """Create the consumer group (idempotent — safe to call multiple times)."""
        key = _stream_key(tenant_slug)
        try:
            await self._r.xgroup_create(key, _GROUP, id="0", mkstream=True)
            logger.info("broker_group_created", tenant=tenant_slug, group=_GROUP)
        except Exception as e:
            if "BUSYGROUP" in str(e):
                pass  # group already exists — normal on restarts
            else:
                logger.warning("broker_group_create_error", tenant=tenant_slug, error=str(e))

    # ── Consume ─────────────────────────────────────────────────────────────

    async def consume(self, tenant_slug: str, handler: MessageHandler) -> None:
        """Main consume loop. Blocks on XREADGROUP, ACKs on success, DLQs on repeated failure."""
        key = _stream_key(tenant_slug)
        logger.info("broker_consume_started", tenant=tenant_slug, consumer=self._consumer)

        while True:
            try:
                # First: try to reclaim messages idle > 60s (from crashed consumers)
                await self._reclaim_pending(tenant_slug, key, handler)

                # Then: read new messages
                results = await self._r.xreadgroup(
                    _GROUP,
                    self._consumer,
                    {key: ">"},
                    count=10,
                    block=_BLOCK_MS,
                )
                if not results:
                    continue

                for _stream, messages in results:
                    for stream_id, fields in messages:
                        await self._process_entry(tenant_slug, key, stream_id, fields, handler)

            except Exception as e:
                logger.exception("broker_consume_error", tenant=tenant_slug, error=str(e))
                # Brief pause before retrying to avoid tight error loops
                import asyncio
                await asyncio.sleep(2)

    async def _process_entry(
        self,
        tenant_slug: str,
        key: str,
        stream_id: str,
        fields: dict,
        handler: MessageHandler,
    ) -> None:
        """Process one stream entry: call handler, ACK on success, DLQ after max retries."""
        wa_id = fields.get("wa_id", "unknown")
        try:
            payload = json.loads(fields.get("payload", "{}"))
            payload["wa_id"] = wa_id
            await handler(payload)
            await self._r.xack(key, _GROUP, stream_id)
            await self._r.delete(_retry_key(stream_id))
            logger.info("broker_message_acked", tenant=tenant_slug, wa_id=wa_id)
        except Exception as e:
            retries = await self._r.incr(_retry_key(stream_id))
            logger.warning("broker_message_failed", tenant=tenant_slug, wa_id=wa_id, retries=retries, error=str(e))

            if int(retries) >= _MAX_RETRIES:
                await self._send_to_dlq(tenant_slug, key, stream_id, fields, str(e))
            # else: leave in PENDING — will be retried on next reclaim cycle

    async def _reclaim_pending(
        self,
        tenant_slug: str,
        key: str,
        handler: MessageHandler,
    ) -> None:
        """Reclaim pending entries idle longer than _CLAIM_MIN_IDLE_MS."""
        try:
            pending = await self._r.xautoclaim(
                key, _GROUP, self._consumer,
                min_idle_time=_CLAIM_MIN_IDLE_MS,
                start_id="0-0",
                count=10,
            )
            # xautoclaim returns (next_id, messages, deleted_ids)
            messages = pending[1] if isinstance(pending, (list, tuple)) and len(pending) > 1 else []
            for stream_id, fields in messages:
                await self._process_entry(tenant_slug, key, stream_id, fields, handler)
        except Exception as e:
            if "XAUTOCLAIM" not in str(e).upper():
                logger.debug("broker_reclaim_error", tenant=tenant_slug, error=str(e))

    async def _send_to_dlq(
        self,
        tenant_slug: str,
        key: str,
        stream_id: str,
        fields: dict,
        error: str,
    ) -> None:
        """Move a failed entry to the Dead Letter Queue and ACK it from the stream."""
        import time as _time
        dlq_entry = json.dumps({
            "stream_id": stream_id,
            "tenant": tenant_slug,
            "wa_id": fields.get("wa_id"),
            "payload": fields.get("payload"),
            "error": error[:500],
            "failed_at": _time.time(),
        }, ensure_ascii=False)
        await self._r.lpush(_dlq_key(tenant_slug), dlq_entry)
        await self._r.xack(key, _GROUP, stream_id)
        await self._r.delete(_retry_key(stream_id))

        from app.core.notifications import notify
        notify(
            event="broker_message_dlq",
            error=f"tenant={tenant_slug} wa_id={fields.get('wa_id')} err={error[:200]}",
        )
        logger.error("broker_message_moved_to_dlq", tenant=tenant_slug, wa_id=fields.get("wa_id"), error=error[:200])

    # ── DLQ management ───────────────────────────────────────────────────────

    async def dlq_list(self, tenant_slug: str) -> list[dict[str, Any]]:
        raw = await self._r.lrange(_dlq_key(tenant_slug), 0, -1)
        return [json.loads(entry) for entry in (raw or [])]

    async def dlq_retry(self, tenant_slug: str, dlq_index: int) -> bool:
        items = await self._r.lrange(_dlq_key(tenant_slug), 0, -1)
        if dlq_index >= len(items):
            return False
        entry = json.loads(items[dlq_index])
        try:
            await self.publish(tenant_slug, entry["wa_id"], json.loads(entry.get("payload", "{}")))
            # Remove from DLQ (LSET to sentinel then LREM)
            await self._r.lset(_dlq_key(tenant_slug), dlq_index, "__deleted__")
            await self._r.lrem(_dlq_key(tenant_slug), 1, "__deleted__")
            return True
        except Exception as e:
            logger.exception("broker_dlq_retry_failed", tenant=tenant_slug, error=str(e))
            return False

    async def close(self) -> None:
        pass  # Redis client lifecycle managed by caller


class InMemoryBroker(MessageBroker):
    """Fallback broker when Redis is not configured.

    Delegates directly to the MessageBufferService (existing in-process behaviour).
    No persistence — messages are lost if the process crashes.
    """

    async def publish(self, tenant_slug: str, wa_id: str, payload: dict[str, Any]) -> None:
        logger.debug("in_memory_broker_publish", tenant=tenant_slug, wa_id=wa_id)
        # In-memory mode: publishing is a no-op here; the webhook enqueues
        # directly via MessageBufferService before calling publish().

    async def consume(self, tenant_slug: str, handler: MessageHandler) -> None:
        logger.warning("in_memory_broker_no_consume", tenant=tenant_slug)
        # In-memory mode has no separate consume loop — processing is in-process.

    async def setup(self, tenant_slug: str) -> None:
        pass

    async def dlq_list(self, tenant_slug: str) -> list[dict[str, Any]]:
        return []

    async def dlq_retry(self, tenant_slug: str, dlq_index: int) -> bool:
        return False

    async def close(self) -> None:
        pass


def create_broker() -> MessageBroker:
    """Factory: Redis Streams if Valkey is configured, otherwise in-memory fallback."""
    from app.core.config import settings
    if settings.VALKEY_HOST and _REDIS_AVAILABLE:
        client = Redis(
            host=settings.VALKEY_HOST,
            port=settings.VALKEY_PORT,
            db=settings.VALKEY_DB,
            password=settings.VALKEY_PASSWORD or None,
            max_connections=10,
            decode_responses=True,
        )
        logger.info("broker_created", backend="redis_streams")
        return RedisStreamBroker(client)

    logger.info("broker_created", backend="in_memory")
    return InMemoryBroker()


# Singleton for use inside the API process (publish-only path)
broker = create_broker()
