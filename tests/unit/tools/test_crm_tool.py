"""Unit tests for the Sofopolis CRM tools (update_crm, get_citas)."""

import json
from datetime import datetime
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


def _async_client_ctx(client: AsyncMock) -> AsyncMock:
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _r(data) -> MagicMock:
    """Shorthand: create a 200 OK mock response."""
    return _make_response(200, data)


# Reusable API response stubs (must be response objects, not plain dicts)
PERSON_NEW = _r({"data": []})
PERSON_EXISTS = _r({"data": [{"id": 99, "name": "Ana López", "emails": [{"value": "591700000000@whatsapp.sofopolis.net"}]}]})
PERSON_CREATED = _make_response(201, {"data": {"id": 99}})
LEADS_EMPTY = _r({"data": []})
LEADS_WITH_MATCH = _r({
    "data": [
        {
            "id": 77,
            "person": {"emails": [{"value": "591700000000@whatsapp.sofopolis.net"}]},
        }
    ]
})
LEAD_CREATED = _make_response(201, {"data": {"id": 77}})
LEAD_UPDATED = _r({"data": {"id": 77}})
ACTIVITY_CREATED = _make_response(201, {"data": {"id": 1}})


class TestUpdateCrm:
    def _base_args(self, **overrides) -> dict:
        args = {
            "wa_id": "591700000000",
            "person_name": "Ana López",
            "person_phone": "591700000000",
        }
        args.update(overrides)
        return args

    @pytest.mark.asyncio
    async def test_creates_new_person_and_lead(self):
        from app.core.langgraph.tools.crm import update_crm

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_NEW, LEADS_EMPTY])
            client.post = AsyncMock(side_effect=[PERSON_CREATED, LEAD_CREATED])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            cls.return_value = client

            result = json.loads(await update_crm.ainvoke(self._base_args()))
            assert result["success"] is True
            assert result["person_id"] == 99
            assert result["lead_id"] == 77

    @pytest.mark.asyncio
    async def test_finds_existing_person_and_updates_lead(self):
        from app.core.langgraph.tools.crm import update_crm

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_WITH_MATCH])
            client.post = AsyncMock(return_value=_make_response(201, {}))
            client.put = AsyncMock(return_value=LEAD_UPDATED)
            cls.return_value = client

            result = json.loads(await update_crm.ainvoke(self._base_args()))
            assert result["success"] is True
            assert result["person_id"] == 99
            assert result["lead_id"] == 77
            # PUT was called for lead update (not POST for lead create)
            assert client.put.called

    @pytest.mark.asyncio
    async def test_creates_activity_when_appointment_confirmed(self):
        from app.core.langgraph.tools.crm import update_crm

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_WITH_MATCH])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            client.post = AsyncMock(return_value=ACTIVITY_CREATED)
            cls.return_value = client

            result = json.loads(
                await update_crm.ainvoke(
                    self._base_args(
                        doctor_id=5,
                        horario_cita="15/05/2026 09:00",
                        es_cita_confirmada=True,
                        products_name="Limpieza",
                        products_product_id=1,
                    )
                )
            )
            assert result["success"] is True
            assert result["appointment_registered"] is True
            # POST must have been called for the activity endpoint
            activity_call_args = client.post.call_args
            assert "activities" in activity_call_args[0][0]

    @pytest.mark.asyncio
    async def test_no_activity_when_not_confirmed(self):
        from app.core.langgraph.tools.crm import update_crm

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_WITH_MATCH])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            client.post = AsyncMock(return_value=_make_response(201, {}))
            cls.return_value = client

            result = json.loads(
                await update_crm.ainvoke(
                    self._base_args(
                        doctor_id=5,
                        horario_cita="15/05/2026 09:00",
                        es_cita_confirmada=False,
                    )
                )
            )
            assert result["appointment_registered"] is False

    @pytest.mark.asyncio
    async def test_returns_error_payload_on_exception(self):
        from app.core.langgraph.tools.crm import update_crm

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=Exception("network down"))
            cls.return_value = client

            result = json.loads(await update_crm.ainvoke(self._base_args()))
            assert result["success"] is False
            assert "error" in result

    @pytest.mark.asyncio
    async def test_cancellation_changes_lead_stage(self):
        from app.core.langgraph.tools.crm import update_crm

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_WITH_MATCH])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            client.post = AsyncMock(return_value=_make_response(201, {}))
            cls.return_value = client

            result = json.loads(
                await update_crm.ainvoke(self._base_args(es_cita_cancelada=True))
            )
            assert result["success"] is True
            # PUT must be called for stage change (lead/stage/edit/{id})
            stage_calls = [
                c for c in client.put.call_args_list if "stage" in c[0][0]
            ]
            assert len(stage_calls) == 1


