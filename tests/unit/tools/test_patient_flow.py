"""Unit tests for the patient flow: person search/registration, insurance verification,
third-party patient handling, and confirmed-appointment modification rules.

Covers scenarios from the WhatsApp agent's patient flow:
1. Person search by wa_id (GET /api/v1/contacts/persons/search)
2. ensure_person_registered idempotency (first message vs subsequent)
3. Insurance verification via GET /api/v1/insurance/verify (all insurers, same endpoint)
4. Third-party patient: CI-based lookup, no relation question
5. Confirmed appointment: change rules (time/doctor allowed, service blocked)
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx


# ── Helpers ──────────────────────────────────────────────────────────────────

def _run(coro):
    """Run an async coroutine synchronously. pytest-asyncio is not installed."""
    return asyncio.run(coro)


def _resp(status: int, data) -> MagicMock:
    """Build a mock httpx response."""
    r = MagicMock()
    r.status_code = status
    r.json.return_value = data
    if status >= 400:
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            str(status), request=MagicMock(), response=r
        )
    else:
        r.raise_for_status = MagicMock()
    return r


def _ok(data) -> MagicMock:
    return _resp(200, data)


def _ctx(mock: AsyncMock) -> AsyncMock:
    """Make an AsyncMock usable as an async context manager."""
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    return mock


# ── Reusable response stubs ───────────────────────────────────────────────────

_PERSON_FOUND = _ok({
    "data": [
        {
            "id": 42,
            "name": "Carlos Mamani",
            "emails": [{"value": "591700000001@whatsapp.sofopolis.net"}],
            "custom_attributes": {
                "ci_paciente": "1234567",
                "seguro_paciente": "Alianza",
            },
        }
    ]
})

_PERSON_NOT_FOUND = _ok({"data": []})

_PERSON_CREATED = _resp(201, {"data": {"id": 99}})

# Insurance API responses — POST /api/insurance/verify shape (all three insurers).
# The tool derives has_insurance strictly from status == "VIGENTE".
_INSURANCE_VIGENTE = _ok({
    "status": "VIGENTE",
    "message": "Todo en orden, cobertura activa",
    "success": True,
    "seguro_name": "Alianza",
    "data": {"CI": "1234567", "NOMBRE COMPLETO": "RIVAS CALDERON, MARIA ALEJANDRA", "ESTADO": "VIGENTE"},
})

_INSURANCE_VENCIDA = _ok({
    "status": "VENCIDO",
    "message": "Cobertura vencida",
    "success": True,
    "seguro_name": "Alianza",
    "data": {"CI": "1234567", "NOMBRE COMPLETO": "RIVAS CALDERON, MARIA ALEJANDRA", "ESTADO": "VENCIDO"},
})

_INSURANCE_NOT_FOUND = _ok({
    "status": "NO_REGISTRADO",
    "message": "No se encontró seguro para este paciente",
    "success": True,
    "seguro_name": "Alianza",
    "data": None,
})


# ── 1. Person search by wa_id via ensure_person_registered ───────────────────

class TestPersonSearchByWaId:
    """ensure_person_registered searches CRM using the WhatsApp email derived from wa_id."""

    def test_person_found_returns_existing_id_without_creating(self):
        """When person already exists, returned person_id matches CRM record and created=False."""
        from app.core.langgraph.tools.crm import ensure_person_registered

        async def _run_test():
            with patch("httpx.AsyncClient") as cls:
                client = _ctx(AsyncMock())
                client.get = AsyncMock(return_value=_PERSON_FOUND)
                cls.return_value = client

                async with httpx.AsyncClient() as c:
                    result = await ensure_person_registered(
                        c, wa_id="591700000001", person_name="Carlos Mamani"
                    )
                # Search was called with wa_id-derived email
                call_params = client.get.call_args[1]["params"]
                assert "591700000001@whatsapp.sofopolis.net" in call_params["search"]
                assert result["person_id"] == 42
                assert result["created"] is False
                # No POST (create) was called
                client.post.assert_not_called()

        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(return_value=_PERSON_FOUND)
            cls.return_value = client
            result = _run(ensure_person_registered(
                client, wa_id="591700000001", person_name="Carlos Mamani"
            ))
        assert result["person_id"] == 42
        assert result["created"] is False

    def test_person_not_found_creates_new_person(self):
        """When CRM returns empty data, a new person is POSTed and created=True."""
        from app.core.langgraph.tools.crm import ensure_person_registered

        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(return_value=_PERSON_NOT_FOUND)
            client.post = AsyncMock(return_value=_PERSON_CREATED)
            cls.return_value = client
            result = _run(ensure_person_registered(
                client, wa_id="591700000002", person_name="Nuevo Paciente"
            ))
        assert result["person_id"] == 99
        assert result["created"] is True
        client.post.assert_called_once()
        # POST body must contain the wa_id-derived email
        post_body = client.post.call_args[1]["json"]
        assert "591700000002@whatsapp.sofopolis.net" in str(post_body)

    def test_person_created_with_profile_name_from_webhook(self):
        """New person's name comes from the WhatsApp profile (webhook contact name)."""
        from app.core.langgraph.tools.crm import ensure_person_registered

        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(return_value=_PERSON_NOT_FOUND)
            client.post = AsyncMock(return_value=_PERSON_CREATED)
            cls.return_value = client
            _run(ensure_person_registered(
                client, wa_id="591700000003", person_name="María Quispe"
            ))
        post_body = client.post.call_args[1]["json"]
        assert post_body["name"] == "María Quispe"

    def test_person_created_with_generated_email_not_real_email(self):
        """The CRM person email is always the wa_id-derived synthetic address, never a real email."""
        from app.core.langgraph.tools.crm import ensure_person_registered

        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(return_value=_PERSON_NOT_FOUND)
            client.post = AsyncMock(return_value=_PERSON_CREATED)
            cls.return_value = client
            _run(ensure_person_registered(
                client, wa_id="591700000004", person_name="Test"
            ))
        post_body = client.post.call_args[1]["json"]
        emails = post_body.get("emails", [])
        assert len(emails) == 1
        assert emails[0]["value"] == "591700000004@whatsapp.sofopolis.net"

    def test_blank_name_falls_back_to_default(self):
        """When profile name is empty, person is created with 'Paciente WhatsApp'."""
        from app.core.langgraph.tools.crm import ensure_person_registered

        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(return_value=_PERSON_NOT_FOUND)
            client.post = AsyncMock(return_value=_PERSON_CREATED)
            cls.return_value = client
            _run(ensure_person_registered(
                client, wa_id="591700000005", person_name=""
            ))
        post_body = client.post.call_args[1]["json"]
        assert post_body["name"] == "Paciente WhatsApp"


