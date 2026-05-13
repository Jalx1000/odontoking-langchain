"""Tests for Redis Streams broker — Fase 2."""

import json
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


class TestInMemoryBroker:
    def _broker(self):
        from app.core.broker import InMemoryBroker
        return InMemoryBroker()

    @pytest.mark.asyncio
    async def test_publish_is_noop(self):
        broker = self._broker()
        # Should not raise
        await broker.publish("odontoking", "591700000000", {"text": "hi"})

    @pytest.mark.asyncio
    async def test_dlq_list_always_empty(self):
        broker = self._broker()
        assert await broker.dlq_list("odontoking") == []

    @pytest.mark.asyncio
    async def test_dlq_retry_always_false(self):
        broker = self._broker()
        assert await broker.dlq_retry("odontoking", 0) is False

    @pytest.mark.asyncio
    async def test_setup_is_noop(self):
        broker = self._broker()
        await broker.setup("odontoking")  # no error

    @pytest.mark.asyncio
    async def test_close_is_safe(self):
        broker = self._broker()
        await broker.close()


class TestRedisStreamBroker:
    def _make_redis(self):
        r = AsyncMock()
        r.xadd = AsyncMock(return_value="1234-0")
        r.xgroup_create = AsyncMock()
        r.xreadgroup = AsyncMock(return_value=[])
        r.xack = AsyncMock()
        r.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        r.delete = AsyncMock()
        r.incr = AsyncMock(return_value=1)
        r.lrange = AsyncMock(return_value=[])
        r.lpush = AsyncMock()
        r.lset = AsyncMock()
        r.lrem = AsyncMock()
        return r

    def _broker(self, redis=None):
        from app.core.broker import RedisStreamBroker
        return RedisStreamBroker(redis or self._make_redis())

    @pytest.mark.asyncio
    async def test_publish_calls_xadd(self):
        redis = self._make_redis()
        broker = self._broker(redis)
        await broker.publish("odontoking", "591700000001", {"text": "hello"})
        redis.xadd.assert_called_once()
        call_args = redis.xadd.call_args
        assert call_args[0][0] == "wa:odontoking"
        entry = call_args[0][1]
        assert entry["wa_id"] == "591700000001"
        assert "hello" in entry["payload"]

    @pytest.mark.asyncio
    async def test_publish_raises_on_redis_failure(self):
        redis = self._make_redis()
        redis.xadd = AsyncMock(side_effect=Exception("connection refused"))
        broker = self._broker(redis)
        with pytest.raises(Exception, match="connection refused"):
            await broker.publish("odontoking", "591700000001", {"text": "hi"})

    @pytest.mark.asyncio
    async def test_setup_creates_consumer_group(self):
        redis = self._make_redis()
        broker = self._broker(redis)
        await broker.setup("odontoking")
        redis.xgroup_create.assert_called_once_with(
            "wa:odontoking", "workers", id="0", mkstream=True
        )

    @pytest.mark.asyncio
    async def test_setup_ignores_busygroup_error(self):
        redis = self._make_redis()
        redis.xgroup_create = AsyncMock(side_effect=Exception("BUSYGROUP already exists"))
        broker = self._broker(redis)
        await broker.setup("odontoking")  # should not raise

    @pytest.mark.asyncio
    async def test_successful_message_is_acked(self):
        redis = self._make_redis()
        redis.xreadgroup = AsyncMock(
            side_effect=[
                [("wa:odontoking", [("1234-0", {"wa_id": "591700000001", "payload": '{"text":"hi"}'})])],
                [],  # second call returns empty to stop loop
            ]
        )
        broker = self._broker(redis)
        calls = []

        async def handler(payload):
            calls.append(payload)

        import asyncio
        try:
            await asyncio.wait_for(broker.consume("odontoking", handler), timeout=0.5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

        assert len(calls) == 1
        assert calls[0]["wa_id"] == "591700000001"
        redis.xack.assert_called()

    @pytest.mark.asyncio
    async def test_failed_message_increments_retry_counter(self):
        redis = self._make_redis()
        redis.incr = AsyncMock(return_value=1)  # first failure
        redis.xreadgroup = AsyncMock(
            side_effect=[
                [("wa:odontoking", [("1234-0", {"wa_id": "591700000001", "payload": '{"text":"fail"}'})])],
                [],
            ]
        )
        broker = self._broker(redis)

        async def failing_handler(payload):
            raise RuntimeError("LLM error")

        import asyncio
        try:
            await asyncio.wait_for(broker.consume("odontoking", failing_handler), timeout=0.5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

        redis.incr.assert_called()  # retry counter incremented
        redis.xack.assert_not_called()  # NOT acked — stays pending for retry

    @pytest.mark.asyncio
    async def test_message_moves_to_dlq_after_max_retries(self):
        redis = self._make_redis()
        redis.incr = AsyncMock(return_value=3)  # MAX_RETRIES reached
        redis.xreadgroup = AsyncMock(
            side_effect=[
                [("wa:odontoking", [("1234-0", {"wa_id": "591700000001", "payload": '{"text":"fail"}'})])],
                [],
            ]
        )
        broker = self._broker(redis)

        async def failing_handler(payload):
            raise RuntimeError("always fails")

        with patch("app.core.notifications.notify"):
            import asyncio
            try:
                await asyncio.wait_for(broker.consume("odontoking", failing_handler), timeout=0.5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        redis.lpush.assert_called()  # message pushed to DLQ
        redis.xack.assert_called()   # ACKed from stream (moved to DLQ)

    @pytest.mark.asyncio
    async def test_dlq_list_returns_parsed_entries(self):
        redis = self._make_redis()
        entry = json.dumps({"wa_id": "591700000001", "error": "timeout", "tenant": "odontoking"})
        redis.lrange = AsyncMock(return_value=[entry])
        broker = self._broker(redis)
        result = await broker.dlq_list("odontoking")
        assert len(result) == 1
        assert result[0]["wa_id"] == "591700000001"

    @pytest.mark.asyncio
    async def test_dlq_retry_republishes_message(self):
        redis = self._make_redis()
        entry = json.dumps({
            "wa_id": "591700000001",
            "payload": '{"text": "retry me"}',
            "tenant": "odontoking",
        })
        redis.lrange = AsyncMock(return_value=[entry])
        broker = self._broker(redis)
        success = await broker.dlq_retry("odontoking", 0)
        assert success is True
        redis.xadd.assert_called()  # re-published to stream
        redis.lrem.assert_called()  # removed from DLQ

    @pytest.mark.asyncio
    async def test_dlq_retry_out_of_range_returns_false(self):
        redis = self._make_redis()
        redis.lrange = AsyncMock(return_value=[])
        broker = self._broker(redis)
        assert await broker.dlq_retry("odontoking", 5) is False


class TestCreateBroker:
    def test_returns_in_memory_when_no_valkey(self):
        from app.core.broker import InMemoryBroker, create_broker
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.VALKEY_HOST = ""
            broker = create_broker()
        assert isinstance(broker, InMemoryBroker)

    def test_returns_redis_broker_when_valkey_configured(self):
        from app.core.broker import RedisStreamBroker, create_broker, _REDIS_AVAILABLE
        if not _REDIS_AVAILABLE:
            pytest.skip("redis not installed")
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.VALKEY_HOST = "localhost"
            mock_settings.VALKEY_PORT = 6379
            mock_settings.VALKEY_DB = 0
            mock_settings.VALKEY_PASSWORD = ""
            broker = create_broker()
        assert isinstance(broker, RedisStreamBroker)
