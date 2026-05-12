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

# Extra seconds on top of BUFFER_WINDOW for the LLM+tools processing time
_LOCK_EXTRA_TTL = 120


class _InMemoryMessageBuffer:
    """Single-process fallback when Redis/Valkey is not configured."""

    def __init__(self) -> None:
        self._buffers: dict[str, list[str]] = {}
        self._workers: set[str] = set()
        self._lock = asyncio.Lock()

    async def push(self, wa_id: str, text: str) -> None:
        async with self._lock:
            buf = self._buffers.setdefault(wa_id, [])
            if len(buf) < settings.BUFFER_MAX_MESSAGES:
                buf.append(text)
            else:
                logger.warning("message_buffer_max_reached", wa_id=wa_id)

    async def drain(self, wa_id: str) -> list[str]:
        async with self._lock:
            return self._buffers.pop(wa_id, [])

    async def try_acquire_worker(self, wa_id: str) -> bool:
        async with self._lock:
            if wa_id in self._workers:
                return False
            self._workers.add(wa_id)
            return True

    async def release_worker(self, wa_id: str) -> None:
        async with self._lock:
            self._workers.discard(wa_id)

    async def close(self) -> None:
        self._buffers.clear()
        self._workers.clear()


class _RedisMessageBuffer:
    """Redis-backed buffer for distributed multi-worker deployments."""

    def __init__(self, client: "Redis") -> None:
        self._r = client

    def _buf_key(self, wa_id: str) -> str:
        return f"wa_incoming:{wa_id}"

    def _lock_key(self, wa_id: str) -> str:
        return f"wa_worker:{wa_id}"

    def _lock_ttl(self) -> int:
        return int(settings.BUFFER_WINDOW_SECONDS) + _LOCK_EXTRA_TTL

    async def push(self, wa_id: str, text: str) -> None:
        key = self._buf_key(wa_id)
        try:
            length = await self._r.llen(key)
            if length >= settings.BUFFER_MAX_MESSAGES:
                logger.warning("message_buffer_max_reached", wa_id=wa_id)
                return
            pipe = self._r.pipeline()
            pipe.rpush(key, text)
            pipe.expire(key, self._lock_ttl())
            await pipe.execute()
        except Exception as e:
            logger.exception("message_buffer_redis_push_failed", wa_id=wa_id, error=str(e))
            raise

    async def drain(self, wa_id: str) -> list[str]:
        key = self._buf_key(wa_id)
        try:
            pipe = self._r.pipeline()
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            results = await pipe.execute()
            return results[0] or []
        except Exception as e:
            logger.exception("message_buffer_redis_drain_failed", wa_id=wa_id, error=str(e))
            return []

    async def try_acquire_worker(self, wa_id: str) -> bool:
        try:
            result = await self._r.set(self._lock_key(wa_id), "1", nx=True, ex=self._lock_ttl())
            return result is not None
        except Exception as e:
            logger.exception("message_buffer_redis_lock_failed", wa_id=wa_id, error=str(e))
            return True  # fail open: allow processing if Redis is down

    async def release_worker(self, wa_id: str) -> None:
        try:
            await self._r.delete(self._lock_key(wa_id))
        except Exception as e:
            logger.warning("message_buffer_redis_release_failed", wa_id=wa_id, error=str(e))

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

    async def enqueue(self, wa_id: str, text: str, process_fn: ProcessFn) -> None:
        """Buffer a message and spawn a worker if none is running for this user.

        If the buffer service is not initialized, falls back to immediate processing.
        """
        if self._backend is None:
            logger.warning("message_buffer_not_initialized_fallback", wa_id=wa_id)
            await process_fn(wa_id, text)
            return

        try:
            await self._backend.push(wa_id, text)
        except Exception:
            # Redis push failed — fall back to immediate processing
            logger.warning("message_buffer_push_failed_fallback", wa_id=wa_id)
            await process_fn(wa_id, text)
            return

        acquired = await self._backend.try_acquire_worker(wa_id)
        if acquired:
            task = asyncio.create_task(self._worker(wa_id, process_fn))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            logger.info("message_buffer_worker_spawned", wa_id=wa_id)

    async def _worker(self, wa_id: str, process_fn: ProcessFn) -> None:
        """Worker loop: sleep once (debounce) → drain → process → drain immediately → repeat."""
        backend = self._backend
        assert backend is not None

        try:
            # Single debounce window — accumulate rapid-fire messages
            await asyncio.sleep(settings.BUFFER_WINDOW_SECONDS)
            while True:
                messages = await backend.drain(wa_id)
                if not messages:
                    return

                combined = "\n".join(messages)
                logger.info("message_buffer_batch_ready", wa_id=wa_id, count=len(messages))

                try:
                    await process_fn(wa_id, combined)
                except Exception as e:
                    logger.exception("message_buffer_process_fn_failed", wa_id=wa_id, error=str(e))
                # Yield once so any in-flight enqueue calls complete, then immediately
                # re-drain — no extra BUFFER_WINDOW_SECONDS for messages received during LLM
                await asyncio.sleep(0)
        finally:
            await backend.release_worker(wa_id)
            logger.info("message_buffer_worker_exited", wa_id=wa_id)


message_buffer_service = MessageBufferService()
