"""Message broker abstraction — Redis Streams (Fase 2) and RabbitMQ (Fase 5) backends.

Provides at-least-once delivery for WhatsApp messages:
  1. Webhook publishes to the broker and returns 200 immediately.
  2. Worker process reads messages — they stay pending until explicitly ACKed.
  3. If the worker crashes, another worker reclaims pending messages.
  4. After MAX_RETRIES failures the message moves to the Dead Letter Queue (DLQ).

Backend priority (create_broker factory):
  RabbitMQ (RABBITMQ_URL set + aio-pika installed)
    → Redis Streams (VALKEY_HOST set + redis installed)
    → InMemory fallback

Redis Streams keys:
  Stream      : wa:{tenant_slug}
  DLQ         : wa:dlq:{tenant_slug}   (Redis List, LPUSH)
  Retry count : wa:retry:{stream_id}   (Redis String, INCR)
  Consumer group: "workers"

RabbitMQ topology (per tenant):
  Exchange    : wa.{tenant_slug}         (direct, durable)
  Queue       : wa.{tenant_slug}.messages (durable, x-dead-letter-exchange)
  DLX         : wa.{tenant_slug}.dlx     (direct, durable)
  DLQ queue   : wa.{tenant_slug}.dlq     (durable)
  DLQ in-mem  : broker._dlq dict        (lost on restart — email alert compensates)
"""

import asyncio
import json
import os
import socket
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any, Optional, TYPE_CHECKING

from app.core.logging import logger

if TYPE_CHECKING:
    from redis.asyncio import Redis  # pyright: ignore[reportMissingImports]
    import aio_pika as _aio_pika  # pyright: ignore[reportMissingImports]

try:
    from redis.asyncio import Redis
    _REDIS_AVAILABLE = True
except ImportError:
    Redis = None  # type: ignore[assignment,misc]
    _REDIS_AVAILABLE = False

try:
    import aio_pika  # pyright: ignore[reportMissingImports]
    _RABBITMQ_AVAILABLE = True
except ImportError:
    aio_pika = None  # type: ignore[assignment]
    _RABBITMQ_AVAILABLE = False

MessageHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]

# ── Redis Streams constants ───────────────────────────────────────────────────
_STREAM_PREFIX = "wa"
_DLQ_PREFIX = "wa:dlq"
_RETRY_PREFIX = "wa:retry"
_GROUP = "workers"
_MAX_RETRIES = 3
_BLOCK_MS = 5_000
_CLAIM_MIN_IDLE_MS = 60_000


def _stream_key(tenant_slug: str) -> str:
    return f"{_STREAM_PREFIX}:{tenant_slug}"


def _dlq_key(tenant_slug: str) -> str:
    return f"{_DLQ_PREFIX}:{tenant_slug}"


def _retry_key(stream_id: str) -> str:
    return f"{_RETRY_PREFIX}:{stream_id}"


# ── Abstract interface ────────────────────────────────────────────────────────

class MessageBroker(ABC):
    """Abstract broker interface — swap implementations without changing worker code."""

    @abstractmethod
    async def publish(self, tenant_slug: str, wa_id: str, payload: dict[str, Any]) -> None:
        """Publish a message to the tenant's queue."""

    @abstractmethod
    async def consume(self, tenant_slug: str, handler: MessageHandler) -> None:
        """Block and process messages for the tenant. Runs until cancelled."""

    @abstractmethod
    async def setup(self, tenant_slug: str) -> None:
        """Create exchange/queue/consumer-group if they do not exist yet."""

    @abstractmethod
    async def dlq_list(self, tenant_slug: str) -> list[dict[str, Any]]:
        """Return all messages currently in the Dead Letter Queue."""

    @abstractmethod
    async def dlq_retry(self, tenant_slug: str, dlq_index: int) -> bool:
        """Re-enqueue a DLQ message by index. Returns True on success."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources."""


# ── Redis Streams implementation ──────────────────────────────────────────────

