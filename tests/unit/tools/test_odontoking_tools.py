"""Unit tests for Odontoking API tools (services, specialties, doctors, schedules, availability)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _make_response(status: int, data) -> MagicMock:
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
                    "has_availability": True,
                    "availability": [{"date": "2026-06-18", "start_time": "09:00:00"}],
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

        raw = {"data": [{"id": 1, "name": "Dr. Sin Esp.", "is_active": True, "age_range_min": 0, "age_range_max": 99, "specialties": None, "has_availability": True, "availability": [{"date": "2026-06-18"}]}]}
        resp = _make_response(200, raw)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_doctors.ainvoke({}))
            assert result["data"][0]["specialties"] == []

    @pytest.mark.asyncio
    async def test_excludes_doctors_without_availability(self):
        from app.core.langgraph.tools.odontoking import get_doctors

        raw = {
            "data": [
                # kept: active, flagged available, non-empty availability list
                {"id": 1, "name": "Dr. Disponible", "is_active": True, "has_availability": True, "availability": [{"date": "2026-06-18"}], "specialties": []},
                # dropped: flag False + empty availability
                {"id": 2, "name": "Dr. Sin Cupo", "is_active": True, "has_availability": False, "availability": [], "specialties": []},
                # dropped: flag True but empty availability list (inconsistent backend data)
                {"id": 3, "name": "Dr. Bandera Mentirosa", "is_active": True, "has_availability": True, "availability": [], "specialties": []},
                # dropped: fields missing entirely
                {"id": 4, "name": "Dr. Sin Datos", "is_active": True, "specialties": []},
            ]
        }
        resp = _make_response(200, raw)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_doctors.ainvoke({}))
            assert [d["id"] for d in result["data"]] == [1]


class TestGetDoctorSchedule:
    """Tests for the /api/doctors/{id}/available-slots endpoint contract (SMD-backed).

    Response shape: {"source", "degraded", "reason",
        "schedule": [{"date", "slots": [{start_time, end_time, status}]}]}.
    Tool returns {"doctor_id", "schedule": [{date, day_label, slots}], "days_queried"}.

    Run synchronously via asyncio.run() — pytest-asyncio is not installed, so
    @pytest.mark.asyncio tests are silently skipped (see test_get_doctor_schedule.py).
    """

    @staticmethod
    def _invoke(payload: dict):
        from app.core.langgraph.tools.odontoking import get_doctor_schedule

        return json.loads(asyncio.run(get_doctor_schedule.ainvoke(payload)))

    def test_calls_slots_endpoint_and_returns_schedule(self):
        """Calls /api/doctors/{id}/available-slots with the Bearer token and returns day objects."""
        raw = {
            "doctor_id": 5,
            "source": "smd",
            "degraded": False,
            "reason": None,
            "schedule": [
                {
                    "date": "2026-05-26",
                    "slots": [
                        {"start_time": "08:30", "end_time": "09:30", "status": "available"},
                        {"start_time": "09:30", "end_time": "10:30", "status": "available"},
                    ],
                }
            ],
        }
        resp = _make_response(200, raw)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = self._invoke({"id_doctor": 5})

        assert result["doctor_id"] == 5
        assert result["days_queried"] == 7
        assert len(result["schedule"]) == 1
        day = result["schedule"][0]
        assert day["date"] == "2026-05-26"
        assert "day_label" in day
        assert len(day["slots"]) == 2
        assert day["slots"][0]["start_time"] == "08:30"

        call_url = client.get.call_args[0][0]
        call_headers = client.get.call_args[1]["headers"]
        assert call_url.endswith("/api/doctors/5/available-slots")
        assert call_headers["accept"] == "application/json"
        assert call_headers["Authorization"].startswith("Bearer ")

    def test_returns_error_when_doctor_not_found(self):
        """404 yields {"error": "doctor_not_found"} without raising."""
        resp = _make_response(404, {"message": "Not found"})

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = self._invoke({"id_doctor": 999})

        assert result["error"] == "doctor_not_found"

    def test_schedule_strips_extra_fields_and_filters_unavailable(self):
        """Slots are reduced to start_time/end_time; busy slots are filtered out."""
        raw = {
            "doctor_id": 3,
            "schedule": [
                {
                    "date": "2026-05-26",
                    "slots": [
                        {
                            "start_time": "08:00",
                            "end_time": "09:00",
                            "internal_id": 42,
                            "status": "available",
                        },
                        {"start_time": "09:00", "end_time": "10:00", "status": "busy"},
                    ],
                }
            ],
        }
        resp = _make_response(200, raw)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = self._invoke({"id_doctor": 3})

        slots = result["schedule"][0]["slots"]
        # Only the available slot survives; busy is filtered out.
        assert len(slots) == 1
        slot = slots[0]
        assert slot["start_time"] == "08:00"
        assert slot["end_time"] == "09:00"
        assert "internal_id" not in slot
        assert "status" not in slot
