"""Unit tests for the Sofopolis CRM tools (save_patient, save_insurance, create_appointment, get_citas)."""

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


# Reusable API response stubs (must be response objects, not plain dicts).
# Leads are matched by person.id (not the number string), so every lead stub carries person.id.
PERSON_NEW = _r({"data": []})
PERSON_EXISTS = _r({"data": [{"id": 99, "name": "Ana López", "emails": [{"value": "591700000000@whatsapp.sofopolis.net"}]}]})
PERSON_CREATED = _make_response(201, {"data": {"id": 99}})
LEADS_EMPTY = _r({"data": []})
# Conversation lead (Consulta stage) — reused by save_patient / save_insurance / ensure_lead_registered.
LEADS_CONSULTA = _r({
    "data": [{"id": 77, "person": {"id": 99}, "lead_pipeline_stage_id": 1}]
})
# An active cita lead (Agendado stage) — used by cancel / get_citas / create_appointment idempotency.
LEADS_AGENDADO = _r({
    "data": [{"id": 77, "person": {"id": 99}, "lead_pipeline_stage_id": 7}]
})
# Back-compat alias: most reuse tests expect the conversation lead.
LEADS_WITH_MATCH = LEADS_CONSULTA
LEAD_CREATED = _make_response(201, {"data": {"id": 77}})
LEAD_UPDATED = _r({"data": {"id": 77}})
ACTIVITY_CREATED = _make_response(201, {"data": {"id": 1}})
# No pre-existing meeting on the lead — the idempotency dup-check GET returns an empty list.
ACTIVITIES_EMPTY = _r({"data": []})


class TestSavePatient:
    """save_patient persists ONLY the patient identity/demographics (person + lead attrs)."""

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
        from app.core.langgraph.tools.crm import save_patient

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_NEW, LEADS_EMPTY])
            client.post = AsyncMock(side_effect=[PERSON_CREATED, LEAD_CREATED])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            cls.return_value = client

            result = json.loads(await save_patient.ainvoke(self._base_args()))
            assert result["success"] is True
            assert result["person_id"] == 99
            assert result["lead_id"] == 77

    @pytest.mark.asyncio
    async def test_finds_existing_person_and_lead(self):
        from app.core.langgraph.tools.crm import save_patient

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_WITH_MATCH])
            client.post = AsyncMock(return_value=_make_response(201, {}))
            client.put = AsyncMock(return_value=LEAD_UPDATED)
            cls.return_value = client

            result = json.loads(await save_patient.ainvoke(self._base_args()))
            assert result["success"] is True
            assert result["person_id"] == 99
            assert result["lead_id"] == 77
            # An existing lead is reused, never created
            client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_persists_age_on_person_record(self):
        from app.core.langgraph.tools.crm import save_patient

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_WITH_MATCH])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            client.post = AsyncMock(return_value=_make_response(201, {}))
            cls.return_value = client

            result = json.loads(await save_patient.ainvoke(self._base_args(edad_paciente=34)))
            assert result["success"] is True
            person_puts = [c for c in client.put.call_args_list if "contacts/persons" in c[0][0]]
            assert person_puts, "age must be PUT to the person record"
            assert person_puts[0].kwargs["json"]["job_title"] == "34"

    @pytest.mark.asyncio
    async def test_tercero_data_written_to_lead_attributes(self):
        from app.core.langgraph.tools.crm import save_patient

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_WITH_MATCH])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            client.post = AsyncMock(return_value=_make_response(201, {}))
            cls.return_value = client

            await save_patient.ainvoke(self._base_args(
                is_for_self=False,
                nombre_paciente_de_otra_persona="Hijo Ejemplo",
                edad_paciente_de_otra_persona=10,
            ))
            attr_puts = [c for c in client.put.call_args_list if "attributes/edit" in c[0][0]]
            assert attr_puts, "tercero data must be PUT to lead attributes"
            body = attr_puts[-1].kwargs["json"]
            assert body["nombre_paciente_de_otra_persona"] == "Hijo Ejemplo"
            assert body["edad_lead"] == "10"

    @pytest.mark.asyncio
    async def test_missing_name_uses_placeholder(self):
        from app.core.langgraph.tools.crm import save_patient

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_NEW, LEADS_EMPTY])
            client.post = AsyncMock(side_effect=[PERSON_CREATED, LEAD_CREATED])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            cls.return_value = client

            await save_patient.ainvoke({"wa_id": "591700000000", "person_name": None})
            person_post_body = client.post.call_args_list[0].kwargs["json"]
            assert person_post_body["name"] == "Paciente WhatsApp"
            assert person_post_body["contact_numbers"][0]["value"] == "591700000000"

    @pytest.mark.asyncio
    async def test_returns_error_payload_on_exception(self):
        from app.core.langgraph.tools.crm import save_patient

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=Exception("network down"))
            cls.return_value = client

            result = json.loads(await save_patient.ainvoke(self._base_args()))
            assert result["success"] is False
            assert "error" in result