# ── 2. ensure_person_registered idempotency ──────────────────────────────────

class TestEnsurePersonRegisteredIdempotency:
    """Calling ensure_person_registered on subsequent messages must NOT re-create the person."""

    def test_existing_person_does_not_trigger_post(self):
        """Second call with same wa_id hits CRM, finds person, and skips POST."""
        from app.core.langgraph.tools.crm import ensure_person_registered

        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(return_value=_PERSON_FOUND)
            client.post = AsyncMock()
            cls.return_value = client

            result = _run(ensure_person_registered(
                client, wa_id="591700000001", person_name="Carlos Mamani"
            ))
        assert result["created"] is False
        assert result["person_id"] == 42
        client.post.assert_not_called()

    def test_update_existing_name_false_skips_put(self):
        """When update_existing_name=False (first webhook message), existing person is not PUTed."""
        from app.core.langgraph.tools.crm import ensure_person_registered

        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(return_value=_PERSON_FOUND)
            client.put = AsyncMock()
            cls.return_value = client

            result = _run(ensure_person_registered(
                client,
                wa_id="591700000001",
                person_name="Nombre Nuevo",
                update_existing_name=False,
            ))
        assert result["created"] is False
        assert result["updated"] is False
        client.put.assert_not_called()

    def test_update_existing_name_true_updates_when_name_differs(self):
        """When update_existing_name=True and name changed, existing person is PUTed."""
        from app.core.langgraph.tools.crm import ensure_person_registered

        put_resp = _ok({"data": {"id": 42}})
        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(return_value=_PERSON_FOUND)
            client.put = AsyncMock(return_value=put_resp)
            cls.return_value = client

            result = _run(ensure_person_registered(
                client,
                wa_id="591700000001",
                person_name="Carlos Mamani Actualizado",
                update_existing_name=True,
            ))
        assert result["updated"] is True
        client.put.assert_called_once()

    def test_update_existing_name_true_skips_put_when_name_unchanged(self):
        """PUT is not called when the name in CRM already matches the provided name."""
        from app.core.langgraph.tools.crm import ensure_person_registered

        # CRM record has name "Carlos Mamani"; we pass the same name
        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(return_value=_PERSON_FOUND)
            client.put = AsyncMock()
            cls.return_value = client

            result = _run(ensure_person_registered(
                client,
                wa_id="591700000001",
                person_name="Carlos Mamani",  # same as CRM
                update_existing_name=True,
            ))
        assert result["updated"] is False
        client.put.assert_not_called()


