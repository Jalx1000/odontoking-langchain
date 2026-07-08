"""Unit tests for the verify_insurance tool (POST /api/insurance/verify)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.langgraph.tools import insurance


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


def _async_client_ctx(mock_client: AsyncMock) -> AsyncMock:
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


_UNSET = object()


def _patch_person(person=_UNSET):
    """Patch find_person_by_wa_id so the tool resolves (or fails to resolve) a person_id.

    Default resolves to person_id 42; pass person=None to simulate a patient not found in CRM.
    """
    return patch.object(
        insurance,
        "find_person_by_wa_id",
        AsyncMock(return_value={"id": 42} if person is _UNSET else person),
    )


# POST /api/insurance/verify payloads.
VERIFIED_VIGENTE = {
    "status": "VIGENTE",
    "message": "Cobertura activa",
    "success": True,
    "seguro_name": "Alianza",
    "data": {"CI": "10153980", "NOMBRE COMPLETO": "RIVAS CALDERON, MARIA ALEJANDRA", "ESTADO": "VIGENTE"},
}
VERIFIED_NO_REGISTRADO = {
    "status": "NO_REGISTRADO",
    "message": "No registrado",
    "success": True,
    "seguro_name": "Alianza",
    "data": None,
}


class TestVerifyInsurance:
    """verify_insurance against POST /api/insurance/verify (person_id resolved from wa_id)."""

    @pytest.mark.asyncio
    async def test_vigente_returns_has_insurance_true(self):
        """VIGENTE status → has_insurance True and correct POST body."""
        with _patch_person(), patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.post = AsyncMock(return_value=_make_response(200, VERIFIED_VIGENTE))
            cls.return_value = client

            result = json.loads(
                await insurance.verify_insurance.ainvoke(
                    {"wa_id": "591700000001", "ci_paciente": "10153980", "seguro_paciente": "Alianza"}
                )
            )
            assert result["has_insurance"] is True
            assert result["status"] == "VIGENTE"
            assert result["patient_name"] == "RIVAS CALDERON, MARIA ALEJANDRA"
            # Correct endpoint + body.
            call_url = client.post.call_args[0][0]
            assert call_url.endswith("/api/insurance/verify")
            body = client.post.call_args[1]["json"]
            assert body == {"person_id": 42, "ci": "10153980", "insurance_type": "Alianza"}

    @pytest.mark.asyncio
    async def test_no_coverage_returns_has_insurance_false(self):
        """A non-VIGENTE status (NO_REGISTRADO) → has_insurance False."""
        with _patch_person(), patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.post = AsyncMock(return_value=_make_response(200, VERIFIED_NO_REGISTRADO))
            cls.return_value = client

            result = json.loads(
                await insurance.verify_insurance.ainvoke(
                    {"wa_id": "591700000001", "ci_paciente": "0000", "seguro_paciente": "Nacional Vida"}
                )
            )
            assert result["has_insurance"] is False
            assert result["status"] == "NO_REGISTRADO"

    @pytest.mark.asyncio
    async def test_person_not_found_skips_verification(self):
        """No CRM person for wa_id → verification is not attempted."""
        with _patch_person(person=None), patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.post = AsyncMock()
            cls.return_value = client

            result = json.loads(
                await insurance.verify_insurance.ainvoke(
                    {"wa_id": "591700000099", "ci_paciente": "10153980", "seguro_paciente": "Alianza"}
                )
            )
            assert result["has_insurance"] is False
            client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_retry_payload_on_5xx(self):
        """A 5xx from the endpoint returns an error payload flagged retry=True."""
        with _patch_person(), patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.post = AsyncMock(return_value=_make_response(503, {}))
            cls.return_value = client

            with patch("tenacity.nap.time"):
                result = json.loads(
                    await insurance.verify_insurance.ainvoke(
                        {"wa_id": "591700000001", "ci_paciente": "123", "seguro_paciente": "Nacional Vida"}
                    )
                )
            assert result["has_insurance"] is False
            assert result.get("retry") is True

    @pytest.mark.asyncio
    async def test_4xx_does_not_retry(self):
        """A 4xx client error is not retried."""
        with _patch_person(), patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.post = AsyncMock(return_value=_make_response(422, {"message": "invalid ci"}))
            cls.return_value = client

            result = json.loads(
                await insurance.verify_insurance.ainvoke(
                    {"wa_id": "591700000001", "ci_paciente": "bad", "seguro_paciente": "Alianza"}
                )
            )
            assert result["has_insurance"] is False
            assert result.get("retry") is not True
            assert client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_3_times_on_5xx(self):
        """Transient 5xx errors are retried up to 3 times."""
        with _patch_person(), patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.post = AsyncMock(return_value=_make_response(500, {}))
            cls.return_value = client

            with patch("tenacity.nap.time"):
                await insurance.verify_insurance.ainvoke(
                    {"wa_id": "591700000001", "ci_paciente": "123", "seguro_paciente": "Alianza"}
                )
            assert client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_all_retries_fail_returns_error_payload(self):
        """When every retry fails, an error payload is returned."""
        with _patch_person(), patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.post = AsyncMock(return_value=_make_response(500, {}))
            cls.return_value = client

            with patch("tenacity.nap.time"):
                result = json.loads(
                    await insurance.verify_insurance.ainvoke(
                        {"wa_id": "591700000001", "ci_paciente": "123", "seguro_paciente": "Alianza"}
                    )
                )
            assert result["has_insurance"] is False
            assert client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_succeeds_after_transient_500(self):
        """A single transient 500 followed by 200 succeeds."""
        with _patch_person(), patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.post = AsyncMock(
                side_effect=[_make_response(500, {}), _make_response(200, VERIFIED_VIGENTE)]
            )
            cls.return_value = client

            with patch("tenacity.nap.time"):
                result = json.loads(
                    await insurance.verify_insurance.ainvoke(
                        {"wa_id": "591700000001", "ci_paciente": "123", "seguro_paciente": "Membresía Odontoking"}
                    )
                )
            assert result["has_insurance"] is True
            assert client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_network_exception_returns_error_payload(self):
        """A network exception returns an error payload, not a raise."""
        with _patch_person(), patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.post = AsyncMock(side_effect=Exception("dns failure"))
            cls.return_value = client

            result = json.loads(
                await insurance.verify_insurance.ainvoke(
                    {"wa_id": "591700000001", "ci_paciente": "123", "seguro_paciente": "Alianza"}
                )
            )
            assert result["has_insurance"] is False
            assert "error" in result