class TestSaveInsurance:
    """save_insurance persists ONLY the insurance data (aseguradora + CI + estado) on the lead."""

    def _base_args(self, **overrides) -> dict:
        args = {
            "wa_id": "591700000000",
            "person_name": "Ana López",
            "seguro_de_vida": "Alianza",
        }
        args.update(overrides)
        return args

    @pytest.mark.asyncio
    async def test_registers_insurance_and_ci(self):
        from app.core.langgraph.tools.crm import save_insurance

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_WITH_MATCH])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            cls.return_value = client

            result = json.loads(
                await save_insurance.ainvoke(self._base_args(numero_carnet="1234567", estado_seguro="VIGENTE"))
            )
            assert result["success"] is True
            assert result["lead_id"] == 77
            insurance_puts = [c for c in client.put.call_args_list if c.kwargs.get("json", {}).get("insurance")]
            assert insurance_puts and insurance_puts[0].kwargs["json"]["insurance"] == [1]
            ci_puts = [c for c in client.put.call_args_list if "ci" in c.kwargs.get("json", {})]
            assert ci_puts and ci_puts[0].kwargs["json"]["ci"] == "1234567"
            assert ci_puts[0].kwargs["json"]["estado_seguro_paciente_cita"] == "VIGENTE"

    @pytest.mark.asyncio
    async def test_unknown_insurer_still_writes_ci(self):
        from app.core.langgraph.tools.crm import save_insurance

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_WITH_MATCH])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            cls.return_value = client

            result = json.loads(
                await save_insurance.ainvoke(self._base_args(seguro_de_vida="Desconocida", numero_carnet="999"))
            )
            assert result["success"] is True
            insurance_puts = [c for c in client.put.call_args_list if c.kwargs.get("json", {}).get("insurance")]
            assert not insurance_puts  # unknown insurer → no insurance-id PUT
            ci_puts = [c for c in client.put.call_args_list if "ci" in c.kwargs.get("json", {})]
            assert ci_puts

    @pytest.mark.asyncio
    async def test_returns_error_payload_on_exception(self):
        from app.core.langgraph.tools.crm import save_insurance

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=Exception("boom"))
            cls.return_value = client

            result = json.loads(await save_insurance.ainvoke(self._base_args()))
            assert result["success"] is False
            assert "error" in result


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