class RedisStreamBroker(MessageBroker):
    """Redis Streams implementation of MessageBroker.

    Guarantees at-least-once delivery via consumer groups and explicit ACK.
    """

    def __init__(self, client: "Redis") -> None:
        self._r = client
        self._consumer = os.getenv("WORKER_CONSUMER_NAME", socket.gethostname())

    async def publish(self, tenant_slug: str, wa_id: str, payload: dict[str, Any]) -> None:
        """XADD — publish a message. Called from the webhook, must be fast.

        Flat wire format: a single field "data" containing the full payload as
        JSON, with wa_id merged in. Consumers parse `data` and receive the same
        dict shape as the publisher sent.
        """
        key = _stream_key(tenant_slug)
        body = json.dumps({"wa_id": wa_id, **payload}, ensure_ascii=False)
        entry = {"data": body}
        try:
            await self._r.xadd(key, entry, maxlen=10_000, approximate=True)
            logger.debug("broker_published", tenant=tenant_slug, wa_id=wa_id)
        except Exception as e:
            logger.exception("broker_publish_failed", tenant=tenant_slug, wa_id=wa_id, error=str(e))
            raise

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

    async def consume(self, tenant_slug: str, handler: MessageHandler) -> None:
        """Main consume loop. Blocks on XREADGROUP, ACKs on success, DLQs on repeated failure."""
        key = _stream_key(tenant_slug)
        logger.info("broker_consume_started", tenant=tenant_slug, consumer=self._consumer)

        while True:
            try:
                await self._reclaim_pending(tenant_slug, key, handler)

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
        try:
            payload = json.loads(fields.get("data", "{}"))
        except Exception as e:
            logger.warning("broker_invalid_payload", tenant=tenant_slug, error=str(e))
            await self._r.xack(key, _GROUP, stream_id)
            return

        wa_id = payload.get("wa_id", "unknown")
        try:
            await handler(payload)
            await self._r.xack(key, _GROUP, stream_id)
            await self._r.delete(_retry_key(stream_id))
            logger.info("broker_message_acked", tenant=tenant_slug, wa_id=wa_id)
        except Exception as e:
            retries = await self._r.incr(_retry_key(stream_id))
            logger.warning("broker_message_failed", tenant=tenant_slug, wa_id=wa_id, retries=retries, error=str(e))

            if int(retries) >= _MAX_RETRIES:
                await self._send_to_dlq(tenant_slug, key, stream_id, fields, str(e))

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
        # The flat payload is in fields["data"] as a JSON string.
        data_raw = fields.get("data", "{}")
        try:
            wa_id_recovered = json.loads(data_raw).get("wa_id")
        except Exception:
            wa_id_recovered = None

        dlq_entry = json.dumps({
            "stream_id": stream_id,
            "tenant": tenant_slug,
            "wa_id": wa_id_recovered,
            "data": data_raw,
            "error": error[:500],
            "failed_at": time.time(),
        }, ensure_ascii=False)
        await self._r.lpush(_dlq_key(tenant_slug), dlq_entry)
        await self._r.xack(key, _GROUP, stream_id)
        await self._r.delete(_retry_key(stream_id))

        from app.core.notifications import notify
        notify(
            event="broker_message_dlq",
            error=f"tenant={tenant_slug} wa_id={wa_id_recovered} err={error[:200]}",
        )
        logger.error("broker_message_moved_to_dlq", tenant=tenant_slug, wa_id=wa_id_recovered, error=error[:200])

    async def dlq_list(self, tenant_slug: str) -> list[dict[str, Any]]:
        raw = await self._r.lrange(_dlq_key(tenant_slug), 0, -1)
        return [json.loads(entry) for entry in (raw or [])]

    async def dlq_retry(self, tenant_slug: str, dlq_index: int) -> bool:
        items = await self._r.lrange(_dlq_key(tenant_slug), 0, -1)
        if dlq_index >= len(items):
            return False
        entry = json.loads(items[dlq_index])
        try:
            flat = json.loads(entry.get("data", "{}"))
            wa_id = flat.pop("wa_id", entry.get("wa_id", ""))
            await self.publish(tenant_slug, wa_id, flat)
            await self._r.lset(_dlq_key(tenant_slug), dlq_index, "__deleted__")
            await self._r.lrem(_dlq_key(tenant_slug), 1, "__deleted__")
            return True
        except Exception as e:
            logger.exception("broker_dlq_retry_failed", tenant=tenant_slug, error=str(e))
            return False

    async def close(self) -> None:
        pass


# ── RabbitMQ implementation (Fase 5) ─────────────────────────────────────────

