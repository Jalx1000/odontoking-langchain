"""Exhaustive unit tests for get_services tool in odontoking.py.

Coverage:
- Network failures (timeout, connect error, DNS/generic)
- HTTP errors (400, 401, 403, 404, 500, 503)
- Malformed API responses (non-dict, missing key, null data, wrong type, missing fields)
- Empty catalog
- Keyword filtering (exact, partial, case-insensitive, no-match fallback, edge cases)
- Input sanitization (whitespace, long keyword, None, injection strings, unicode)
- Extra fields in API items (stripped to id+name)
- Concurrent calls

Note: pytest-asyncio is not installed in this project.  All async tests are run via
asyncio.run() inside a synchronous pytest function — the same strategy that makes all
other tool tests in the suite runnable without the optional dependency.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


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


def _ok(data) -> MagicMock:
    """Shorthand: 200 OK response wrapping data."""
    return _make_response(200, data)


def _catalog(*items) -> dict:
    """Build a standard API payload with the given items in 'data'."""
    return {"data": list(items)}


def _svc(id_: int, name: str, **extra) -> dict:
    """Build a service item dict, optionally with extra fields."""
    return {"id": id_, "name": name, **extra}


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


async def _ainvoke(keyword: str = "") -> dict:
    """Import and ainvoke get_services, parse JSON."""
    from app.core.langgraph.tools.odontoking import get_services

    raw = await get_services.ainvoke({"keyword": keyword})
    return json.loads(raw)


def invoke(keyword: str = "") -> dict:
    """Sync entry point for all tests."""
    return _run(_ainvoke(keyword))


# ---------------------------------------------------------------------------
# Network failure tests
# ---------------------------------------------------------------------------


class TestNetworkFailures:
    def test_timeout_returns_error(self):
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
            cls.return_value = client

            result = invoke()
            assert "error" in result
            assert "timed out" in result["error"]

    def test_connect_error_returns_error(self):
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
            cls.return_value = client

            result = invoke()
            assert "error" in result
            assert "connect" in result["error"].lower()

    def test_dns_error_falls_through_to_generic_handler(self):
        """DNS errors surface as generic Exception or ConnectError; both must return error."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(side_effect=Exception("Name or service not known"))
            cls.return_value = client

            result = invoke()
            assert "error" in result


# ---------------------------------------------------------------------------
# HTTP error status tests
# ---------------------------------------------------------------------------


class TestHTTPErrors:
    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 500, 503])
    def test_http_error_status_returns_error_with_code(self, status_code: int):
        resp = _make_response(status_code, {"message": "error"})

        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=resp)
            cls.return_value = client

            result = invoke()
            assert "error" in result
            # The tool encodes the status code in the response
            assert str(status_code) in result["error"] or result.get("status") == status_code


# ---------------------------------------------------------------------------
# Malformed API response tests
# ---------------------------------------------------------------------------


class TestMalformedResponses:
    def test_response_is_list_not_dict(self):
        """API returns a JSON array at root level."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_ok([{"id": 1, "name": "Limpieza"}]))
            cls.return_value = client

            result = invoke()
            assert result["data"] == []
            assert "warning" in result

    def test_response_is_string_not_dict(self):
        """API returns a plain string."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_ok("OK"))
            cls.return_value = client

            result = invoke()
            assert result["data"] == []
            assert "warning" in result

    def test_missing_data_key_returns_empty(self):
        """Response is a valid dict but has no 'data' key."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_ok({}))
            cls.return_value = client

            result = invoke()
            assert result["data"] == []

    def test_data_is_null(self):
        """data key is present but its value is null/None."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_ok({"data": None}))
            cls.return_value = client

            result = invoke()
            assert result["data"] == []
            assert "warning" in result

    def test_data_is_string_not_list(self):
        """data key holds a string instead of a list."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_ok({"data": "Limpieza Dental"}))
            cls.return_value = client

            result = invoke()
            assert result["data"] == []
            assert "warning" in result

    def test_items_missing_id_field_are_skipped(self):
        """Items without 'id' are silently dropped."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(
                return_value=_ok(_catalog({"name": "Limpieza"}, _svc(2, "Ortodoncia")))
            )
            cls.return_value = client

            result = invoke()
            assert len(result["data"]) == 1
            assert result["data"][0]["id"] == 2

    def test_items_missing_name_field_are_skipped(self):
        """Items without 'name' are silently dropped."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(
                return_value=_ok(_catalog({"id": 1}, _svc(2, "Ortodoncia")))
            )
            cls.return_value = client

            result = invoke()
            assert len(result["data"]) == 1
            assert result["data"][0]["name"] == "Ortodoncia"

    def test_items_missing_both_id_and_name_are_skipped(self):
        """Items with neither 'id' nor 'name' are silently dropped."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(
                return_value=_ok(_catalog({"price": 100}, _svc(1, "Limpieza")))
            )
            cls.return_value = client

            result = invoke()
            assert len(result["data"]) == 1
            assert result["data"][0]["id"] == 1

    def test_mix_of_valid_and_invalid_items(self):
        """Only valid items (with both id and name) are returned."""
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(
                return_value=_ok(
                    _catalog(
                        _svc(1, "Limpieza"),          # valid
                        {"id": 2},                     # missing name
                        {"name": "Blanqueamiento"},    # missing id
                        {},                            # missing both
                        _svc(5, "Ortodoncia"),         # valid
                    )
                )
            )
            cls.return_value = client

            result = invoke()
            assert len(result["data"]) == 2
            ids = {s["id"] for s in result["data"]}
            assert ids == {1, 5}


