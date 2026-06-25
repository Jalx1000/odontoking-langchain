"""Unit tests for the rewritten get_doctor_schedule tool.

Endpoint under test:
    GET {CRM_BASE_URL}/api/doctors/{id}/available-slots
        ?date=YYYY-MM-DD
        &days=<int>
        &duration_minutes=<int>

Key contract:
- Authenticated (Bearer token) — availability comes from SMD ∩ jornada − citas.
- Returns {"doctor_id": int, "schedule": [...], "days_queried": int}.
- 404  → {"error": "doctor_not_found", ...}  — no exception raised.
- 422  → {"error": "invalid_parameters", "slots": []}  — no exception raised.
- 5xx/429  → retried up to 3 times, then returns error.
- date query param is today (YYYY-MM-DD).
- Old endpoints (/api/disponibilidad, the local /api/doctors/{id}/slots) are
  never called.

Note: pytest-asyncio is not installed in this project. All async tests are run
via asyncio.run() inside synchronous pytest functions — the same strategy used
in test_get_services.py.
"""

import asyncio
import json
from datetime import date as _date
from unittest.mock import AsyncMock, MagicMock, patch

import httpx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status: int, data) -> MagicMock:
    """Build a mock httpx.Response with raise_for_status wired up correctly."""
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
    """Wire the async context-manager protocol onto a mock client."""
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _slots_payload(doctor_id: int = 5, slots: list | None = None, days: int = 7) -> dict:
    """Build a realistic /slots API response matching the real CRM endpoint shape."""
    if slots is None:
        slots = [
            {"start_time": "08:30", "end_time": "09:30", "status": "available"},
            {"start_time": "09:30", "end_time": "10:30", "status": "available"},
        ]
    schedule = [{"date": "2026-05-26", "slots": slots}] if slots else []
    return {
        "doctor_id": doctor_id,
        "from": "2026-05-25",
        "days": days,
        "duration_minutes": 60,
        "source": "smd",
        "degraded": False,
        "reason": None,
        "schedule": schedule,
    }


def _today() -> str:
    return _date.today().isoformat()


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


async def _invoke(id_doctor: int, **kwargs) -> dict:
    """Import and ainvoke get_doctor_schedule, parse JSON."""
    from app.core.langgraph.tools.odontoking import get_doctor_schedule

    payload = {"id_doctor": id_doctor, **kwargs}
    raw = await get_doctor_schedule.ainvoke(payload)
    return json.loads(raw)


