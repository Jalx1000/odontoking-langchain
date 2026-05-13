"""Unit tests for the MessageBufferService — buffer, worker, and concurrency logic."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def in_memory_buffer():
    """Fresh _InMemoryMessageBuffer for each test."""
    from app.services.message_buffer import _InMemoryMessageBuffer

    return _InMemoryMessageBuffer()


@pytest.fixture
def buffer_service():
    """Fresh MessageBufferService with no backend (before initialize())."""
    from app.services.message_buffer import MessageBufferService

    return MessageBufferService()


# ── _InMemoryMessageBuffer ───────────────────────────────────────────────────


class TestInMemoryMessageBuffer:
    @pytest.mark.asyncio
    async def test_push_and_drain(self, in_memory_buffer):
        await in_memory_buffer.push("user1", "hello")
        await in_memory_buffer.push("user1", "world")
        msgs = await in_memory_buffer.drain("user1")
        assert msgs == ["hello", "world"]

    @pytest.mark.asyncio
    async def test_drain_empties_buffer(self, in_memory_buffer):
        await in_memory_buffer.push("user1", "msg")
        await in_memory_buffer.drain("user1")
        second_drain = await in_memory_buffer.drain("user1")
        assert second_drain == []

    @pytest.mark.asyncio
    async def test_drain_nonexistent_user_returns_empty(self, in_memory_buffer):
        result = await in_memory_buffer.drain("nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_max_messages_cap(self, in_memory_buffer):
        from app.core.config import settings

        for i in range(settings.BUFFER_MAX_MESSAGES + 5):
            await in_memory_buffer.push("user1", f"msg{i}")

        msgs = await in_memory_buffer.drain("user1")
        assert len(msgs) == settings.BUFFER_MAX_MESSAGES

    @pytest.mark.asyncio
    async def test_worker_isolation_per_user(self, in_memory_buffer):
        """Messages for different users don't mix."""
        await in_memory_buffer.push("user1", "a")
        await in_memory_buffer.push("user2", "b")

        assert await in_memory_buffer.drain("user1") == ["a"]
        assert await in_memory_buffer.drain("user2") == ["b"]

    @pytest.mark.asyncio
    async def test_try_acquire_worker_once(self, in_memory_buffer):
        acquired = await in_memory_buffer.try_acquire_worker("user1")
        assert acquired is True
        second = await in_memory_buffer.try_acquire_worker("user1")
        assert second is False

    @pytest.mark.asyncio
    async def test_release_worker_allows_reacquire(self, in_memory_buffer):
        await in_memory_buffer.try_acquire_worker("user1")
        await in_memory_buffer.release_worker("user1")
        reacquired = await in_memory_buffer.try_acquire_worker("user1")
        assert reacquired is True

    @pytest.mark.asyncio
    async def test_release_nonexistent_worker_is_safe(self, in_memory_buffer):
        # Should not raise
        await in_memory_buffer.release_worker("ghost")

    @pytest.mark.asyncio
    async def test_close_clears_state(self, in_memory_buffer):
        await in_memory_buffer.push("user1", "msg")
        await in_memory_buffer.try_acquire_worker("user1")
        await in_memory_buffer.close()
        assert await in_memory_buffer.drain("user1") == []

    @pytest.mark.asyncio
    async def test_concurrent_push_is_safe(self, in_memory_buffer):
        """Concurrent pushes must not corrupt the buffer."""
        tasks = [in_memory_buffer.push("user1", f"msg{i}") for i in range(20)]
        await asyncio.gather(*tasks)
        msgs = await in_memory_buffer.drain("user1")
        from app.core.config import settings

        assert len(msgs) <= settings.BUFFER_MAX_MESSAGES


# ── MessageBufferService ─────────────────────────────────────────────────────