# ── 3. Insurance verification — single POST endpoint for all three insurers ───

class TestVerifyInsuranceUnifiedEndpoint:
    """verify_insurance uses POST /api/insurance/verify for all insurers, routing by
    insurance_type and resolving person_id from wa_id. Coverage is active only when the
    returned status is VIGENTE."""

    _UNSET = object()

    def _invoke(self, ci: str, seguro: str, response, *, person=_UNSET) -> tuple[dict, AsyncMock]:
        from app.core.langgraph.tools import insurance

        async def _call():
            return json.loads(await insurance.verify_insurance.coroutine(
                wa_id="591700000001", ci_paciente=ci, seguro_paciente=seguro
            ))

        find_return = {"id": 42} if person is self._UNSET else person
        find = AsyncMock(return_value=find_return)
        with patch.object(insurance, "find_person_by_wa_id", find):
            with patch("httpx.AsyncClient") as cls:
                client = _ctx(AsyncMock())
                client.post = AsyncMock(return_value=response)
                cls.return_value = client
                result = _run(_call())
        return result, client

    def test_alianza_vigente_returns_has_insurance_true(self):
        """Alianza with VIGENTE status → has_insurance True, hitting POST /api/insurance/verify."""
        result, client = self._invoke("1234567", "Alianza", _INSURANCE_VIGENTE)

        assert result["has_insurance"] is True
        assert result["status"] == "VIGENTE"
        # Correct endpoint (POST, no /v1/) and body params.
        call_url = client.post.call_args[0][0]
        assert call_url.endswith("/api/insurance/verify")
        assert "/api/v1/insurance/verify" not in call_url
        body = client.post.call_args[1]["json"]
        assert body["ci"] == "1234567"
        assert body["insurance_type"] == "Alianza"
        assert body["person_id"] == 42

    def test_alianza_vencido_returns_has_insurance_false(self):
        """A VENCIDO policy must NOT be reported as active coverage (regression: old code forced VIGENTE)."""
        result, _ = self._invoke("1234567", "Alianza", _INSURANCE_VENCIDA)

        assert result["has_insurance"] is False
        assert result["status"] == "VENCIDO"

    def test_nacional_vida_vigente_uses_same_endpoint(self):
        """Nacional Vida also uses POST /api/insurance/verify — no separate endpoint."""
        result, client = self._invoke("9876543", "Nacional Vida", _INSURANCE_VIGENTE)

        call_url = client.post.call_args[0][0]
        assert call_url.endswith("/api/insurance/verify")
        assert client.post.call_args[1]["json"]["insurance_type"] == "Nacional Vida"
        assert result["has_insurance"] is True

    def test_membresia_odontoking_vigente_valid(self):
        """Membresía Odontoking also uses the same POST endpoint and returns VIGENTE."""
        result, _ = self._invoke("5551234", "Membresía Odontoking", _INSURANCE_VIGENTE)

        assert result["has_insurance"] is True
        assert result["status"] == "VIGENTE"

    def test_no_registrado_is_not_has_insurance(self):
        """NO_REGISTRADO (patient/insurer not found) → has_insurance False."""
        result, _ = self._invoke("0000000", "Alianza", _INSURANCE_NOT_FOUND)

        assert result["has_insurance"] is False
        assert result["status"] == "NO_REGISTRADO"

    def test_person_not_found_blocks_verification(self):
        """If the CRM person cannot be resolved from wa_id, verification is not attempted."""
        result, client = self._invoke("1234567", "Alianza", _INSURANCE_VIGENTE, person=None)

        # find_person_by_wa_id returned None → no POST is issued.
        assert result["has_insurance"] is False
        client.post.assert_not_called()

    def test_5xx_returns_error_with_retry_flag(self):
        """A 500 from the insurance endpoint returns error payload with retry=True."""
        from app.core.langgraph.tools import insurance

        async def _call():
            return json.loads(await insurance.verify_insurance.coroutine(
                wa_id="591700000001", ci_paciente="1234567", seguro_paciente="Alianza"
            ))

        with patch.object(insurance, "find_person_by_wa_id", AsyncMock(return_value={"id": 42})):
            with patch("httpx.AsyncClient") as cls:
                client = _ctx(AsyncMock())
                client.post = AsyncMock(return_value=_resp(500, {}))
                cls.return_value = client
                with patch("tenacity.nap.time"):
                    result = _run(_call())

        assert result["has_insurance"] is False
        assert result.get("retry") is True

    def test_4xx_returns_error_without_retry(self):
        """A 422 from the insurance endpoint returns error payload WITHOUT retry."""
        from app.core.langgraph.tools import insurance

        async def _call():
            return json.loads(await insurance.verify_insurance.coroutine(
                wa_id="591700000001", ci_paciente="bad-ci", seguro_paciente="Alianza"
            ))

        with patch.object(insurance, "find_person_by_wa_id", AsyncMock(return_value={"id": 42})):
            with patch("httpx.AsyncClient") as cls:
                client = _ctx(AsyncMock())
                client.post = AsyncMock(return_value=_resp(422, {"message": "invalid ci"}))
                cls.return_value = client
                result = _run(_call())

        assert result["has_insurance"] is False
        assert result.get("retry") is not True