class TestCreateAppointment:
    """create_appointment books the cita: moves the lead to scheduled + creates the meeting."""

    def _args(self, **overrides) -> dict:
        args = {
            "wa_id": "591700000000",
            "person_name": "Ana López",
            "person_phone": "591700000000",
            "doctor_id": 19,
            "horario_cita": "15/05/2026 09:00",
            "products_name": "Limpieza",
            "products_product_id": 174,
        }
        args.update(overrides)
        return args

    @pytest.mark.asyncio
    async def test_creates_activity(self):
        """Happy path: lead is updated and the meeting activity is POSTed."""
        from app.core.langgraph.tools.crm import create_appointment

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_WITH_MATCH, ACTIVITIES_EMPTY])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            client.post = AsyncMock(return_value=ACTIVITY_CREATED)
            cls.return_value = client

            result = json.loads(await create_appointment.ainvoke(self._args()))
            assert result["success"] is True
            assert result["appointment_registered"] is True
            assert result.get("idempotent") is not True
            assert any("activities" in c[0][0] for c in client.post.call_args_list)

    @pytest.mark.asyncio
    async def test_invalid_datetime_returns_error(self):
        """An unparsable datetime returns an error before any HTTP call."""
        from app.core.langgraph.tools.crm import create_appointment

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_WITH_MATCH])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            client.post = AsyncMock(return_value=_make_response(201, {"data": {"id": 1}}))
            cls.return_value = client

            result = json.loads(await create_appointment.ainvoke(self._args(horario_cita="fecha-invalida")))
            assert result["success"] is False
            assert result["appointment_registered"] is False
            assert result["error_type"] == "invalid_appointment_datetime"
            client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_time_returns_error(self):
        """A missing/blank horario returns a guard error and never touches the API."""
        from app.core.langgraph.tools.crm import create_appointment

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_WITH_MATCH])
            client.post = AsyncMock(return_value=_make_response(201, {}))
            cls.return_value = client

            result = json.loads(await create_appointment.ainvoke(self._args(horario_cita="")))
            assert result["success"] is False
            assert result["appointment_registered"] is False
            assert result["error_type"] == "missing_appointment_fields"
            client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_422_doctor_conflict_returns_graceful_error(self):
        """When doctor is already booked (422), tool returns error payload without raising."""
        from app.core.langgraph.tools.crm import create_appointment

        conflict_resp = MagicMock()
        conflict_resp.status_code = 422
        conflict_resp.text = '{"message":"El doctor ya tiene una cita programada en este horario en el sistema local.","details":[]}'
        conflict_resp.json.return_value = {
            "message": "El doctor ya tiene una cita programada en este horario en el sistema local.",
            "details": [],
        }
        conflict_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_WITH_MATCH, ACTIVITIES_EMPTY])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            # POST /leads (new cita lead) succeeds; POST /activities returns the 422 conflict.
            client.post = AsyncMock(side_effect=[LEAD_CREATED, conflict_resp])
            cls.return_value = client

            result = json.loads(await create_appointment.ainvoke(self._args()))

        assert result["success"] is False
        assert result["appointment_registered"] is False
        assert result["error_type"] == "appointment_conflict"
        assert "doctor" in result["message"].lower() or "cita" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_422_preserves_person_and_lead_ids(self):
        """Even on 422, person_id and lead_id are returned so the agent has context."""
        from app.core.langgraph.tools.crm import create_appointment

        conflict_resp = MagicMock()
        conflict_resp.status_code = 422
        conflict_resp.text = '{"message":"Horario ocupado."}'
        conflict_resp.json.return_value = {"message": "Horario ocupado."}
        conflict_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_WITH_MATCH, ACTIVITIES_EMPTY])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            client.post = AsyncMock(side_effect=[LEAD_CREATED, conflict_resp])
            cls.return_value = client

            result = json.loads(await create_appointment.ainvoke(self._args()))

        assert result.get("person_id") == 99
        assert result.get("lead_id") == 77


class TestCreateAppointmentIdempotency:
    """A confirmed appointment must not create a duplicate meeting for a slot already booked."""

    def _args(self, **overrides) -> dict:
        args = {
            "wa_id": "591700000000",
            "person_name": "Ana López",
            "person_phone": "591700000000",
            "doctor_id": 5,
            "horario_cita": "15/05/2026 09:00",
            "products_name": "Limpieza",
            "products_product_id": 1,
        }
        args.update(overrides)
        return args

    @pytest.mark.asyncio
    async def test_existing_meeting_same_slot_skips_creation(self):
        """A retried confirmation for an already-booked slot returns idempotent, no new POST."""
        from app.core.langgraph.tools.crm import create_appointment

        existing = _r({"data": [
            {"id": 42, "type": "meeting", "schedule_from": "2026-05-15 09:00:00", "is_done": 0},
        ]})
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            # The patient already has an Agendado cita-lead holding a meeting at this slot.
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_AGENDADO, existing])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            client.post = AsyncMock(return_value=ACTIVITY_CREATED)
            cls.return_value = client

            result = json.loads(await create_appointment.ainvoke(self._args()))

        assert result["success"] is True
        assert result["appointment_registered"] is True
        assert result["idempotent"] is True
        # Reuses the existing cita-lead instead of creating a second one.
        assert result["lead_id"] == 77
        # No new lead and no activity POST — the slot was already booked.
        assert client.post.call_count == 0

    @pytest.mark.asyncio
    async def test_existing_meeting_different_slot_still_creates(self):
        """A meeting at a different time does not block booking the requested slot."""
        from app.core.langgraph.tools.crm import create_appointment

        other = _r({"data": [
            {"id": 42, "type": "meeting", "schedule_from": "2026-05-16 11:00:00", "is_done": 0},
        ]})
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_AGENDADO, other])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            client.post = AsyncMock(return_value=ACTIVITY_CREATED)
            cls.return_value = client

            result = json.loads(await create_appointment.ainvoke(self._args()))

        assert result["appointment_registered"] is True
        assert result.get("idempotent") is not True
        assert any("activities" in c[0][0] for c in client.post.call_args_list)

    @pytest.mark.asyncio
    async def test_done_meeting_same_slot_does_not_block(self):
        """A completed (is_done) meeting at the same time is a past visit, not a duplicate."""
        from app.core.langgraph.tools.crm import create_appointment

        done = _r({"data": [
            {"id": 42, "type": "meeting", "schedule_from": "2026-05-15 09:00:00", "is_done": 1},
        ]})
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_AGENDADO, done])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            client.post = AsyncMock(return_value=ACTIVITY_CREATED)
            cls.return_value = client

            result = json.loads(await create_appointment.ainvoke(self._args()))

        assert result["appointment_registered"] is True
        assert result.get("idempotent") is not True
        assert any("activities" in c[0][0] for c in client.post.call_args_list)

    @pytest.mark.asyncio
    async def test_dup_check_failure_falls_open_and_creates(self):
        """If the activities lookup errors, booking proceeds (never blocked by the guard)."""
        from app.core.langgraph.tools.crm import create_appointment

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_AGENDADO, Exception("boom")])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            client.post = AsyncMock(return_value=ACTIVITY_CREATED)
            cls.return_value = client

            result = json.loads(await create_appointment.ainvoke(self._args()))

        assert result["appointment_registered"] is True
        assert any("activities" in c[0][0] for c in client.post.call_args_list)


