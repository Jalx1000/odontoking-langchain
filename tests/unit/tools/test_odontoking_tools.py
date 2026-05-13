"""Unit tests for Odontoking API tools (services, specialties, doctors, schedules)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _make_response(status: int, data: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            str(status), request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _async_client_ctx(mock_client: AsyncMock):
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


class TestGetServices:
    @pytest.mark.asyncio
    async def test_returns_filtered_services(self):
        from app.core.langgraph.tools.odontoking import get_services

        data = {"data": [{"id": 1, "name": "Limpieza dental", "price": 100}, {"id": 2, "name": "Ortodoncia", "price": 500}]}
        resp = _make_response(200, data)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_services.ainvoke({}))
            assert len(result["data"]) == 2
            # Price must NOT be exposed
            assert "price" not in result["data"][0]
            assert result["data"][0]["id"] == 1
            assert result["data"][0]["name"] == "Limpieza dental"

    @pytest.mark.asyncio
    async def test_returns_error_on_http_failure(self):
        from app.core.langgraph.tools.odontoking import get_services

        resp = _make_response(500, {})

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_services.ainvoke({}))
            assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_error_on_network_exception(self):
        from app.core.langgraph.tools.odontoking import get_services

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=Exception("timeout"))
            cls.return_value = client

            result = json.loads(await get_services.ainvoke({}))
            assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty_list(self):
        from app.core.langgraph.tools.odontoking import get_services

        resp = _make_response(200, {"data": []})

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_services.ainvoke({}))
            assert result["data"] == []


class TestGetSpecialties:
    @pytest.mark.asyncio
    async def test_returns_specialties(self):
        from app.core.langgraph.tools.odontoking import get_specialties

        data = [{"id": 1, "name": "Ortodoncia"}, {"id": 2, "name": "Endodoncia"}]
        resp = _make_response(200, data)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_specialties.ainvoke({}))
            assert result == data

    @pytest.mark.asyncio
    async def test_returns_error_on_failure(self):
        from app.core.langgraph.tools.odontoking import get_specialties

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=Exception("connection refused"))
            cls.return_value = client

            result = json.loads(await get_specialties.ainvoke({}))
            assert "error" in result


class TestGetDoctors:
    @pytest.mark.asyncio
    async def test_returns_filtered_doctor_fields(self):
        from app.core.langgraph.tools.odontoking import get_doctors

        raw = {
            "data": [
                {
                    "id": 10,
                    "name": "Dr. García",
                    "is_active": True,
                    "age_range_min": 5,
                    "age_range_max": 80,
                    "salary": 5000,  # must be stripped
                    "specialties": [{"id": 1, "name": "Ortodoncia"}],
                    "availability": [],
                }
            ]
        }
        resp = _make_response(200, raw)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_doctors.ainvoke({}))
            doc = result["data"][0]
            assert doc["id"] == 10
            assert doc["name"] == "Dr. García"
            assert "salary" not in doc
            assert doc["specialties"][0]["name"] == "Ortodoncia"

    @pytest.mark.asyncio
    async def test_doctor_with_no_specialties(self):
        from app.core.langgraph.tools.odontoking import get_doctors

        raw = {"data": [{"id": 1, "name": "Dr. Sin Esp.", "is_active": True, "age_range_min": 0, "age_range_max": 99, "specialties": None}]}
        resp = _make_response(200, raw)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_doctors.ainvoke({}))
            assert result["data"][0]["specialties"] == []


class TestGetDoctorSchedule:
    @pytest.mark.asyncio
    async def test_returns_availability_for_matching_doctor(self):
        from app.core.langgraph.tools.odontoking import get_doctor_schedule

        raw = {
            "data": [
                {
                    "id": 5,
                    "name": "Dra. López",
                    "availability": [
                        {"date": "2026-05-15", "start_time": "09:00"},
                        {"date": "2026-05-16", "start_time": "10:00"},
                    ],
                },
                {"id": 7, "name": "Dr. Otro", "availability": []},
            ]
        }
        resp = _make_response(200, raw)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_doctor_schedule.ainvoke({"id_doctor": 5}))
            assert result["doctor_id"] == 5
            assert result["name"] == "Dra. López"
            assert len(result["availability"]) == 2

    @pytest.mark.asyncio
    async def test_returns_error_when_doctor_not_found(self):
        from app.core.langgraph.tools.odontoking import get_doctor_schedule

        resp = _make_response(200, {"data": [{"id": 1, "name": "Dr. A", "availability": []}]})

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_doctor_schedule.ainvoke({"id_doctor": 999}))
            assert "error" in result

    @pytest.mark.asyncio
    async def test_availability_strips_extra_fields(self):
        from app.core.langgraph.tools.odontoking import get_doctor_schedule

        raw = {
            "data": [
                {
                    "id": 3,
                    "name": "Dr. X",
                    "availability": [
                        {"date": "2026-05-20", "start_time": "08:00", "end_time": "09:00", "internal_id": 42}
                    ],
                }
            ]
        }
        resp = _make_response(200, raw)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_doctor_schedule.ainvoke({"id_doctor": 3}))
            slot = result["availability"][0]
            # Only date and start_time should be present
            assert "date" in slot
            assert "start_time" in slot
            assert "end_time" not in slot
            assert "internal_id" not in slot