class TestParseAppointmentDatetime:
    def test_slash_format(self):
        from app.core.langgraph.tools.crm import _parse_appointment_datetime

        start, end = _parse_appointment_datetime("15/05/2026 09:00")
        assert start == "2026-05-15 09:00:00"
        assert end == "2026-05-15 10:00:00"

    def test_iso_format(self):
        from app.core.langgraph.tools.crm import _parse_appointment_datetime

        start, end = _parse_appointment_datetime("2026-05-15 09:00")
        assert start == "2026-05-15 09:00:00"

    def test_underscore_separator(self):
        from app.core.langgraph.tools.crm import _parse_appointment_datetime

        start, end = _parse_appointment_datetime("15/05/2026_09:00")
        assert start == "2026-05-15 09:00:00"

    def test_invalid_returns_empty_strings(self):
        from app.core.langgraph.tools.crm import _parse_appointment_datetime

        start, end = _parse_appointment_datetime("not-a-date")
        assert start == ""
        assert end == ""

    def test_appointment_is_one_hour_long(self):
        from app.core.langgraph.tools.crm import _parse_appointment_datetime

        start, end = _parse_appointment_datetime("15/05/2026 14:30")
        start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
        delta = (end_dt - start_dt).total_seconds()
        assert delta == 3600


class TestGetCitas:
    @pytest.mark.asyncio
    async def test_returns_meetings_for_patient(self):
        from app.core.langgraph.tools.crm import get_citas

        person = {"data": [{"id": 99}]}
        leads = {
            "data": [
                {
                    "id": 77,
                    "person": {"emails": [{"value": "591700000000@whatsapp.sofopolis.net"}]},
                }
            ]
        }
        activities = {
            "data": [
                {
                    "id": 1,
                    "type": "meeting",
                    "title": "Ana - Limpieza",
                    "schedule_from": "2026-05-15 09:00:00",
                    "schedule_to": "2026-05-15 10:00:00",
                    "is_done": 0,
                    "comment": "Limpieza",
                    "participants": [{"person": {"name": "Ana López"}}],
                },
                {
                    "id": 2,
                    "type": "call",  # must be filtered out
                    "title": "Llamada",
                    "schedule_from": "2026-05-16 09:00:00",
                    "schedule_to": "2026-05-16 09:30:00",
                    "is_done": 0,
                },
            ]
        }

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(
                side_effect=[
                    _make_response(200, person),
                    _make_response(200, leads),
                    _make_response(200, activities),
                ]
            )
            cls.return_value = client

            result = json.loads(await get_citas.ainvoke({"wa_id": "591700000000"}))
            assert len(result["citas"]) == 1  # only meeting, not call
            assert result["citas"][0]["title"] == "Ana - Limpieza"

    @pytest.mark.asyncio
    async def test_returns_empty_when_person_not_found(self):
        from app.core.langgraph.tools.crm import get_citas

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, {"data": []}))
            cls.return_value = client

            result = json.loads(await get_citas.ainvoke({"wa_id": "591799999999"}))
            assert result["citas"] == []
            assert "patient_not_found" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_returns_error_on_exception(self):
        from app.core.langgraph.tools.crm import get_citas

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=Exception("timeout"))
            cls.return_value = client

            result = json.loads(await get_citas.ainvoke({"wa_id": "591700000000"}))
            assert "error" in result