def invoke(id_doctor: int, **kwargs) -> dict:
    """Synchronous entry point for all tests."""
    return _run(_invoke(id_doctor, **kwargs))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_returns_slots_on_success(self):
        """Tool returns parsed slots when the API responds with 200."""
        resp = _make_response(200, _slots_payload(doctor_id=5))

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = invoke(5)

        assert result["doctor_id"] == 5
        assert isinstance(result["schedule"], list)
        assert len(result["schedule"]) == 1  # one day with slots

    def test_duration_minutes_forwarded_as_query_param(self):
        """duration_minutes must appear in the request's query params."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, _slots_payload()))
            cls.return_value = client

            invoke(5, duration_minutes=30)

            call_kwargs = client.get.call_args[1]
            assert call_kwargs["params"]["duration_minutes"] == 30

    def test_days_7_forwarded(self):
        """days=7 must appear in the request's query params."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, _slots_payload(days=7)))
            cls.return_value = client

            invoke(5, days=7)

            call_kwargs = client.get.call_args[1]
            assert call_kwargs["params"]["days"] == 7

    def test_days_1_forwarded(self):
        """Minimum boundary: days=1 is forwarded correctly."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, _slots_payload(days=1)))
            cls.return_value = client

            invoke(5, days=1)

            call_kwargs = client.get.call_args[1]
            assert call_kwargs["params"]["days"] == 1

    def test_days_30_forwarded(self):
        """Maximum allowed boundary: days=30 is forwarded correctly."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, _slots_payload(days=30)))
            cls.return_value = client

            invoke(5, days=30)

            call_kwargs = client.get.call_args[1]
            assert call_kwargs["params"]["days"] == 30

    def test_today_used_as_date_query_param(self):
        """The date query param must be today in YYYY-MM-DD format."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, _slots_payload()))
            cls.return_value = client

            invoke(5)

            call_kwargs = client.get.call_args[1]
            assert call_kwargs["params"]["date"] == _today()

    def test_response_structure_matches_contract(self):
        """Result must have doctor_id (int), schedule (list), days_queried (int)."""
        payload = _slots_payload(doctor_id=7, days=7)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, payload))
            cls.return_value = client

            result = invoke(7)

        assert "doctor_id" in result
        assert "schedule" in result
        assert "days_queried" in result
        assert isinstance(result["doctor_id"], int)
        assert isinstance(result["schedule"], list)
        assert isinstance(result["days_queried"], int)

    def test_duration_minutes_30_forwarded(self):
        """duration_minutes=30 edge case is forwarded correctly."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, _slots_payload()))
            cls.return_value = client

            invoke(5, duration_minutes=30)

            call_kwargs = client.get.call_args[1]
            assert call_kwargs["params"]["duration_minutes"] == 30


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_slots_list_returned_gracefully(self):
        """API returns all days with slots=[] — tool must return empty schedule, not error."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, _slots_payload(slots=[])))
            cls.return_value = client

            result = invoke(5)

        assert "error" not in result
        assert result["schedule"] == []

    def test_days_1_returns_single_day_result(self):
        """days=1 sends correct param and propagates days_queried=1 in result."""
        single_day_slots = [{"start_time": "09:00", "end_time": "10:00", "status": "available"}]
        payload = _slots_payload(slots=single_day_slots, days=1)

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, payload))
            cls.return_value = client

            result = invoke(5, days=1)

        call_kwargs = client.get.call_args[1]
        assert call_kwargs["params"]["days"] == 1
        assert result["days_queried"] == 1
        assert len(result["schedule"]) == 1

    def test_duration_minutes_default_is_60(self):
        """When duration_minutes is not provided, defaults to 60."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, _slots_payload()))
            cls.return_value = client

            invoke(5)

            call_kwargs = client.get.call_args[1]
            assert call_kwargs["params"]["duration_minutes"] == 60

    def test_days_default_is_7(self):
        """When days is not provided, defaults to 7."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, _slots_payload()))
            cls.return_value = client

            invoke(5)

            call_kwargs = client.get.call_args[1]
            assert call_kwargs["params"]["days"] == 7


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_404_returns_doctor_not_found_without_raising(self):
        """404 must produce {"error": "doctor_not_found", ...} — no exception raised."""
        resp = _make_response(404, {"message": "Not found"})

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = invoke(999)

        assert "error" in result
        assert result["error"] == "doctor_not_found"

    def test_422_returns_invalid_parameters_without_raising(self):
        """422 must produce {"error": "invalid_parameters", "slots": []} — no exception."""
        resp = _make_response(422, {"detail": "Invalid date format"})

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = invoke(5)

        assert result["error"] == "invalid_parameters"
        assert result["slots"] == []

    def test_500_retried_and_succeeds_on_third_attempt(self):
        """5xx errors must be retried; tool succeeds when third attempt returns 200."""
        fail_resp = _make_response(500, {"message": "Internal Server Error"})
        ok_resp = _make_response(200, _slots_payload())

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            # Fail twice, succeed on the third call
            client.get = AsyncMock(side_effect=[fail_resp, fail_resp, ok_resp])
            cls.return_value = client

            result = invoke(5)

        assert "error" not in result
        assert result["doctor_id"] == 5

    def test_500_exhausts_retries_returns_error(self):
        """When all retries are exhausted (3x 500), an error dict is returned."""
        fail_resp = _make_response(500, {"message": "Internal Server Error"})

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            # Always fail — more entries than any retry count
            client.get = AsyncMock(side_effect=[fail_resp] * 10)
            cls.return_value = client

            result = invoke(5)

        assert "error" in result

    def test_429_retried_and_succeeds(self):
        """429 Too Many Requests must trigger retry behavior."""
        fail_resp = _make_response(429, {"message": "Too Many Requests"})
        ok_resp = _make_response(200, _slots_payload())

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=[fail_resp, fail_resp, ok_resp])
            cls.return_value = client

            result = invoke(5)

        assert "error" not in result
        assert "schedule" in result

    def test_network_exception_returns_error(self):
        """Unexpected network failures must return an error dict, not raise."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=Exception("network unreachable"))
            cls.return_value = client

            result = invoke(5)

        assert "error" in result


