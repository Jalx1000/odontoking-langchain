"""Unit tests for the verify_insurance tool."""

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


def _async_client_ctx(mock_client: AsyncMock) -> AsyncMock:
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


VERIFIED_OK = {"verified": True, "status": "VIGENTE", "message": "Membresía activa"}
VERIFIED_NO = {"verified": False, "status": "NO_VIGENTE", "message": "Sin cobertura"}


class TestVerifyInsurance:
    @pytest.mark.asyncio
    async def test_returns_verified_when_api_ok(self):
        from app.core.langgraph.tools.insurance import verify_insurance

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, VERIFIED_OK))
            cls.return_value = client

            result = json.loads(
                await verify_insurance.ainvoke(
                    {"ci_paciente": "1234567", "seguro_paciente": "Membresía Odontoking"}
                )
            )
            assert result["verified"] is True
            client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_not_verified_when_api_returns_no_coverage(self):
        from app.core.langgraph.tools.insurance import verify_insurance

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, VERIFIED_NO))
            cls.return_value = client

            result = json.loads(
                await verify_insurance.ainvoke(
                    {"ci_paciente": "0000", "seguro_paciente": "Nacional Seguro"}
                )
            )
            assert result["verified"] is False
            client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_retry_payload_on_5xx(self):
        from app.core.langgraph.tools.insurance import verify_insurance

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(503, {}))
            cls.return_value = client

            with patch("tenacity.nap.time"):
                result = json.loads(
                    await verify_insurance.ainvoke(
                        {"ci_paciente": "123", "seguro_paciente": "Nacional Seguro"}
                    )
                )
            assert result["has_insurance"] is False
            assert result.get("retry") is True

    @pytest.mark.asyncio
    async def test_4xx_does_not_retry(self):
        from app.core.langgraph.tools.insurance import verify_insurance

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(422, {"message": "invalid ci"}))
            cls.return_value = client

            result = json.loads(
                await verify_insurance.ainvoke(
                    {"ci_paciente": "bad", "seguro_paciente": "Alianza"}
                )
            )
            assert result["has_insurance"] is False
            assert result.get("retry") is not True
            assert client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_3_times_on_5xx(self):
        from app.core.langgraph.tools.insurance import verify_insurance

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(500, {}))
            cls.return_value = client

            with patch("tenacity.nap.time"):
                await verify_insurance.ainvoke(
                    {"ci_paciente": "123", "seguro_paciente": "Alianza"}
                )
            assert client.get.call_count == 3

    @pytest.mark.asyncio
    async def test_all_retries_fail_returns_error_payload(self):
        from app.core.langgraph.tools.insurance import verify_insurance

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(500, {}))
            cls.return_value = client

            with patch("tenacity.nap.time"):
                result = json.loads(
                    await verify_insurance.ainvoke(
                        {"ci_paciente": "123", "seguro_paciente": "Alianza"}
                    )
                )
            assert result["has_insurance"] is False
            assert client.get.call_count == 3

    @pytest.mark.asyncio
    async def test_succeeds_after_transient_500(self):
        from app.core.langgraph.tools.insurance import verify_insurance

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(
                side_effect=[_make_response(500, {}), _make_response(200, VERIFIED_OK)]
            )
            cls.return_value = client

            with patch("tenacity.nap.time"):
                result = json.loads(
                    await verify_insurance.ainvoke(
                        {"ci_paciente": "123", "seguro_paciente": "Membresía Odontoking"}
                    )
                )
            assert result["verified"] is True
            assert client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_network_exception_returns_error_payload(self):
        from app.core.langgraph.tools.insurance import verify_insurance

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=Exception("dns failure"))
            cls.return_value = client

            result = json.loads(
                await verify_insurance.ainvoke(
                    {"ci_paciente": "123", "seguro_paciente": "Alianza"}
                )
            )
            assert result["has_insurance"] is False
            assert "error" in result
