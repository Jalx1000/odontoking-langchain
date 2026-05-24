"""Unit tests for Odontoking API tools (services, specialties, doctors, schedules, availability)."""

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
    async def test_calls_doctor_detail_endpoint_and_returns_availability(self):
        from app.core.langgraph.tools.odontoking import get_doctor_schedule

        raw = {
            "id": 5,
            "name": "Dra. López",
            "schedule": [
                {
                    "date": "2026-05-15",
                    "slots": [
                        {"start_time": "09:00", "end_time": "09:30", "status": "available"},
                        {"start_time": "09:30", "end_time": "10:00", "status": "busy"},
                    ],
                },
                {
                    "date": "2026-05-16",
                    "slots": [{"start_time": "10:00", "end_time": "10:30", "status": "available"}],
                },
            ],
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
            call_url = client.get.call_args[0][0]
            call_headers = client.get.call_args[1]["headers"]
            assert call_url.endswith("/api/doctors/5")
            assert call_headers["accept"] == "application/json"
            assert call_headers["X-CSRF-TOKEN"] == ""

    @pytest.mark.asyncio
    async def test_returns_error_when_doctor_not_found(self):
        from app.core.langgraph.tools.odontoking import get_doctor_schedule

        resp = _make_response(404, {"message": "Not found"})

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
            "id": 3,
            "name": "Dr. X",
            "schedule": [
                {
                    "date": "2026-05-20",
                    "slots": [
                        {
                            "startTime": "08:00",
                            "endTime": "09:00",
                            "internal_id": 42,
                            "status": "available",
                        }
                    ],
                }
            ],
        }
        resp = _make_response(200, raw)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_doctor_schedule.ainvoke({"id_doctor": 3}))
            slot = result["availability"][0]
            assert "date" in slot
            assert "start_time" in slot
            assert "end_time" in slot
            assert "internal_id" not in slot


class TestGetHorarios:
    @pytest.mark.asyncio
    async def test_returns_all_horarios_without_filter(self):
        from app.core.langgraph.tools.odontoking import get_horarios

        data = [
            {"doctorId": 1, "doctorName": "Dr. García", "horarioRaw": "Lun-Vie 09:00-17:00"},
            {"doctorId": 2, "doctorName": "Dra. López", "horarioRaw": "Lun-Sáb 08:00-14:00"},
        ]
        resp = _make_response(200, data)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_horarios.ainvoke({}))
            assert len(result) == 2
            assert result[0]["doctorName"] == "Dr. García"

    @pytest.mark.asyncio
    async def test_filters_by_doctor_id(self):
        from app.core.langgraph.tools.odontoking import get_horarios

        data = [{"doctorId": 5, "doctorName": "Dr. Specific", "horarioRaw": "Lun-Vie 10:00-18:00"}]
        resp = _make_response(200, data)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_horarios.ainvoke({"doctor_id": 5}))
            # Verify doctorId param was passed
            call_params = client.get.call_args[1]["params"]
            assert call_params["doctorId"] == 5
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_no_doctor_id_omits_param(self):
        from app.core.langgraph.tools.odontoking import get_horarios

        resp = _make_response(200, [])

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            await get_horarios.ainvoke({})
            call_params = client.get.call_args[1]["params"]
            assert "doctorId" not in call_params

    @pytest.mark.asyncio
    async def test_returns_error_on_404(self):
        from app.core.langgraph.tools.odontoking import get_horarios

        resp = _make_response(404, {"message": "Not found"})

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_horarios.ainvoke({"doctor_id": 999}))
            assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_error_on_exception(self):
        from app.core.langgraph.tools.odontoking import get_horarios

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=Exception("timeout"))
            cls.return_value = client

            result = json.loads(await get_horarios.ainvoke({}))
            assert "error" in result


class TestGetDisponibilidad:
    @pytest.mark.asyncio
    async def test_returns_available_slots(self):
        from app.core.langgraph.tools.odontoking import get_disponibilidad

        data = [
            {"doctorId": 5, "date": "2026-05-15", "startTime": "09:00", "endTime": "10:00"},
            {"doctorId": 5, "date": "2026-05-15", "startTime": "11:00", "endTime": "12:00"},
        ]
        resp = _make_response(200, data)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_disponibilidad.ainvoke({"doctor_id": 5, "date": "2026-05-15"}))
            assert len(result) == 2
            assert result[0]["startTime"] == "09:00"

    @pytest.mark.asyncio
    async def test_passes_correct_query_params(self):
        from app.core.langgraph.tools.odontoking import get_disponibilidad

        resp = _make_response(200, [])

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            await get_disponibilidad.ainvoke({"doctor_id": 19, "date": "2026-05-20"})
            call_params = client.get.call_args[1]["params"]
            assert call_params["doctorId"] == 19
            assert call_params["date"] == "2026-05-20"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_slots(self):
        from app.core.langgraph.tools.odontoking import get_disponibilidad

        resp = _make_response(200, [])

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_disponibilidad.ainvoke({"doctor_id": 5, "date": "2026-05-15"}))
            assert result == []

    @pytest.mark.asyncio
    async def test_returns_error_on_400(self):
        from app.core.langgraph.tools.odontoking import get_disponibilidad

        resp = _make_response(400, {"message": "invalid date"})

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = json.loads(await get_disponibilidad.ainvoke({"doctor_id": 5, "date": "not-a-date"}))
            assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_error_on_exception(self):
        from app.core.langgraph.tools.odontoking import get_disponibilidad

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=Exception("network error"))
            cls.return_value = client

            result = json.loads(await get_disponibilidad.ainvoke({"doctor_id": 5, "date": "2026-05-15"}))
            assert "error" in result
