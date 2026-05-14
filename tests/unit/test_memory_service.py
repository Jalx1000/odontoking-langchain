"""Unit tests for MemoryService — covers sync/async from_config variants and error paths."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestMemoryServiceGetMemory:
    @pytest.mark.asyncio
    async def test_from_config_async_is_awaited(self):
        """When from_config returns a coroutine, it is properly awaited."""
        from app.services.memory import MemoryService

        fake_memory = MagicMock()

        async def _async_from_config(**kwargs):
            return fake_memory

        with patch("app.services.memory.AsyncMemory") as MockAM:
            MockAM.from_config.return_value = _async_from_config()
            svc = MemoryService()
            result = await svc._get_memory()

        assert result is fake_memory
        assert svc._memory is fake_memory

    @pytest.mark.asyncio
    async def test_from_config_sync_is_not_awaited(self):
        """When from_config returns a plain object (sync), it is used directly."""
        from app.services.memory import MemoryService

        fake_memory = MagicMock()

        with patch("app.services.memory.AsyncMemory") as MockAM:
            MockAM.from_config.return_value = fake_memory  # not awaitable
            svc = MemoryService()
            result = await svc._get_memory()

        assert result is fake_memory
        assert svc._memory is fake_memory

    @pytest.mark.asyncio
    async def test_get_memory_cached_after_first_call(self):
        """from_config is only called once even when _get_memory is called multiple times."""
        from app.services.memory import MemoryService

        fake_memory = MagicMock()

        with patch("app.services.memory.AsyncMemory") as MockAM:
            MockAM.from_config.return_value = fake_memory
            svc = MemoryService()
            await svc._get_memory()
            await svc._get_memory()

        MockAM.from_config.assert_called_once()


class TestMemoryServiceSearch:
    @pytest.mark.asyncio
    async def test_returns_empty_string_for_none_user(self):
        from app.services.memory import MemoryService

        svc = MemoryService()
        result = await svc.search(None, "hola")
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_empty_string_on_error(self):
        from app.services.memory import MemoryService

        with patch("app.services.memory.AsyncMemory") as MockAM:
            MockAM.from_config.side_effect = RuntimeError("pgvector connection failed")
            svc = MemoryService()
            result = await svc.search("591700000000", "reservar cita")

        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_formatted_memories(self):
        from app.services.memory import MemoryService

        fake_memory = MagicMock()
        fake_memory.search.return_value = {
            "results": [
                {"memory": "Paciente prefiere mañanas"},
                {"memory": "Alergia a la anestesia X"},
            ]
        }

        with (
            patch("app.services.memory.AsyncMemory") as MockAM,
            patch("app.services.memory.cache_service") as mock_cache,
        ):
            MockAM.from_config.return_value = fake_memory
            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()
            svc = MemoryService()
            result = await svc.search("591700000000", "reservar cita")

        assert "Paciente prefiere mañanas" in result
        assert "Alergia a la anestesia X" in result

    @pytest.mark.asyncio
    async def test_search_async_method_is_awaited(self):
        """When memory.search() returns a coroutine, it is awaited."""
        from app.services.memory import MemoryService

        async def async_search(**kwargs):
            return {"results": [{"memory": "dato importante"}]}

        fake_memory = MagicMock()
        fake_memory.search = async_search

        with (
            patch("app.services.memory.AsyncMemory") as MockAM,
            patch("app.services.memory.cache_service") as mock_cache,
        ):
            MockAM.from_config.return_value = fake_memory
            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()
            svc = MemoryService()
            result = await svc.search("591700000000", "query")

        assert "dato importante" in result

    @pytest.mark.asyncio
    async def test_uses_cache_on_hit(self):
        from app.services.memory import MemoryService

        with (
            patch("app.services.memory.AsyncMemory"),
            patch("app.services.memory.cache_service") as mock_cache,
        ):
            mock_cache.get = AsyncMock(return_value="cached memory result")
            svc = MemoryService()
            result = await svc.search("591700000000", "test query")

        assert result == "cached memory result"


class TestMemoryServiceAdd:
    @pytest.mark.asyncio
    async def test_noop_for_none_user(self):
        from app.services.memory import MemoryService

        svc = MemoryService()
        await svc.add(None, [{"role": "user", "content": "hola"}])
        # no error — just a silent no-op

    @pytest.mark.asyncio
    async def test_add_async_method_is_awaited(self):
        """When memory.add() returns a coroutine, it is awaited."""
        from app.services.memory import MemoryService

        add_called = []

        async def async_add(messages, **kwargs):
            add_called.append(True)

        fake_memory = MagicMock()
        fake_memory.add = async_add

        with patch("app.services.memory.AsyncMemory") as MockAM:
            MockAM.from_config.return_value = fake_memory
            svc = MemoryService()
            await svc.add("591700000000", [{"role": "user", "content": "hola"}])

        assert add_called

    @pytest.mark.asyncio
    async def test_add_handles_exception_gracefully(self):
        from app.services.memory import MemoryService

        with patch("app.services.memory.AsyncMemory") as MockAM:
            MockAM.from_config.side_effect = RuntimeError("db down")
            svc = MemoryService()
            # must not raise
            await svc.add("591700000000", [{"role": "user", "content": "test"}])