class TestEnsureLeadRegistered:
    """First contact creates a lead in the Consulta stage; idempotent if one already exists."""

    @pytest.mark.asyncio
    async def test_creates_lead_in_consulta_stage_when_none(self):
        """No existing lead → POST a lead in stage 1 titled 'Consulta - <name>'."""
        from app.core.langgraph.tools.crm import ensure_lead_registered

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=LEADS_EMPTY)
            client.post = AsyncMock(return_value=LEAD_CREATED)
            cls.return_value = client

            result = await ensure_lead_registered("591700000000", 99, "Ana López")

        assert result["success"] is True
        assert result["created"] is True
        assert result["lead_id"] == 77
        body = client.post.call_args.kwargs["json"]
        assert body["lead_pipeline_stage_id"] == 1
        assert body["title"] == "Consulta - Ana López"
        assert body["person"]["id"] == "99"

    @pytest.mark.asyncio
    async def test_skips_creation_when_lead_exists(self):
        """An existing lead is reused, never duplicated (safe against webhook retries)."""
        from app.core.langgraph.tools.crm import ensure_lead_registered

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=LEADS_WITH_MATCH)
            client.post = AsyncMock(return_value=LEAD_CREATED)
            cls.return_value = client

            result = await ensure_lead_registered("591700000000", 99, "Ana López")

        assert result["created"] is False
        assert result["lead_id"] == 77
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_placeholder_when_name_missing(self):
        """With no real name the lead falls back to the 'Paciente WhatsApp' placeholder."""
        from app.core.langgraph.tools.crm import ensure_lead_registered

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=LEADS_EMPTY)
            client.post = AsyncMock(return_value=LEAD_CREATED)
            cls.return_value = client

            await ensure_lead_registered("591700000000", 99, None)

        body = client.post.call_args.kwargs["json"]
        assert body["title"] == "Consulta - Paciente WhatsApp"

    @pytest.mark.asyncio
    async def test_returns_error_on_exception(self):
        """Any failure returns an error payload and never raises (never breaks the webhook)."""
        from app.core.langgraph.tools.crm import ensure_lead_registered

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=Exception("boom"))
            cls.return_value = client

            result = await ensure_lead_registered("591700000000", 99, "Ana López")

        assert result["success"] is False
        assert "error" in result