class TestMessageBufferService:
    @pytest.mark.asyncio
    async def test_initialize_uses_in_memory_when_no_redis(self, buffer_service):
        await buffer_service.initialize()
        from app.services.message_buffer import _InMemoryMessageBuffer

        assert isinstance(buffer_service._backend, _InMemoryMessageBuffer)

    @pytest.mark.asyncio
    async def test_enqueue_without_init_falls_back_to_immediate(self, buffer_service):
        """When backend is None, process_fn is called immediately (not deferred)."""
        calls = []

        async def process_fn(wa_id: str, text: str) -> None:
            calls.append((wa_id, text))

        await buffer_service.enqueue("user1", "hello", process_fn)
        assert calls == [("user1", "hello")]

    @pytest.mark.asyncio
    async def test_enqueue_spawns_worker(self, buffer_service):
        await buffer_service.initialize()
        calls = []

        async def process_fn(wa_id: str, text: str) -> None:
            calls.append((wa_id, text))

        await buffer_service.enqueue("user1", "hello", process_fn)
        # Worker has a BUFFER_WINDOW_SECONDS sleep (0.05s in test env) — wait for it
        await asyncio.sleep(0.2)

        assert calls == [("user1", "hello")]

    @pytest.mark.asyncio
    async def test_multiple_messages_within_window_are_batched(self, buffer_service):
        """Messages arriving before window expires should arrive as one batch."""
        await buffer_service.initialize()
        received: list[str] = []

        async def process_fn(wa_id: str, text: str) -> None:
            received.append(text)

        # Enqueue three messages rapidly
        await buffer_service.enqueue("user1", "msg1", process_fn)
        await buffer_service.enqueue("user1", "msg2", process_fn)
        await buffer_service.enqueue("user1", "msg3", process_fn)

        await asyncio.sleep(0.3)

        # All three should be combined into a single call
        assert len(received) == 1
        assert "msg1" in received[0]
        assert "msg2" in received[0]
        assert "msg3" in received[0]

    @pytest.mark.asyncio
    async def test_worker_not_duplicated_per_user(self, buffer_service):
        """Only one worker task per wa_id at a time."""
        await buffer_service.initialize()
        call_count = 0

        async def slow_process(wa_id: str, text: str) -> None:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)

        # Enqueue quickly before the first worker can finish
        await buffer_service.enqueue("user1", "a", slow_process)
        await buffer_service.enqueue("user1", "b", slow_process)
        await buffer_service.enqueue("user1", "c", slow_process)

        await asyncio.sleep(0.5)
        # Should be 1 or 2 calls (second batch if c arrived during processing), never 3
        assert call_count <= 2

    @pytest.mark.asyncio
    async def test_workers_independent_per_user(self, buffer_service):
        """Two different users get independent workers."""
        await buffer_service.initialize()
        calls: list[str] = []

        async def process_fn(wa_id: str, text: str) -> None:
            calls.append(wa_id)

        await buffer_service.enqueue("user1", "hi", process_fn)
        await buffer_service.enqueue("user2", "hello", process_fn)

        await asyncio.sleep(0.3)
        assert "user1" in calls
        assert "user2" in calls

    @pytest.mark.asyncio
    async def test_worker_retries_on_process_fn_exception(self, buffer_service):
        """Worker must not crash and must release lock even if process_fn raises."""
        await buffer_service.initialize()

        async def failing_fn(wa_id: str, text: str) -> None:
            raise RuntimeError("LLM exploded")

        await buffer_service.enqueue("user1", "trigger", failing_fn)
        await asyncio.sleep(0.3)

        # Lock must be released so a new worker can be spawned
        acquired = await buffer_service._backend.try_acquire_worker("user1")
        assert acquired is True
        await buffer_service._backend.release_worker("user1")

    @pytest.mark.asyncio
    async def test_messages_after_processing_are_picked_up(self, buffer_service):
        """Messages arriving DURING LLM call should be processed in the next loop iteration."""
        await buffer_service.initialize()
        received: list[str] = []
        barrier = asyncio.Event()

        async def slow_process(wa_id: str, text: str) -> None:
            received.append(text)
            if len(received) == 1:
                # Simulate LLM latency — enqueue more during processing
                barrier.set()
                await asyncio.sleep(0.1)

        await buffer_service.enqueue("user1", "first", slow_process)
        await barrier.wait()
        await buffer_service.enqueue("user1", "second", slow_process)
        await asyncio.sleep(0.5)

        assert len(received) == 2
        assert "first" in received[0]
        assert "second" in received[1]

    @pytest.mark.asyncio
    async def test_close_awaits_pending_tasks(self, buffer_service):
        """close() must wait for in-flight workers to finish."""
        await buffer_service.initialize()
        finished = False

        async def slow_process(wa_id: str, text: str) -> None:
            nonlocal finished
            await asyncio.sleep(0.05)
            finished = True

        await buffer_service.enqueue("user1", "msg", slow_process)
        await buffer_service.close()
        assert finished is True