# ---------------------------------------------------------------------------
# Security — the available-slots endpoint is authenticated
# ---------------------------------------------------------------------------


class TestAuthHeader:
    def test_authorization_bearer_header_sent(self):
        """available-slots is authenticated — a Bearer Authorization header must be sent."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, _slots_payload()))
            cls.return_value = client

            invoke(5)

        headers = client.get.call_args[1].get("headers", {})
        assert "Authorization" in headers, (
            "get_doctor_schedule must send the Bearer token — available-slots is authenticated"
        )
        assert headers["Authorization"].startswith("Bearer ")


# ---------------------------------------------------------------------------
# Regression — correct endpoint must be called
# ---------------------------------------------------------------------------


class TestEndpointRegression:
    def test_calls_available_slots_endpoint(self):
        """Must call /api/doctors/{id}/available-slots — the SMD-backed availability endpoint."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, _slots_payload(doctor_id=5)))
            cls.return_value = client

            invoke(5)

        called_url: str = client.get.call_args[0][0]
        assert "/available-slots" in called_url, (
            f"Expected URL to contain /available-slots, got: {called_url}"
        )

    def test_does_not_call_local_slots_or_bare_doctor_url(self):
        """Neither the local /api/doctors/{id}/slots nor the bare /api/doctors/{id} may be called.

        Regression for the old implementations: /api/doctors/5 (doctor detail) and the
        local-only /api/doctors/5/slots (no ShareMeData availability).
        """
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, _slots_payload(doctor_id=5)))
            cls.return_value = client

            invoke(5)

        called_url: str = client.get.call_args[0][0]
        import re
        assert not re.search(r"/api/doctors/\d+$", called_url), (
            f"Called the old bare doctor-detail URL: {called_url}"
        )
        assert not re.search(r"/api/doctors/\d+/slots$", called_url), (
            f"Called the old local-only /slots URL (no SMD): {called_url}"
        )

    def test_does_not_call_disponibilidad_endpoint(self):
        """Must not call the old /api/disponibilidad endpoint."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, _slots_payload()))
            cls.return_value = client

            invoke(5)

        called_url: str = client.get.call_args[0][0]
        assert "disponibilidad" not in called_url, (
            f"get_doctor_schedule must not call /api/disponibilidad. Called: {called_url}"
        )

    def test_url_contains_doctor_id(self):
        """The doctor id must appear in the URL path."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, _slots_payload(doctor_id=42)))
            cls.return_value = client

            invoke(42)

        called_url: str = client.get.call_args[0][0]
        assert "42" in called_url, (
            f"Doctor ID 42 must appear in the request URL. Called: {called_url}"
        )

    def test_only_one_http_request_made_on_success(self):
        """On a successful response, exactly one HTTP GET must be issued."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_make_response(200, _slots_payload()))
            cls.return_value = client

            invoke(5)

        assert client.get.call_count == 1, (
            f"Expected 1 HTTP call on success, got {client.get.call_count}"
        )