class TestGetCitas:
    @pytest.mark.asyncio
    async def test_returns_meetings_for_patient(self):
        from app.core.langgraph.tools.crm import get_citas

        person = {"data": [{"id": 99}]}
        # Each cita is its OWN lead. Here the patient has an Agendado cita lead (a real cita) and a
        # Consulta lead (the conversation lead — must be excluded from citas).
        leads = {
            "data": [
                {"id": 77, "person": {"id": 99}, "lead_pipeline_stage_id": 7,
                 "products": {"product_0": {"name": "Limpieza"}}, "description": "Limpieza - Dra. Paz"},
                {"id": 10, "person": {"id": 99}, "lead_pipeline_stage_id": 1},  # Consulta → excluded
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
                    "comment": "Doctor: Dra. Paz",
                    "participants": [{"person": {"name": "Ana López"}}],
                },
                {
                    "id": 2,
                    "type": "call",  # not a meeting — ignored for the datetime
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
            # Only the Agendado cita lead is returned — the Consulta lead is excluded.
            assert len(result["citas"]) == 1
            cita = result["citas"][0]
            assert cita["lead_id"] == 77
            assert cita["estado"] == "Agendado"
            assert cita["servicio"] == "Limpieza"
            assert cita["fecha"] == "2026-05-15"
            assert cita["hora"] == "09:00"

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


class TestSaveInsuranceFallbacks:
    """Regression: save_insurance must not raise when person_name/person_phone are omitted.

    Root cause pattern: the LLM calls save_insurance after verify_insurance with only insurance
    fields, omitting person_name/person_phone. The tool must fall back to the placeholder name
    and wa_id-as-phone instead of dropping the write.
    """

    def _insurance_only_args(self, **overrides) -> dict:
        args = {
            "wa_id": "59176616013",
            "numero_carnet": "12387735",
            "seguro_de_vida": "Membresía Odontoking",
            "estado_seguro": "VIGENTE",
        }
        args.update(overrides)
        return args

    @pytest.mark.asyncio
    async def test_omitting_person_name_and_phone_does_not_raise(self):
        from app.core.langgraph.tools.crm import save_insurance

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_NEW, LEADS_EMPTY])
            client.post = AsyncMock(side_effect=[PERSON_CREATED, LEAD_CREATED])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            cls.return_value = client

            result = json.loads(await save_insurance.ainvoke(self._insurance_only_args()))
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_person_name_none_does_not_raise(self):
        from app.core.langgraph.tools.crm import save_insurance

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_NEW, LEADS_EMPTY])
            client.post = AsyncMock(side_effect=[PERSON_CREATED, LEAD_CREATED])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            cls.return_value = client

            result = json.loads(
                await save_insurance.ainvoke(self._insurance_only_args(person_name=None, person_phone=None))
            )
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_fallback_name_and_phone_used_in_person_post(self):
        from app.core.langgraph.tools.crm import save_insurance

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_NEW, LEADS_EMPTY])
            client.post = AsyncMock(side_effect=[PERSON_CREATED, LEAD_CREATED])
            client.put = AsyncMock(return_value=_make_response(200, {}))
            cls.return_value = client

            await save_insurance.ainvoke(self._insurance_only_args())

            person_post_body = client.post.call_args_list[0].kwargs["json"]
            assert person_post_body["name"] == "Paciente WhatsApp"
            assert person_post_body["contact_numbers"][0]["value"] == "59176616013"


class TestCancelAppointmentTool:
    """The @tool wrapper returns cancel_appointment's result as a JSON string."""

    _ACTIVITIES = _r({
        "data": [
            {"id": 5, "type": "meeting", "schedule_from": "2026-06-23 14:30:00", "is_done": 0},
        ]
    })

    @pytest.mark.asyncio
    async def test_returns_json_string_success(self):
        from app.core.langgraph.tools.crm import cancel_appointment_tool

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_AGENDADO, self._ACTIVITIES])
            client.delete = AsyncMock(return_value=_make_response(200, {}))
            client.put = AsyncMock(return_value=_make_response(200, {}))
            cls.return_value = client

            raw = await cancel_appointment_tool.ainvoke({"wa_id": "591700000000"})
            result = json.loads(raw)
            assert result["success"] is True
            assert result["deleted_activity_id"] == 5


class TestRealNameOrNone:
    """The placeholder name must be treated as 'no name' so onboarding still runs."""

    def test_placeholder_returns_none(self):
        """The auto-created placeholder name is treated as no name."""
        from app.core.langgraph.tools.crm import _real_name_or_none

        assert _real_name_or_none("Paciente WhatsApp") is None

    def test_empty_and_whitespace_return_none(self):
        """Empty or whitespace-only names are treated as no name."""
        from app.core.langgraph.tools.crm import _real_name_or_none

        assert _real_name_or_none("") is None
        assert _real_name_or_none("   ") is None

    def test_none_returns_none(self):
        """A None name stays None."""
        from app.core.langgraph.tools.crm import _real_name_or_none

        assert _real_name_or_none(None) is None

    def test_real_name_is_kept_and_stripped(self):
        """A real name is preserved and stripped of surrounding whitespace."""
        from app.core.langgraph.tools.crm import _real_name_or_none

        assert _real_name_or_none("  Javier Mogro  ") == "Javier Mogro"