# ── _RedisMessageBuffer ─────────────────────────────────────────────────────


class TestRedisMessageBuffer:
    """Tests for the Redis-backed buffer using a mocked Redis client."""

    def _make_redis_mock(self):
        redis = AsyncMock()
        # Pipeline commands (rpush, expire, lrange, delete) are called WITHOUT await
        # in Redis pipeline mode — only execute() is awaited.
        pipeline = MagicMock()
        pipeline.rpush = MagicMock()
        pipeline.expire = MagicMock()
        pipeline.lrange = MagicMock()
        pipeline.delete = MagicMock()
        pipeline.execute = AsyncMock(return_value=[["hello", "world"], 1])
        redis.pipeline = MagicMock(return_value=pipeline)
        redis.llen = AsyncMock(return_value=0)
        redis.set = AsyncMock(return_value=True)
        redis.delete = AsyncMock()
        return redis, pipeline

    @pytest.mark.asyncio
    async def test_push_calls_rpush(self):
        from app.services.message_buffer import _RedisMessageBuffer

        redis, pipeline = self._make_redis_mock()
        buf = _RedisMessageBuffer(redis)
        await buf.push("user1", "hello")
        pipeline.rpush.assert_called_once_with("wa_incoming:user1", "hello")

    @pytest.mark.asyncio
    async def test_push_respects_max_messages(self):
        from app.services.message_buffer import _RedisMessageBuffer

        redis, _ = self._make_redis_mock()
        from app.core.config import settings

        redis.llen = AsyncMock(return_value=settings.BUFFER_MAX_MESSAGES)
        buf = _RedisMessageBuffer(redis)
        # Should not call pipeline at all when max reached
        await buf.push("user1", "overflow")
        redis.pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_drain_returns_and_deletes(self):
        from app.services.message_buffer import _RedisMessageBuffer

        redis, pipeline = self._make_redis_mock()
        buf = _RedisMessageBuffer(redis)
        result = await buf.drain("user1")
        assert result == ["hello", "world"]

    @pytest.mark.asyncio
    async def test_drain_on_exception_returns_empty(self):
        from app.services.message_buffer import _RedisMessageBuffer

        redis = AsyncMock()
        redis.pipeline = MagicMock(side_effect=Exception("Redis down"))
        buf = _RedisMessageBuffer(redis)
        result = await buf.drain("user1")
        assert result == []

    @pytest.mark.asyncio
    async def test_try_acquire_worker_success(self):
        from app.services.message_buffer import _RedisMessageBuffer

        redis, _ = self._make_redis_mock()
        redis.set = AsyncMock(return_value=True)
        buf = _RedisMessageBuffer(redis)
        result = await buf.try_acquire_worker("user1")
        assert result is True

    @pytest.mark.asyncio
    async def test_try_acquire_worker_already_locked(self):
        from app.services.message_buffer import _RedisMessageBuffer

        redis, _ = self._make_redis_mock()
        redis.set = AsyncMock(return_value=None)  # nx=True failed
        buf = _RedisMessageBuffer(redis)
        result = await buf.try_acquire_worker("user1")
        assert result is False

    @pytest.mark.asyncio
    async def test_try_acquire_worker_fails_open_on_redis_error(self):
        """On Redis failure, fail-open: allow processing to prevent message loss."""
        from app.services.message_buffer import _RedisMessageBuffer

        redis, _ = self._make_redis_mock()
        redis.set = AsyncMock(side_effect=Exception("connection refused"))
        buf = _RedisMessageBuffer(redis)
        result = await buf.try_acquire_worker("user1")
        assert result is True  # fail-open

    @pytest.mark.asyncio
    async def test_release_worker_deletes_key(self):
        from app.services.message_buffer import _RedisMessageBuffer

        redis, _ = self._make_redis_mock()
        buf = _RedisMessageBuffer(redis)
        await buf.release_worker("user1")
        redis.delete.assert_called_once_with("wa_worker:user1")
