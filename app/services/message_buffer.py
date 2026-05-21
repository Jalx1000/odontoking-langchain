"""Message buffer service — debounce and batch WhatsApp messages per user.

Accumulates messages within BUFFER_WINDOW_SECONDS per wa_id, then processes them
as a single combined input. A single worker per user guarantees FIFO sequential
processing and prevents race conditions on the LangGraph checkpointer.
"""

import asyncio
from collections.abc import Callable, Coroutine
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Union,
)

from app.core.config import settings
from app.core.logging import logger

if TYPE_CHECKING:
    from redis.asyncio import Redis  # pyright: ignore[reportMissingImports]

    _REDIS_AVAILABLE = True
else:
    try:
        from redis.asyncio import Redis

        _REDIS_AVAILABLE = True
    except ImportError:
        Redis = None
        _REDIS_AVAILABLE = False

ProcessFn = Callable[[str, str], Coroutine[Any, Any, None]]

# How often the worker renews the Redis lock while the LLM is processing
_LOCK_RENEW_INTERVAL = 30.0


class _InMemoryMessageBuffer:
    """Single-process fallback when Redis/Valkey is not configured."""

    def __init__(self) -> None:
        self._buffers: dict[str, list[str]] = {}
        self._workers: set[str] = set()
        self._lock = asyncio.Lock()

    async def push(
        self,
        tenant_slug: str,
        wa_id: str | None = None,
        text: str | None = None,
    ) -> None:
        if text is None:
            text = wa_id
            wa_id = tenant_slug
            tenant_slug = "odontoking"
        if wa_id is None or text is None:
            return
        async with self._lock:
            buf = self._buffers.setdefault(f"{tenant_slug}:{wa_id}", [])
            if len(buf) < settings.BUFFER_MAX_MESSAGES:
                buf.append(text)
            else:
                logger.warning("message_buffer_max_reached", tenant_slug=tenant_slug, wa_id=wa_id)

    async def drain(self, tenant_slug: str, wa_id: str | None = None) -> list[str]:
        if wa_id is None:
            wa_id = tenant_slug
            tenant_slug = "odontoking"
        async with self._lock:
            return self._buffers.pop(f"{tenant_slug}:{wa_id}", [])

    async def try_acquire_worker(self, tenant_slug: str, wa_id: str | None = None) -> bool:
        if wa_id is None:
            wa_id = tenant_slug
            tenant_slug = "odontoking"
        async with self._lock:
            key = f"{tenant_slug}:{wa_id}"
            if key in self._workers:
                return False
            self._workers.add(key)
            return True

    async def release_worker(self, tenant_slug: str, wa_id: str | None = None) -> None:
        if wa_id is None:
            wa_id = tenant_slug
            tenant_slug = "odontoking"
        async with self._lock:
            self._workers.discard(f"{tenant_slug}:{wa_id}")

    async def close(self) -> None:
        self._buffers.clear()
        self._workers.clear()