class TestCancelAppointment:
    """Cancelling deletes the latest meeting activity and moves the lead to the cancelled stage."""

    _ACTIVITIES = _r({
        "data": [
            {"id": 5, "type": "meeting", "schedule_from": "2026-06-23 14:30:00", "is_done": 0},
            {"id": 6, "type": "note", "schedule_from": "2026-06-20 10:00:00", "is_done": 0},
            {"id": 7, "type": "meeting", "schedule_from": "2026-06-10 09:00:00", "is_done": 1},
        ]
    })

    @pytest.mark.asyncio
    async def test_deletes_meeting_and_cancels_lead(self):
        """The future not-done meeting is deleted and the lead stage moves to cancelled."""
        from app.core.langgraph.tools.crm import cancel_appointment

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[PERSON_EXISTS, LEADS_AGENDADO, self._ACTIVITIES])
            client.delete = AsyncMock(return_value=_make_response(200, {}))
            client.put = AsyncMock(return_value=_make_response(200, {}))
            cls.return_value = client

            result = await cancel_appointment("591700000000")

            assert result["success"] is True
            # the future, not-done meeting (id 5) is the one deleted — not the done one (7)
            assert result["deleted_activity_id"] == 5
            assert client.delete.call_args.args[0].endswith("/api/v1/activities/5")
            stage_calls = [c for c in client.put.call_args_list if "stage" in c[0][0]]
            assert len(stage_calls) == 1

    @pytest.mark.asyncio
    async def test_no_lead_returns_not_found(self):
        """No person/lead for the wa_id returns a not-found result, never raises."""
        from app.core.langgraph.tools.crm import cancel_appointment

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, {"data": []}))
            cls.return_value = client

            result = await cancel_appointment("591799999999")
            assert result["success"] is False
            assert result["error"] == "no_appointment_found"


class TestRenamePerson:
    """Renaming PUTs the new name to the person endpoint, preserving the stored age."""

    @pytest.mark.asyncio
    async def test_puts_new_name_preserving_age(self):
        """The new name is PUT to the person endpoint and the stored age is preserved."""
        from app.core.langgraph.tools.crm import rename_person

        person = _r({"data": [{
            "id": 99, "name": "Ale", "job_title": "24",
            "emails": [{"value": "591700000000@whatsapp.sofopolis.net"}],
            "contact_numbers": [{"value": "591700000000"}],
        }]})
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=person)
            client.put = AsyncMock(return_value=_make_response(200, {}))
            cls.return_value = client

            result = await rename_person("591700000000", "Ale - Maria Isabel Galarza")

            assert result["success"] is True
            url = client.put.call_args.args[0]
            payload = client.put.call_args.kwargs["json"]
            assert url.endswith("/api/v1/contacts/persons/99")
            assert payload["name"] == "Ale - Maria Isabel Galarza"
            assert payload["job_title"] == "24"  # existing age preserved

    @pytest.mark.asyncio
    async def test_person_not_found(self):
        """A missing person returns a not-found result, never raises."""
        from app.core.langgraph.tools.crm import rename_person

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, {"data": []}))
            cls.return_value = client

            result = await rename_person("591799999999", "Nuevo Nombre")
            assert result["success"] is False
            assert result["error"] == "person_not_found"


class TestUpdatePersonAge:
    """Person age is persisted via the standard person PUT, never the removed 404 route."""

    @pytest.mark.asyncio
    async def test_puts_age_to_persons_endpoint(self):
        """It PUTs job_title to /contacts/persons/{id} (not the non-existent attributes route)."""
        from app.core.langgraph.tools.crm import _update_person_age

        client = AsyncMock()
        client.put = AsyncMock(return_value=_r({"data": {"id": 99}}))

        await _update_person_age(client, 99, "591700000000", "Ana López", "591700000000", 34)

        url = client.put.call_args.args[0]
        payload = client.put.call_args.kwargs["json"]
        assert url.endswith("/api/v1/contacts/persons/99")
        assert "attributes/edit" not in url  # the old route returned 404 — never use it again
        assert payload["job_title"] == "34"
        assert payload["entity_type"] == "persons"