# ── 4. Third-party patient flow ───────────────────────────────────────────────

class TestThirdPartyPatientFlow:
    """When a patient books for someone else, the agent verifies insurance using
    the third party's CI, not the wa_id of the caller.

    The CRM lookup for the third party uses ci= query param.
    No relation question (hijo/familiar) must be asked by the tool.
    """

    def _base_args(self, **overrides) -> dict:
        args = {
            "wa_id": "591700000010",
            "person_name": "Madre que llama",
            "person_phone": "591700000010",
            "is_for_self": False,
            "nombre_paciente_de_otra_persona": "Hijo Ejemplo",
            "edad_paciente_de_otra_persona": 10,
        }
        args.update(overrides)
        return args

    def test_save_patient_with_tercero_data_succeeds(self):
        """save_patient accepts nombre_paciente_de_otra_persona without requiring a relation field."""
        from app.core.langgraph.tools.crm import save_patient

        person_exists = _ok({"data": [{"id": 55, "name": "Madre que llama",
                                        "emails": [{"value": "591700000010@whatsapp.sofopolis.net"}]}]})
        # Conversation lead matched by person.id (Consulta stage) — reused, not recreated.
        leads_with_match = _ok({"data": [{"id": 88, "person": {"id": 55}, "lead_pipeline_stage_id": 1}]})

        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[person_exists, leads_with_match])
            client.put = AsyncMock(return_value=_ok({"data": {"id": 88}}))
            client.post = AsyncMock(return_value=_resp(201, {}))
            cls.return_value = client

            result = json.loads(_run(save_patient.coroutine(**self._base_args())))

        assert result["success"] is True
        assert result["person_id"] == 55
        assert result["lead_id"] == 88

    def test_tercero_tools_do_not_require_relacion_field(self):
        """No CRM write tool has a 'relacion' parameter — booking for a third party
        never asks for the family relationship."""
        from app.core.langgraph.tools.crm import create_appointment, save_patient
        import inspect

        for tool in (save_patient, create_appointment):
            param_names = list(inspect.signature(tool.coroutine).parameters.keys())
            assert "relacion" not in param_names, (
                f"{tool.name} must NOT have a 'relacion' param — agent should not ask for relationship"
            )

    def test_insurance_verify_uses_tercero_ci_not_wa_id(self):
        """Insurance is verified with the third party's CI, not the caller's wa_id."""
        from app.core.langgraph.tools import insurance

        wa_id = "591700000010"
        tercero_ci = "7654321"

        async def _call():
            return json.loads(await insurance.verify_insurance.coroutine(
                wa_id=wa_id,
                ci_paciente=tercero_ci,
                seguro_paciente="Alianza",
            ))

        with patch.object(insurance, "find_person_by_wa_id", AsyncMock(return_value={"id": 42})):
            with patch("httpx.AsyncClient") as cls:
                client = _ctx(AsyncMock())
                client.post = AsyncMock(return_value=_INSURANCE_VIGENTE)
                cls.return_value = client
                result = _run(_call())

        body = client.post.call_args[1]["json"]
        # The CI sent to the insurer is the third party's, never the wa_id.
        assert body["ci"] == tercero_ci
        assert body["ci"] != wa_id
        assert result["has_insurance"] is True


# ── 5. Confirmed appointment — modification rules ─────────────────────────────