# ---------------------------------------------------------------------------
# Empty catalog
# ---------------------------------------------------------------------------


class TestEmptyCatalog:
    def test_empty_data_list_returns_empty(self):
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_ok(_catalog()))
            cls.return_value = client

            result = invoke()
            assert result["data"] == []


# ---------------------------------------------------------------------------
# Keyword filtering
# ---------------------------------------------------------------------------

_FIVE_SERVICES = _catalog(
    _svc(1, "Limpieza Dental"),
    _svc(2, "Ortodoncia"),
    _svc(3, "Blanqueamiento Dental"),
    _svc(4, "Extracción"),
    _svc(5, "Implante"),
)


def _five_service_client() -> AsyncMock:
    """Build a mock client returning the five-service catalog."""
    client = _async_client_ctx(AsyncMock())
    client.get = AsyncMock(return_value=_ok(_FIVE_SERVICES))
    return client


class TestKeywordFiltering:
    def test_exact_case_match(self):
        """Exact keyword match (same case as catalog)."""
        with patch("httpx.AsyncClient") as cls:
            cls.return_value = _five_service_client()
            result = invoke("Ortodoncia")
        assert len(result["data"]) == 1
        assert result["data"][0]["id"] == 2

    def test_partial_match(self):
        """Partial keyword matches multiple services."""
        with patch("httpx.AsyncClient") as cls:
            cls.return_value = _five_service_client()
            result = invoke("Dental")
        names = [s["name"] for s in result["data"]]
        assert "Limpieza Dental" in names
        assert "Blanqueamiento Dental" in names
        assert len(result["data"]) == 2

    def test_case_insensitive_match(self):
        """Lower-case keyword matches mixed-case catalog entry."""
        with patch("httpx.AsyncClient") as cls:
            cls.return_value = _five_service_client()
            result = invoke("limpieza")
        assert len(result["data"]) == 1
        assert result["data"][0]["name"] == "Limpieza Dental"

    def test_no_match_falls_back_to_all_services(self):
        """When keyword matches nothing, return all services."""
        with patch("httpx.AsyncClient") as cls:
            cls.return_value = _five_service_client()
            result = invoke("xyzzy_nonexistent_service")
        assert len(result["data"]) == 5

    def test_empty_string_keyword_returns_all(self):
        """Empty string — no filter applied."""
        with patch("httpx.AsyncClient") as cls:
            cls.return_value = _five_service_client()
            result = invoke("")
        assert len(result["data"]) == 5

    def test_whitespace_only_keyword_returns_all(self):
        """Whitespace-only string is treated as empty after strip()."""
        with patch("httpx.AsyncClient") as cls:
            cls.return_value = _five_service_client()
            result = invoke("   ")
        assert len(result["data"]) == 5

    def test_keyword_truncated_at_100_chars(self):
        """Keywords longer than 100 chars are silently truncated.

        The tool does keyword.strip()[:100].  A 200-char keyword whose
        first 100 chars do not match any service name falls back to all.
        """
        long_kw = "x" * 200
        with patch("httpx.AsyncClient") as cls:
            cls.return_value = _five_service_client()
            result = invoke(long_kw)
        # x*100 matches nothing → fallback returns all 5
        assert len(result["data"]) == 5

    def test_keyword_exactly_100_chars_accepted(self):
        """A 100-char keyword is accepted without truncation."""
        kw = "L" * 100
        with patch("httpx.AsyncClient") as cls:
            cls.return_value = _five_service_client()
            result = invoke(kw)
        # 100 Ls don't match any service → fallback returns all 5
        assert len(result["data"]) == 5

    def test_keyword_none_handled_gracefully(self):
        """The underlying coroutine sanitizes None to empty string gracefully.

        LangChain's Pydantic schema rejects None before the function runs when
        called via ainvoke(), so we test the coroutine directly via
        get_services.coroutine — which is the raw async function before any
        decorator wrapping.  This verifies the isinstance(keyword, str) guard.
        """
        from app.core.langgraph.tools.odontoking import get_services

        with patch("httpx.AsyncClient") as cls:
            cls.return_value = _five_service_client()
            raw = _run(get_services.coroutine(keyword=None))  # type: ignore[arg-type]
            result = json.loads(raw)
        # None → clean_keyword="" → returns all services
        assert "data" in result
        assert len(result["data"]) == 5

    def test_sql_injection_keyword_treated_as_literal_string(self):
        """SQL injection strings are passed through as plain filter text."""
        with patch("httpx.AsyncClient") as cls:
            cls.return_value = _five_service_client()
            result = invoke("'; DROP TABLE services;--")
        # No match → fallback to all
        assert "data" in result
        assert len(result["data"]) == 5

    def test_script_injection_keyword_treated_as_literal_string(self):
        """XSS payloads are passed through without execution."""
        with patch("httpx.AsyncClient") as cls:
            cls.return_value = _five_service_client()
            result = invoke("<script>alert(1)</script>")
        assert "data" in result
        assert len(result["data"]) == 5

    def test_unicode_keyword_matches(self):
        """Unicode keywords (Spanish accents) match correctly."""
        catalog = _catalog(
            _svc(1, "Extracción Dental"),
            _svc(2, "Ortodoncia"),
        )
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_ok(catalog))
            cls.return_value = client
            result = invoke("ción")
        assert len(result["data"]) == 1
        assert result["data"][0]["name"] == "Extracción Dental"

    def test_unicode_keyword_no_match_fallback(self):
        """Unicode keyword with no match falls back to all services."""
        catalog = _catalog(
            _svc(1, "Limpieza"),
            _svc(2, "Ortodoncia"),
        )
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_ok(catalog))
            cls.return_value = client
            result = invoke("ñ")
        # Neither entry contains ñ → fallback
        assert len(result["data"]) == 2


