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


class TestRabbitMQBroker:
    """Tests for RabbitMQBroker — mocks the AMQP channel/message layer."""

    def _broker(self) -> "RabbitMQBroker":
        from app.core.broker import RabbitMQBroker
        return RabbitMQBroker("amqp://guest:guest@localhost/")

    def _mock_message(self, wa_id: str = "591700000001", retry_count: int = 0) -> MagicMock:
        msg = MagicMock()
        msg.body = json.dumps({
            "wa_id": wa_id,
            "payload": json.dumps({"text": "hello"}),
        }).encode()
        msg.headers = {"x-retry-count": retry_count}
        msg.ack = AsyncMock()
        msg.nack = AsyncMock()
        return msg

    @pytest.mark.asyncio
    async def test_process_message_acks_on_success(self):
        broker = self._broker()
        channel = AsyncMock()
        message = self._mock_message()
        calls: list = []

        async def handler(payload: dict) -> None:
            calls.append(payload)

        with patch.object(broker, "_republish", AsyncMock()):
            await broker._process_message("odontoking", channel, message, handler)

        assert len(calls) == 1
        assert calls[0]["wa_id"] == "591700000001"
        message.ack.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_message_retries_on_first_failure(self):
        broker = self._broker()
        channel = AsyncMock()
        message = self._mock_message(retry_count=0)

        async def failing_handler(payload: dict) -> None:
            raise RuntimeError("LLM error")

        with patch.object(broker, "_republish", AsyncMock()) as mock_republish:
            await broker._process_message("odontoking", channel, message, failing_handler)

        message.ack.assert_called_once()   # ack original before republish
        mock_republish.assert_called_once_with("odontoking", channel, message, 1)

    @pytest.mark.asyncio
    async def test_process_message_moves_to_dlq_at_max_retries(self):
        broker = self._broker()
        channel = AsyncMock()
        message = self._mock_message(retry_count=2)  # next failure = 3 = MAX_RETRIES

        async def failing_handler(payload: dict) -> None:
            raise RuntimeError("always fails")

        with patch("app.core.notifications.notify"):
            await broker._process_message("odontoking", channel, message, failing_handler)

        assert len(broker._dlq.get("odontoking", [])) == 1
        assert broker._dlq["odontoking"][0]["wa_id"] == "591700000001"
        message.ack.assert_called_once()  # ACKed from queue after DLQ save

    @pytest.mark.asyncio
    async def test_process_message_no_retry_when_already_at_limit(self):
        broker = self._broker()
        channel = AsyncMock()
        message = self._mock_message(retry_count=2)

        async def failing_handler(payload: dict) -> None:
            raise RuntimeError("fail")

        with patch.object(broker, "_republish", AsyncMock()) as mock_republish, \
                patch("app.core.notifications.notify"):
            await broker._process_message("odontoking", channel, message, failing_handler)

        mock_republish.assert_not_called()  # DLQ path, not retry

    @pytest.mark.asyncio
    async def test_dlq_list_returns_pending_entries(self):
        broker = self._broker()
        broker._dlq["odontoking"] = [
            {"wa_id": "591700000001", "error": "timeout", "tenant": "odontoking"},
        ]
        result = await broker.dlq_list("odontoking")
        assert len(result) == 1
        assert result[0]["wa_id"] == "591700000001"

    @pytest.mark.asyncio
    async def test_dlq_list_empty_for_unknown_tenant(self):
        broker = self._broker()
        result = await broker.dlq_list("unknown-tenant")
        assert result == []

    @pytest.mark.asyncio
    async def test_dlq_retry_republishes_and_removes(self):
        broker = self._broker()
        broker._dlq["odontoking"] = [{
            "wa_id": "591700000001",
            "payload": json.dumps({
                "wa_id": "591700000001",
                "payload": '{"text": "retry me"}',
            }),
            "error": "timeout",
            "tenant": "odontoking",
        }]

        with patch.object(broker, "publish", AsyncMock()) as mock_publish:
            result = await broker.dlq_retry("odontoking", 0)

        assert result is True
        mock_publish.assert_called_once()
        assert len(broker._dlq["odontoking"]) == 0

    @pytest.mark.asyncio
    async def test_dlq_retry_out_of_range_returns_false(self):
        broker = self._broker()
        broker._dlq["odontoking"] = []
        result = await broker.dlq_retry("odontoking", 5)
        assert result is False

    @pytest.mark.asyncio
    async def test_save_to_dlq_sends_email_notification(self):
        broker = self._broker()
        message = self._mock_message()

        with patch("app.core.notifications.notify") as mock_notify:
            await broker._save_to_dlq("odontoking", message, "591700000001", "connection refused")

        # notify is called once explicitly + once via alert_processor on logger.error()
        assert mock_notify.called
        events = [c[1].get("event", "") for c in mock_notify.call_args_list]
        assert any("rabbitmq_message_dlq" in e for e in events)

    @pytest.mark.asyncio
    async def test_close_is_safe_when_not_connected(self):
        broker = self._broker()
        await broker.close()  # should not raise


class TestCreateBroker:
    def test_returns_in_memory_when_no_valkey(self):
        from app.core.broker import InMemoryBroker, create_broker
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.VALKEY_HOST = ""
            mock_settings.RABBITMQ_URL = ""
            broker = create_broker()
        assert isinstance(broker, InMemoryBroker)

    def test_returns_redis_broker_when_valkey_configured(self):
        from app.core.broker import RedisStreamBroker, create_broker, _REDIS_AVAILABLE
        if not _REDIS_AVAILABLE:
            pytest.skip("redis not installed")
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.RABBITMQ_URL = ""
            mock_settings.VALKEY_HOST = "localhost"
            mock_settings.VALKEY_PORT = 6379
            mock_settings.VALKEY_DB = 0
            mock_settings.VALKEY_PASSWORD = ""
            broker = create_broker()
        assert isinstance(broker, RedisStreamBroker)

    def test_returns_rabbitmq_broker_when_configured(self):
        from app.core.broker import RabbitMQBroker, create_broker, _RABBITMQ_AVAILABLE
        if not _RABBITMQ_AVAILABLE:
            pytest.skip("aio-pika not installed")
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.RABBITMQ_URL = "amqp://guest:guest@localhost/"
            broker = create_broker()
        assert isinstance(broker, RabbitMQBroker)

    def test_rabbitmq_takes_priority_over_redis(self):
        from app.core.broker import RabbitMQBroker, create_broker, _RABBITMQ_AVAILABLE, _REDIS_AVAILABLE
        if not _RABBITMQ_AVAILABLE or not _REDIS_AVAILABLE:
            pytest.skip("aio-pika or redis not installed")
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.RABBITMQ_URL = "amqp://guest:guest@localhost/"
            mock_settings.VALKEY_HOST = "localhost"
            mock_settings.VALKEY_PORT = 6379
            mock_settings.VALKEY_DB = 0
            mock_settings.VALKEY_PASSWORD = ""
            broker = create_broker()
        assert isinstance(broker, RabbitMQBroker)