class RabbitMQBroker(MessageBroker):
    """RabbitMQ implementation of MessageBroker via aio-pika.

    Topology per tenant:
      Exchange  : wa.{tenant}          (direct, durable)
      Queue     : wa.{tenant}.messages (durable, x-dead-letter-exchange → wa.{tenant}.dlx)
      DLX       : wa.{tenant}.dlx      (direct, durable)
      DLQ queue : wa.{tenant}.dlq      (durable)

    Retry strategy:
      - On failure: ack original + republish with incremented x-retry-count header.
      - After MAX_RETRIES: ack + store in in-memory _dlq dict + email alert.
      - Native DLX/DLQ queue receives messages that fail outside the handler
        (e.g. deserialization errors), giving full RabbitMQ observability.

    DLQ persistence note:
      _dlq is in-memory per process. On crash, the email alert serves as the
      durable record. Use dlq_list / dlq_retry via the admin panel to manage.
      Upgrade to DB-backed DLQ when 3+ clients reach high volume (Fase 5b).
    """

    _MAX_RETRIES = 3
    _RETRY_HEADER = "x-retry-count"

    def __init__(self, url: str) -> None:
        self._url = url
        self._dlq: dict[str, list[dict[str, Any]]] = {}
        self._connection: Optional[Any] = None  # aio_pika.RobustConnection
        self._setup_done: set[str] = set()  # tenants whose topology was declared in this process

    def _exchange_name(self, tenant_slug: str) -> str:
        return f"wa.{tenant_slug}"

    def _queue_name(self, tenant_slug: str) -> str:
        return f"wa.{tenant_slug}.messages"

    def _dlx_name(self, tenant_slug: str) -> str:
        return f"wa.{tenant_slug}.dlx"

    def _dlq_name(self, tenant_slug: str) -> str:
        return f"wa.{tenant_slug}.dlq"

    async def _get_connection(self) -> Any:
        if self._connection is None or self._connection.is_closed:
            self._connection = await aio_pika.connect_robust(self._url)  # type: ignore[union-attr]
        return self._connection

    async def setup(self, tenant_slug: str) -> None:
        """Declare exchanges and queues (idempotent)."""
        conn = await self._get_connection()
        async with conn.channel() as channel:
            # Main exchange
            exchange = await channel.declare_exchange(
                self._exchange_name(tenant_slug),
                aio_pika.ExchangeType.DIRECT,  # type: ignore[union-attr]
                durable=True,
            )
            # DLX exchange (receives nacked messages)
            dlx = await channel.declare_exchange(
                self._dlx_name(tenant_slug),
                aio_pika.ExchangeType.DIRECT,  # type: ignore[union-attr]
                durable=True,
            )
            # Main queue — messages dead-lettered to DLX on nack(requeue=False)
            queue = await channel.declare_queue(
                self._queue_name(tenant_slug),
                durable=True,
                arguments={"x-dead-letter-exchange": self._dlx_name(tenant_slug)},
            )
            await queue.bind(exchange, routing_key="messages")
            # DLQ queue — receives dead-lettered messages for inspection
            dlq_queue = await channel.declare_queue(self._dlq_name(tenant_slug), durable=True)
            await dlq_queue.bind(dlx, routing_key="")
        logger.info("rabbitmq_setup_done", tenant=tenant_slug)

    async def publish(self, tenant_slug: str, wa_id: str, payload: dict[str, Any]) -> None:
        """Publish a persistent message to the tenant exchange.

        Flat wire format: the message body is the JSON of `{"wa_id": ..., **payload}`.
        Consumers parse a single JSON object — no payload-in-payload nesting.
        """
        # Declare topology once per tenant per process so the first publish
        # works even if the consumer hasn't started yet.
        if tenant_slug not in self._setup_done:
            await self.setup(tenant_slug)
            self._setup_done.add(tenant_slug)

        conn = await self._get_connection()
        async with conn.channel() as channel:
            exchange = await channel.get_exchange(self._exchange_name(tenant_slug))
            body = json.dumps({"wa_id": wa_id, **payload}, ensure_ascii=False).encode()
            await exchange.publish(
                aio_pika.Message(  # type: ignore[union-attr]
                    body=body,
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,  # type: ignore[union-attr]
                    headers={self._RETRY_HEADER: 0},
                ),
                routing_key="messages",
            )
            logger.debug("rabbitmq_published", tenant=tenant_slug, wa_id=wa_id)

    async def consume(self, tenant_slug: str, handler: MessageHandler) -> None:
        """Main consume loop. Blocks on AMQP queue iterator until cancelled."""
        conn = await self._get_connection()
        channel = await conn.channel()
        await channel.set_qos(prefetch_count=10)
        queue = await channel.declare_queue(self._queue_name(tenant_slug), durable=True, passive=True)
        logger.info("rabbitmq_consume_started", tenant=tenant_slug)

        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                try:
                    await self._process_message(tenant_slug, channel, message, handler)
                except Exception as e:
                    logger.exception("rabbitmq_consume_error", tenant=tenant_slug, error=str(e))

    async def _process_message(
        self,
        tenant_slug: str,
        channel: Any,
        message: Any,
        handler: MessageHandler,
    ) -> None:
        """Process one AMQP message: call handler, ACK on success, retry or DLQ on failure."""
        headers = dict(message.headers or {})
        retry_count = int(headers.get(self._RETRY_HEADER, 0))
        wa_id = "unknown"
        try:
            payload = json.loads(message.body.decode())
            wa_id = payload.get("wa_id", "unknown")
            await handler(payload)
            await message.ack()
            logger.info("rabbitmq_message_acked", tenant=tenant_slug, wa_id=wa_id)
        except Exception as e:
            retry_count += 1
            logger.warning(
                "rabbitmq_message_failed",
                tenant=tenant_slug,
                wa_id=wa_id,
                retries=retry_count,
                error=str(e),
            )
            if retry_count >= self._MAX_RETRIES:
                await self._save_to_dlq(tenant_slug, message, wa_id, str(e))
                await message.ack()
            else:
                # ACK original + republish with incremented counter to preserve header
                await message.ack()
                await self._republish(tenant_slug, channel, message, retry_count)

    async def _republish(
        self,
        tenant_slug: str,
        channel: Any,
        message: Any,
        retry_count: int,
    ) -> None:
        """Re-enqueue a failed message with an incremented retry counter."""
        try:
            exchange = await channel.get_exchange(self._exchange_name(tenant_slug))
            headers = dict(message.headers or {})
            headers[self._RETRY_HEADER] = retry_count
            await exchange.publish(
                aio_pika.Message(  # type: ignore[union-attr]
                    body=message.body,
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,  # type: ignore[union-attr]
                    headers=headers,
                ),
                routing_key="messages",
            )
            logger.debug("rabbitmq_message_requeued", tenant=tenant_slug, retries=retry_count)
        except Exception as e:
            logger.exception("rabbitmq_republish_failed", tenant=tenant_slug, error=str(e))

    async def _save_to_dlq(
        self,
        tenant_slug: str,
        message: Any,
        wa_id: str,
        error: str,
    ) -> None:
        """Store a max-retried message in the in-memory DLQ and send email alert."""
        entry: dict[str, Any] = {
            "tenant": tenant_slug,
            "wa_id": wa_id,
            "payload": message.body.decode(),
            "error": error[:500],
            "failed_at": time.time(),
        }
        self._dlq.setdefault(tenant_slug, []).append(entry)

        from app.core.notifications import notify
        notify(
            event="rabbitmq_message_dlq",
            error=f"tenant={tenant_slug} wa_id={wa_id} err={error[:200]}",
        )
        logger.error(
            "rabbitmq_message_moved_to_dlq",
            tenant=tenant_slug,
            wa_id=wa_id,
            error=error[:200],
        )

    async def dlq_list(self, tenant_slug: str) -> list[dict[str, Any]]:
        return list(self._dlq.get(tenant_slug, []))

    async def dlq_retry(self, tenant_slug: str, dlq_index: int) -> bool:
        items = self._dlq.get(tenant_slug, [])
        if dlq_index >= len(items):
            return False
        entry = items[dlq_index]
        try:
            flat = json.loads(entry["payload"])
            wa_id = flat.pop("wa_id", entry.get("wa_id", ""))
            await self.publish(tenant_slug, wa_id, flat)
            items.pop(dlq_index)
            logger.info("rabbitmq_dlq_retried", tenant=tenant_slug, wa_id=wa_id)
            return True
        except Exception as e:
            logger.exception("rabbitmq_dlq_retry_failed", tenant=tenant_slug, error=str(e))
            return False

    async def close(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
            logger.info("rabbitmq_connection_closed")


# ── InMemory fallback ─────────────────────────────────────────────────────────

class InMemoryBroker(MessageBroker):
    """Fallback broker when neither Redis nor RabbitMQ is configured.

    No persistence — messages are lost if the process crashes.
    Suitable for local development without external services.
    """

    async def publish(self, tenant_slug: str, wa_id: str, payload: dict[str, Any]) -> None:
        logger.debug("in_memory_broker_publish", tenant=tenant_slug, wa_id=wa_id)

    async def consume(self, tenant_slug: str, handler: MessageHandler) -> None:
        logger.warning("in_memory_broker_no_consume", tenant=tenant_slug)

    async def setup(self, tenant_slug: str) -> None:
        pass

    async def dlq_list(self, tenant_slug: str) -> list[dict[str, Any]]:
        return []

    async def dlq_retry(self, tenant_slug: str, dlq_index: int) -> bool:
        return False

    async def close(self) -> None:
        pass


# ── Factory ───────────────────────────────────────────────────────────────────

def create_broker() -> MessageBroker:
    """Factory: RabbitMQ > Redis Streams > InMemory, based on available config."""
    from app.core.config import settings

    if getattr(settings, "RABBITMQ_URL", "") and _RABBITMQ_AVAILABLE:
        logger.info("broker_created", backend="rabbitmq")
        return RabbitMQBroker(settings.RABBITMQ_URL)

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