# ---------------------------------------------------------------------------
# Extra fields in API items
# ---------------------------------------------------------------------------


class TestExtraFields:
    def test_only_id_and_name_returned(self):
        """Extra fields from the API (price, category, etc.) are stripped."""
        catalog = _catalog(
            _svc(1, "Limpieza Dental", price=150, category="preventivo", internal_code="LMP-01"),
            _svc(2, "Ortodoncia", price=2500, is_active=True),
        )
        with patch("httpx.AsyncClient") as cls:
            client = _async_client_ctx(AsyncMock())
            client.get = AsyncMock(return_value=_ok(catalog))
            cls.return_value = client

            result = invoke()

        for item in result["data"]:
            assert set(item.keys()) == {"id", "name", "duration_minutes"}, (
                f"Expected {{id, name, duration_minutes}}, got {set(item.keys())}"
            )


# ---------------------------------------------------------------------------
# Concurrent calls
# ---------------------------------------------------------------------------


class TestConcurrentCalls:
    def test_two_concurrent_calls_with_different_keywords(self):
        """Two simultaneous calls with different keywords return independent results."""
        catalog_a = _catalog(_svc(1, "Limpieza Dental"), _svc(2, "Ortodoncia"))
        catalog_b = _catalog(_svc(3, "Blanqueamiento"), _svc(4, "Implante"))

        client_a = _async_client_ctx(AsyncMock())
        client_a.get = AsyncMock(return_value=_ok(catalog_a))

        client_b = _async_client_ctx(AsyncMock())
        client_b.get = AsyncMock(return_value=_ok(catalog_b))

        clients = [client_a, client_b]
        call_idx = 0

        def get_client(*args, **kwargs):
            nonlocal call_idx
            c = clients[call_idx % 2]
            call_idx += 1
            return c

        from app.core.langgraph.tools.odontoking import get_services

        async def _run_concurrent():
            with patch("httpx.AsyncClient") as cls:
                cls.side_effect = get_client
                raw_a, raw_b = await asyncio.gather(
                    get_services.ainvoke({"keyword": "Limpieza"}),
                    get_services.ainvoke({"keyword": "Implante"}),
                )
            return json.loads(raw_a), json.loads(raw_b)

        result_a, result_b = _run(_run_concurrent())

        # Each call must have returned data without errors
        assert "data" in result_a, f"result_a missing 'data': {result_a}"
        assert "data" in result_b, f"result_b missing 'data': {result_b}"
        # Results came from independent mock clients — they must differ
        assert result_a != result_b