class _RedisMessageBuffer:
    """Redis-backed buffer for distributed multi-worker deployments."""

    def __init__(self, client: "Redis") -> None:
        self._r = client

    def _buf_key(self, tenant_slug: str, wa_id: str) -> str:
        return f"wa_incoming:{tenant_slug}:{wa_id}"

    def _lock_key(self, tenant_slug: str, wa_id: str) -> str:
        return f"wa_worker:{tenant_slug}:{wa_id}"

    def _lock_ttl(self) -> int:
        # Budget: debounce window + full LLM timeout + grace for retries
        return int(settings.BUFFER_WINDOW_SECONDS) + settings.LLM_TOTAL_TIMEOUT + 30

    async def push(
        self,
        tenant_slug: str,
        wa_id: str | None = None,
        text: str | None = None,
    ) -> None:
        if text is None:
            text = wa_id
            wa_id = tenant_slug
            tenant_slug = "odontoking"
        if wa_id is None or text is None:
            return
        key = self._buf_key(tenant_slug, wa_id)
        try:
            length = await self._r.llen(key)
            if length >= settings.BUFFER_MAX_MESSAGES:
                logger.warning("message_buffer_max_reached", tenant_slug=tenant_slug, wa_id=wa_id)
                return
            pipe = self._r.pipeline()
            pipe.rpush(key, text)
            pipe.expire(key, self._lock_ttl())
            await pipe.execute()
        except Exception as e:
            logger.exception("message_buffer_redis_push_failed", tenant_slug=tenant_slug, wa_id=wa_id, error=str(e))
            raise

    async def drain(self, tenant_slug: str, wa_id: str | None = None) -> list[str]:
        if wa_id is None:
            wa_id = tenant_slug
            tenant_slug = "odontoking"
        key = self._buf_key(tenant_slug, wa_id)
        try:
            pipe = self._r.pipeline()
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            results = await pipe.execute()
            return results[0] or []
        except Exception as e:
            logger.exception("message_buffer_redis_drain_failed", tenant_slug=tenant_slug, wa_id=wa_id, error=str(e))
            return []

    async def try_acquire_worker(self, tenant_slug: str, wa_id: str | None = None) -> bool:
        if wa_id is None:
            wa_id = tenant_slug
            tenant_slug = "odontoking"
        try:
            result = await self._r.set(self._lock_key(tenant_slug, wa_id), "1", nx=True, ex=self._lock_ttl())
            return result is not None
        except Exception as e:
            logger.exception("message_buffer_redis_lock_failed", tenant_slug=tenant_slug, wa_id=wa_id, error=str(e))
            return True  # fail open: allow processing if Redis is down

    async def release_worker(self, tenant_slug: str, wa_id: str | None = None) -> None:
        if wa_id is None:
            wa_id = tenant_slug
            tenant_slug = "odontoking"
        try:
            await self._r.delete(self._lock_key(tenant_slug, wa_id))
        except Exception as e:
            logger.warning("message_buffer_redis_release_failed", tenant_slug=tenant_slug, wa_id=wa_id, error=str(e))

    async def renew_lock(self, tenant_slug: str, wa_id: str | None = None) -> None:
        if wa_id is None:
            wa_id = tenant_slug
            tenant_slug = "odontoking"
        """Extend the worker lock TTL — call every _LOCK_RENEW_INTERVAL during processing."""
        try:
            await self._r.expire(self._lock_key(tenant_slug, wa_id), self._lock_ttl())
        except Exception as e:
            logger.warning("message_buffer_redis_renew_failed", tenant_slug=tenant_slug, wa_id=wa_id, error=str(e))

    async def list_orphaned_buffers(self) -> list[str]:
        """Return wa_ids that have buffered messages but no active worker lock.

        Used on startup to recover messages that survived a process crash.
        """
        try:
            buf_keys: list[str] = await self._r.keys("wa_incoming:*")
            orphaned = []
            for key in buf_keys:
                raw = key.replace("wa_incoming:", "")
                if ":" in raw:
                    tenant_slug, wa_id = raw.split(":", 1)
                    lock_key = self._lock_key(tenant_slug, wa_id)
                    legacy_lock_key = self._lock_key("odontoking", wa_id)
                else:
                    tenant_slug, wa_id = "odontoking", raw
                    lock_key = self._lock_key(tenant_slug, wa_id)
                    legacy_lock_key = f"wa_worker:{wa_id}"

                if not await self._r.exists(lock_key) and not await self._r.exists(legacy_lock_key):
                    orphaned.append(f"{tenant_slug}:{wa_id}")
            return orphaned
        except Exception as e:
            logger.exception("message_buffer_orphan_scan_failed", error=str(e))
            return []

    async def close(self) -> None:
        pass  # Redis client lifecycle is managed by the caller


