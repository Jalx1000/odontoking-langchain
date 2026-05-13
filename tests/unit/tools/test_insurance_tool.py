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


PERSON_FOUND = {"data": [{"id": 42, "name": "Juan Pérez"}]}
PERSON_NOT_FOUND = {"data": []}
VERIFIED_OK = {"verified": True, "coverage": "full"}


class TestVerifyInsurance:
    @pytest.mark.asyncio
    async def test_returns_verified_when_person_found_and_api_ok(self):
        from app.core.langgraph.tools.insurance import verify_insurance

        search_resp = _make_response(200, PERSON_FOUND)
        verify_resp = _make_response(200, VERIFIED_OK)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=search_resp)
            client.post = AsyncMock(return_value=verify_resp)
            cls.return_value = client

            result = json.loads(
                await verify_insurance.ainvoke(
                    {"empresa_seguro": "Nacional Vida", "carnet_identidad": "1234567", "wa_id": "591700000000"}
                )
            )
            assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_returns_not_verified_when_person_not_in_crm(self):
        from app.core.langgraph.tools.insurance import verify_insurance

        search_resp = _make_response(200, PERSON_NOT_FOUND)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=search_resp)
            cls.return_value = client

            result = json.loads(
                await verify_insurance.ainvoke(
                    {"empresa_seguro": "Nacional Vida", "carnet_identidad": "0000", "wa_id": "591799999999"}
                )
            )
            assert result["verified"] is False
            assert "person not found" in result["reason"]

    @pytest.mark.asyncio
    async def test_returns_error_payload_on_5xx(self):
        from app.core.langgraph.tools.insurance import verify_insurance

        search_resp = _make_response(200, PERSON_FOUND)
        server_error = _make_response(503, {})

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=search_resp)
            client.post = AsyncMock(return_value=server_error)
            cls.return_value = client

            result = json.loads(
                await verify_insurance.ainvoke(
                    {"empresa_seguro": "Nacional Vida", "carnet_identidad": "123", "wa_id": "591700000001"}
                )
            )
            assert result["verified"] is False
            assert result.get("retry") is True

    @pytest.mark.asyncio
    async def test_4xx_does_not_retry(self):
        from app.core.langgraph.tools.insurance import verify_insurance

        search_resp = _make_response(200, PERSON_FOUND)
        client_error = _make_response(422, {"message": "invalid ci"})

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=search_resp)
            client.post = AsyncMock(return_value=client_error)
            cls.return_value = client

            result = json.loads(
                await verify_insurance.ainvoke(
                    {"empresa_seguro": "Nacional Vida", "carnet_identidad": "bad", "wa_id": "591700000002"}
                )
            )
            assert result["verified"] is False
            # On 4xx we should NOT suggest retry
            assert result.get("retry") is not True
            # The post must have been called only once (no retries on 4xx)
            assert client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_5xx_up_to_3_times(self):
        from app.core.langgraph.tools.insurance import verify_insurance

        search_resp = _make_response(200, PERSON_FOUND)
        server_error = _make_response(500, {})

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=search_resp)
            client.post = AsyncMock(return_value=server_error)
            cls.return_value = client

            # tenacity retries with wait — patch sleep to speed up
            with patch("tenacity.nap.time") as mock_sleep:
                mock_sleep.sleep = MagicMock()
                result = json.loads(
                    await verify_insurance.ainvoke(
                        {"empresa_seguro": "Nacional Vida", "carnet_identidad": "123", "wa_id": "591700000003"}
                    )
                )

            # After max 3 attempts, should still return a safe payload
            assert result["verified"] is False
            assert client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_network_exception_returns_error_payload(self):
        from app.core.langgraph.tools.insurance import verify_insurance

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=Exception("dns failure"))
            cls.return_value = client

            result = json.loads(
                await verify_insurance.ainvoke(
                    {"empresa_seguro": "Nacional Vida", "carnet_identidad": "123", "wa_id": "591700000004"}
                )
            )
            assert result["verified"] is False
            assert "error" in result