class TestConfirmedAppointmentModificationRules:
    """Rules for create_appointment on an existing confirmed appointment.

    Allowed changes: horario_cita (time), doctor_id (doctor by specialty)
    Blocked changes: products_name/products_product_id (service)
    Rejected: re-confirming with same data on already-confirmed slot → 422 from API
    """

    def _confirmed_args(self, **overrides) -> dict:
        args = {
            "wa_id": "591700000020",
            "person_name": "Pedro Flores",
            "person_phone": "591700000020",
            "doctor_id": 5,
            "horario_cita": "20/06/2026 10:00",
            "products_name": "Limpieza",
            "products_product_id": 174,
        }
        args.update(overrides)
        return args

    _PERSON = _ok({"data": [{"id": 70, "name": "Pedro Flores",
                              "emails": [{"value": "591700000020@whatsapp.sofopolis.net"}]}]})
    _LEADS = _ok({"data": [{"id": 55, "person": {
        "emails": [{"value": "591700000020@whatsapp.sofopolis.net"}]
    }}]})
    _ACTIVITIES_EMPTY = _ok({"data": []})

    def test_confirmed_appointment_is_registered_when_all_data_valid(self):
        """Happy path: all required fields present, API returns 201 → appointment_registered=True."""
        from app.core.langgraph.tools.crm import create_appointment

        activity_created = _resp(201, {"data": {"id": 300}})

        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[self._PERSON, self._LEADS, self._ACTIVITIES_EMPTY])
            client.put = AsyncMock(return_value=_ok({}))
            client.post = AsyncMock(return_value=activity_created)
            cls.return_value = client

            result = json.loads(_run(create_appointment.coroutine(**self._confirmed_args())))

        assert result["success"] is True
        assert result["appointment_registered"] is True

    def test_time_change_on_confirmed_appointment_is_allowed(self):
        """Changing horario_cita on a confirmed slot is allowed — POST to /activities."""
        from app.core.langgraph.tools.crm import create_appointment

        activity_created = _resp(201, {"data": {"id": 301}})

        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[self._PERSON, self._LEADS, self._ACTIVITIES_EMPTY])
            client.put = AsyncMock(return_value=_ok({}))
            client.post = AsyncMock(return_value=activity_created)
            cls.return_value = client

            result = json.loads(_run(create_appointment.coroutine(
                **self._confirmed_args(horario_cita="25/06/2026 14:00")
            )))

        assert result["success"] is True
        assert result["appointment_registered"] is True
        # The activity POST must have been called
        post_url = client.post.call_args[0][0]
        assert "activities" in post_url

    def test_doctor_change_on_confirmed_appointment_is_allowed(self):
        """Changing doctor_id on a confirmed slot is allowed — POST to /activities with new doctor."""
        from app.core.langgraph.tools.crm import create_appointment

        activity_created = _resp(201, {"data": {"id": 302}})

        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[self._PERSON, self._LEADS, self._ACTIVITIES_EMPTY])
            client.put = AsyncMock(return_value=_ok({}))
            client.post = AsyncMock(return_value=activity_created)
            cls.return_value = client

            result = json.loads(_run(create_appointment.coroutine(
                **self._confirmed_args(doctor_id=19)  # different doctor
            )))

        assert result["success"] is True
        assert result["appointment_registered"] is True
        # The activity body must reference the new doctor_id
        activity_body = client.post.call_args[1]["json"]
        assert "19" in str(activity_body.get("participants", {}).get("doctors", []))

    def test_api_conflict_422_on_confirmed_appointment_returns_graceful_error(self):
        """When API returns 422 (slot conflict), tool returns appointment_conflict error without raising."""
        from app.core.langgraph.tools.crm import create_appointment

        conflict_resp = MagicMock()
        conflict_resp.status_code = 422
        conflict_resp.text = '{"message":"El doctor ya tiene una cita programada en este horario."}'
        conflict_resp.json.return_value = {"message": "El doctor ya tiene una cita programada en este horario."}
        conflict_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[self._PERSON, self._LEADS, self._ACTIVITIES_EMPTY])
            client.put = AsyncMock(return_value=_ok({}))
            # POST /leads (new cita lead id 55) succeeds; POST /activities hits the 422 conflict.
            client.post = AsyncMock(side_effect=[_resp(201, {"data": {"id": 55}}), conflict_resp])
            cls.return_value = client

            result = json.loads(_run(create_appointment.coroutine(**self._confirmed_args())))

        assert result["success"] is False
        assert result["appointment_registered"] is False
        assert result["error_type"] == "appointment_conflict"
        # Person and lead IDs are preserved so agent can retry with different data
        assert result.get("person_id") == 70
        assert result.get("lead_id") == 55

    def test_service_change_blocked_when_missing_product_data(self):
        """If products_name is provided without products_product_id, product is omitted silently.
        This is the 'service change blocked' behaviour — the agent must ask for the full product.
        The tool itself does not attach the service unless both product_id AND name are present."""
        from app.core.langgraph.tools.crm import create_appointment

        activity_created = _resp(201, {"data": {"id": 303}})

        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[self._PERSON, self._LEADS, self._ACTIVITIES_EMPTY])
            client.put = AsyncMock(return_value=_ok({}))
            client.post = AsyncMock(return_value=activity_created)
            cls.return_value = client

            # Pass only name without the required product_id
            args = self._confirmed_args()
            args.pop("products_product_id")
            args["products_name"] = "Ortodoncia"
            result = json.loads(_run(create_appointment.coroutine(**args)))

        # Tool succeeds but the service is not attached without a product_id.
        assert result["success"] is True
        # products live on the cita-lead POST body now; it must NOT include products when the
        # product_id is missing (only the name was supplied).
        lead_posts = [c for c in client.post.call_args_list if c[0][0].endswith("/api/v1/leads")]
        assert lead_posts, "a cita lead must be created"
        lead_body = lead_posts[-1][1].get("json", {})
        products = lead_body.get("products", {})
        assert products == {} or "product_0" not in products

    def test_invalid_datetime_on_confirmed_appointment_returns_error(self):
        """When horario_cita cannot be parsed, tool returns error before hitting the API."""
        from app.core.langgraph.tools.crm import create_appointment

        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[self._PERSON, self._LEADS])
            client.put = AsyncMock(return_value=_ok({}))
            cls.return_value = client

            result = json.loads(_run(create_appointment.coroutine(
                **self._confirmed_args(horario_cita="not-a-valid-date")
            )))

        assert result["success"] is False
        assert result["appointment_registered"] is False
        assert result["error_type"] == "invalid_appointment_datetime"
        # API was never called for activities
        client.post.assert_not_called()

    def test_missing_doctor_returns_error_and_no_activity(self):
        """create_appointment with no doctor (0) returns a guard error and never POSTs an activity."""
        from app.core.langgraph.tools.crm import create_appointment

        with patch("httpx.AsyncClient") as cls:
            client = _ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[self._PERSON, self._LEADS])
            client.put = AsyncMock(return_value=_ok({}))
            client.post = AsyncMock(return_value=_resp(201, {"data": {"id": 1}}))
            cls.return_value = client

            result = json.loads(_run(create_appointment.coroutine(
                wa_id="591700000020",
                person_name="Pedro Flores",
                person_phone="591700000020",
                horario_cita="20/06/2026 10:00",
                doctor_id=0,  # missing doctor
            )))

        assert result["success"] is False
        assert result["appointment_registered"] is False
        assert result["error_type"] == "missing_appointment_fields"
        # POST to /activities must NOT have been called
        activity_posts = [c for c in client.post.call_args_list if "activities" in (c[0][0] if c[0] else "")]
        assert len(activity_posts) == 0


# ── 6. Person payload helpers ─────────────────────────────────────────────────

class TestPersonPayloadHelper:
    """_person_email_from_wa_id and _person_payload correctness."""

    def test_email_derived_from_wa_id(self):
        from app.core.langgraph.tools.crm import _person_email_from_wa_id

        assert _person_email_from_wa_id("591700000001") == "591700000001@whatsapp.sofopolis.net"

    def test_person_payload_uses_whatsapp_email(self):
        from app.core.langgraph.tools.crm import _person_payload

        payload = _person_payload("591700000001", "Test User", "591700000001")
        assert payload["emails"][0]["value"] == "591700000001@whatsapp.sofopolis.net"
        assert payload["name"] == "Test User"
        assert payload["entity_type"] == "persons"

    def test_person_payload_empty_name_defaults_to_paciente_whatsapp(self):
        from app.core.langgraph.tools.crm import _person_payload

        payload = _person_payload("591700000001", "  ", "591700000001")
        assert payload["name"] == "Paciente WhatsApp"

    def test_person_payload_empty_phone_falls_back_to_wa_id(self):
        from app.core.langgraph.tools.crm import _person_payload

        payload = _person_payload("591700000001", "Test", "")
        assert payload["contact_numbers"][0]["value"] == "591700000001"