class MessageBufferService:
    """Debounce-and-batch service for WhatsApp messages.

    One worker task per wa_id sleeps for BUFFER_WINDOW_SECONDS, then drains
    all buffered messages and processes them as a single combined string.
    After processing it loops back to check for messages that arrived during
    the LLM call, guaranteeing FIFO sequential processing with no lost messages.
    """

    def __init__(self) -> None:
        """Initialize with no backend; call initialize() to connect."""
        self._backend: Optional[Union[_InMemoryMessageBuffer, _RedisMessageBuffer]] = None
        self._background_tasks: set[asyncio.Task] = set()

    async def initialize(self) -> None:
        """Connect to Redis if available, otherwise use in-memory backend."""
        if settings.VALKEY_HOST and _REDIS_AVAILABLE:
            client = Redis(
                host=settings.VALKEY_HOST,
                port=settings.VALKEY_PORT,
                db=settings.VALKEY_DB,
                password=settings.VALKEY_PASSWORD or None,
                max_connections=settings.VALKEY_MAX_CONNECTIONS,
                decode_responses=True,
            )
            self._backend = _RedisMessageBuffer(client)
            logger.info("message_buffer_initialized", backend="redis", window=settings.BUFFER_WINDOW_SECONDS)
        else:
            self._backend = _InMemoryMessageBuffer()
            logger.info("message_buffer_initialized", backend="in_memory", window=settings.BUFFER_WINDOW_SECONDS)

    async def close(self) -> None:
        """Drain pending tasks and release backend resources."""
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        if self._backend:
            await self._backend.close()

    async def enqueue(
        self,
        tenant_slug: str,
        wa_id: str | None = None,
        text: str | None = None,
        process_fn: ProcessFn | None = None,
    ) -> None:
        """Buffer a message and spawn a worker if none is running for this user.

        If the buffer service is not initialized, falls back to immediate processing.
        """
        if process_fn is None:
            process_fn = text  # type: ignore[assignment]
            text = wa_id
            wa_id = tenant_slug
            tenant_slug = "odontoking"
        if wa_id is None or text is None or process_fn is None:
            return
        if self._backend is None:
            logger.warning("message_buffer_not_initialized_fallback", tenant_slug=tenant_slug, wa_id=wa_id)
            await process_fn(wa_id, text)
            return

        try:
            await self._backend.push(tenant_slug, wa_id, text)
        except Exception:
            # Redis push failed — fall back to immediate processing
            logger.warning("message_buffer_push_failed_fallback", tenant_slug=tenant_slug, wa_id=wa_id)
            await process_fn(wa_id, text)
            return

        acquired = await self._backend.try_acquire_worker(tenant_slug, wa_id)
        if acquired:
            task = asyncio.create_task(self._worker(tenant_slug, wa_id, process_fn))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            logger.info("message_buffer_worker_spawned", tenant_slug=tenant_slug, wa_id=wa_id)

    async def _renew_lock_loop(self, tenant_slug: str, wa_id: str, backend: "_RedisMessageBuffer") -> None:
        """Periodically extend the Redis lock while the LLM is processing."""
        while True:
            await asyncio.sleep(_LOCK_RENEW_INTERVAL)
            await backend.renew_lock(tenant_slug, wa_id)
            logger.debug("message_buffer_lock_renewed", tenant_slug=tenant_slug, wa_id=wa_id)

    async def _worker(self, tenant_slug: str, wa_id: str, process_fn: ProcessFn) -> None:
        """Worker loop: sleep once (debounce) → drain → process → drain immediately → repeat."""
        backend = self._backend
        assert backend is not None

        try:
            # Single debounce window — accumulate rapid-fire messages
            await asyncio.sleep(settings.BUFFER_WINDOW_SECONDS)
            while True:
                messages = await backend.drain(tenant_slug, wa_id)
                if not messages:
                    return

                combined = "\n".join(messages)
                logger.info("message_buffer_batch_ready", tenant_slug=tenant_slug, wa_id=wa_id, count=len(messages))

                # Renew the Redis lock every 30s during LLM processing so it never expires
                renew_task: Optional[asyncio.Task] = None
                if isinstance(backend, _RedisMessageBuffer):
                    renew_task = asyncio.create_task(self._renew_lock_loop(tenant_slug, wa_id, backend))
                try:
                    await process_fn(wa_id, combined)
                except Exception as e:
                    logger.exception("message_buffer_process_fn_failed", tenant_slug=tenant_slug, wa_id=wa_id, error=str(e))
                finally:
                    if renew_task:
                        renew_task.cancel()
                # Yield once so any in-flight enqueue calls complete, then re-drain immediately
                await asyncio.sleep(0)
        finally:
            await backend.release_worker(tenant_slug, wa_id)
            logger.info("message_buffer_worker_exited", tenant_slug=tenant_slug, wa_id=wa_id)

    async def recover(self, process_fn: Callable[[str, str, str], Coroutine[Any, Any, None]]) -> None:
        """Recover messages that survived a process crash (Redis only).

        On startup, finds wa_incoming:* keys with no active wa_worker:* lock
        and spawns workers for them. Call this from the lifespan after initialize().
        """
        if not isinstance(self._backend, _RedisMessageBuffer):
            return

        orphaned = await self._backend.list_orphaned_buffers()
        if not orphaned:
            return

        logger.warning("message_buffer_recovery_started", count=len(orphaned))
        for compound in orphaned:
            tenant_slug, wa_id = compound.split(":", 1)
            acquired = await self._backend.try_acquire_worker(tenant_slug, wa_id)
            if acquired:
                async def _wrapped_process_fn(inner_wa_id: str, text: str, _tenant_slug: str = tenant_slug) -> None:
                    await process_fn(_tenant_slug, inner_wa_id, text)

                task = asyncio.create_task(self._worker(tenant_slug, wa_id, _wrapped_process_fn))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
                logger.warning("message_buffer_orphan_recovered", tenant_slug=tenant_slug, wa_id=wa_id)


message_buffer_service = MessageBufferService()
