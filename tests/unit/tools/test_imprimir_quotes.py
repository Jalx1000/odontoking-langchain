"""Unit tests for IMPRIMIR city routing, lead guards, and the cotizacion tool.

Pure logic + the cotizacion tool with a stubbed httpx client — no live CRM calls.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.langgraph.tools import crm
from app.core.langgraph.tools.crm import (
    _ctx_ids,
    _initial_stage_id,
    _is_unattended,
    _lead_stage_name,
    _marca_de_sku,
    _resolve_pipeline_id,
    crear_cotizacion,
    derivar_a_asesor,
    quien_atiende,
)


class TestCityRouting:
    """_resolve_pipeline_id maps free-text cities to the (non-correlative) pipeline ids."""

    @pytest.mark.parametrize(
        "ciudad,expected",
        [
            ("Santa Cruz", (1, True)),
            ("scz", (1, True)),
            ("Cochabamba", (8, True)),
            ("cbba", (8, True)),
            ("La Paz", (7, True)),
            ("Potosí", (4, True)),
            ("potosi", (4, True)),
            ("Oruro", (6, True)),
            ("Sucre", (9, True)),
        ],
    )
    def test_known_cities(self, ciudad, expected):
        """Each listed city (and its aliases) resolves to its exact pipeline id."""
        assert _resolve_pipeline_id(ciudad) == expected

    @pytest.mark.parametrize("ciudad", ["Tarija", "cualquier cosa", "", None])
    def test_unknown_city_falls_back_to_sin_ciudad(self, ciudad):
        """Unknown/empty city → pipeline 10 (Sin ciudad), NEVER Santa Cruz (keeps metrics clean)."""
        pipeline_id, recognised = _resolve_pipeline_id(ciudad)
        assert pipeline_id == 10
        assert recognised is False


class TestUnattendedGuard:
    """Only the untouched auto-created lead ('No atendido') may be moved/enriched."""

    @pytest.mark.parametrize("name", ["No atendido", "no atendido", "  NO ATENDIDO  "])
    def test_unattended_stage_is_movable(self, name):
        """The initial stage (any case/spacing) is movable."""
        assert _is_unattended({"lead_pipeline_stage": {"name": name}}) is True

    @pytest.mark.parametrize("name", ["En proceso", "Ganado", "Perdido"])
    def test_advanced_stage_is_not_touched(self, name):
        """A lead an advisor already advanced is left alone."""
        assert _is_unattended({"lead_pipeline_stage": {"name": name}}) is False

    def test_unknown_stage_defaults_movable(self):
        """When the stage can't be read, treat as movable (fresh lead is the common case)."""
        assert _is_unattended({}) is True
        assert _lead_stage_name({}) == ""


def _patch_httpx(monkeypatch, *, status=201, payload=None):
    """Patch crm.httpx.AsyncClient with a fake that records the POST; returns the capture dict."""
    box = {"payload": payload if payload is not None else {"quote_id": 1}, "url": None, "json": None}

    class _Resp:
        status_code = status
        headers = {"content-type": "application/json"}

        def json(self):
            return box["payload"]

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            box["url"] = url
            box["json"] = json
            return _Resp()

        async def get(self, url, params=None, headers=None):
            box["url"] = url
            box["params"] = params
            return _Resp()

    monkeypatch.setattr(crm.httpx, "AsyncClient", _Client)
    return box


class TestCrearCotizacion:
    """crear_cotizacion POSTs to the productos cotizacion endpoint; conversation_id comes from config."""

    @pytest.mark.asyncio
    async def test_no_conversation_id_returns_text_error(self):
        """No conversation_id → graceful text error, never a crash (the LLM never passes ids)."""
        out = await crear_cotizacion.ainvoke({"sku": "CM_00002", "cantidad": 25}, {"metadata": {}})
        assert "No hay una conversación activa" in out

    @pytest.mark.asyncio
    async def test_posts_expected_body_and_returns_payload(self, monkeypatch):
        """POSTs sku/cantidad/criterios/nit to /conversations/{id}/cotizacion and returns the JSON."""
        box = _patch_httpx(monkeypatch, status=201, payload={"quote_id": 114, "total": 325})
        out = await crear_cotizacion.ainvoke(
            {
                "sku": "CM_00002", "cantidad": 25,
                "criterios": {"Tamaño": "50 L", "Canal": "HORECA", "Zona": "Santa Cruz"},
                "nit": "1234567019", "empresa": "El Fogón",
            },
            {"metadata": {"conversation_id": 123}},
        )
        assert box["url"].endswith("/api/v1/productos/conversations/123/cotizacion")
        assert box["json"] == {
            "sku": "CM_00002", "cantidad": 25, "marca": "Magia Verde",
            "criterios": {"Tamaño": "50 L", "Canal": "HORECA", "Zona": "Santa Cruz"},
            "nit": "1234567019", "empresa": "El Fogón",
        }
        assert '"quote_id": 114' in out

    @pytest.mark.asyncio
    async def test_non_bag_sku_gets_imprimir_marca(self, monkeypatch):
        """A non-bag product (e.g. Tapas) carries marca 'Imprimir' in the quote body."""
        box = _patch_httpx(monkeypatch)
        await crear_cotizacion.ainvoke(
            {"sku": "CM_00004", "cantidad": 30}, {"metadata": {"conversation_id": 9}}
        )
        assert box["json"]["marca"] == "Imprimir"

    @pytest.mark.asyncio
    async def test_ciudad_travels_in_body_for_advisor_routing(self, monkeypatch):
        """`ciudad` (real city, not Zona) is sent so the CRM can pick the round-robin advisor."""
        box = _patch_httpx(monkeypatch)
        await crear_cotizacion.ainvoke(
            {"sku": "CM_00003", "cantidad": 5, "criterios": {"Color": "Negro"}, "ciudad": "La Paz"},
            {"metadata": {"conversation_id": 9}},
        )
        assert box["json"]["ciudad"] == "La Paz"


class TestQuienAtiende:
    """quien_atiende GETs /productos/responsables read-only and passes the JSON through."""

    @pytest.mark.asyncio
    async def test_gets_responsables_with_sku_and_city(self, monkeypatch):
        """Sends sku+ciudad as query params and returns the CRM payload verbatim."""
        box = _patch_httpx(
            monkeypatch, status=200,
            payload={"cobertura": "local", "siguiente": {"nombre": "Gabriela Marconi"}},
        )
        out = await quien_atiende.ainvoke({"sku": "CM_00003", "ciudad": "La Paz"})
        assert box["url"].endswith("/api/v1/productos/responsables")
        assert box["params"] == {"sku": "CM_00003", "ciudad": "La Paz"}
        assert "Gabriela Marconi" in out

    @pytest.mark.asyncio
    async def test_unknown_sku_returns_404_message(self, monkeypatch):
        """A 404 (unknown SKU) surfaces the CRM message, never crashes."""
        _patch_httpx(monkeypatch, status=404, payload={"message": "No existe un producto con el SKU X."})
        out = await quien_atiende.ainvoke({"sku": "NADA"})
        assert "No existe un producto" in out


class TestDerivarSignal:
    """derivar_a_asesor is a pure signal that now carries sku+ciudad for advisor routing."""

    @pytest.mark.asyncio
    async def test_signal_includes_sku_and_ciudad(self):
        """The returned signal echoes reason/sku/ciudad so the router forwards them to the handoff."""
        out = await derivar_a_asesor.ainvoke(
            {"conversation_id": 2, "reason": "quiere hablar", "sku": "CM_00003", "ciudad": "Santa Cruz"}
        )
        d = json.loads(out)
        assert d["status"] == "handoff_signaled"
        assert d["sku"] == "CM_00003" and d["ciudad"] == "Santa Cruz"

    @pytest.mark.asyncio
    async def test_price_and_total_are_never_sent(self, monkeypatch):
        """The agent must never send a price/total — the CRM resolves them (a model error stays cheap)."""
        box = _patch_httpx(monkeypatch)
        await crear_cotizacion.ainvoke(
            {"sku": "CM_00002", "cantidad": 25}, {"metadata": {"conversation_id": 9}}
        )
        assert "precio" not in box["json"] and "total" not in box["json"]

    @pytest.mark.asyncio
    async def test_422_surfaces_crm_message(self, monkeypatch):
        """A 422 (missing axis / unassociated contact) surfaces the CRM message verbatim to the model."""
        _patch_httpx(monkeypatch, status=422, payload={"message": "Faltan datos para cotizar: Canal."})
        out = await crear_cotizacion.ainvoke(
            {"sku": "CM_00002", "cantidad": 25}, {"metadata": {"conversation_id": 123}}
        )
        assert "Faltan datos para cotizar: Canal." in out


class TestMarcaDeSku:
    """_marca_de_sku: bags are 'Magia Verde', the rest of the catalog is 'Imprimir'."""

    def test_bags_are_magia_verde(self):
        """CM_00002 (Bolsas Magia Verde) → 'Magia Verde' (case/space-insensitive)."""
        assert _marca_de_sku("CM_00002") == "Magia Verde"
        assert _marca_de_sku("  cm_00002 ") == "Magia Verde"

    def test_everything_else_is_imprimir(self):
        """Hules/Stretch/Tapas and any unknown SKU → 'Imprimir'."""
        assert _marca_de_sku("CM_00003") == "Imprimir"
        assert _marca_de_sku("CM_00001") == "Imprimir"
        assert _marca_de_sku("CM_00004") == "Imprimir"
        assert _marca_de_sku("") == "Imprimir"


class TestCtxIds:
    """_ctx_ids reads injected ids from config.metadata (the LLM never sees them)."""

    def test_reads_metadata(self):
        """lead_id/person_id come from config.metadata."""
        cfg = {"metadata": {"lead_id": 86, "person_id": 239}}
        assert _ctx_ids(cfg) == (86, 239)

    def test_missing_config_is_none_none(self):
        """No config / no metadata → (None, None), never a crash."""
        assert _ctx_ids(None) == (None, None)
        assert _ctx_ids({}) == (None, None)


class TestInitialStage:
    """_initial_stage_id resolves the lowest-sort_order stage (never hardcode stage ids)."""

    @pytest.mark.asyncio
    async def test_picks_lowest_sort_order_list(self, monkeypatch):
        """Given a list of stages, the one with the smallest sort_order wins."""
        resp = MagicMock()
        resp.json.return_value = {"data": {"stages": [
            {"id": 41, "sort_order": 1}, {"id": 42, "sort_order": 2}, {"id": 40, "sort_order": 0},
        ]}}
        monkeypatch.setattr(crm, "_request", AsyncMock(return_value=resp))
        assert await _initial_stage_id(MagicMock(), 8) == 40

    @pytest.mark.asyncio
    async def test_picks_lowest_sort_order_dict(self, monkeypatch):
        """Krayin sometimes returns stages as a dict keyed by id — still resolved correctly."""
        resp = MagicMock()
        resp.json.return_value = {"data": {"stages": {
            "a": {"id": 7, "sort_order": 5}, "b": {"id": 3, "sort_order": 1},
        }}}
        monkeypatch.setattr(crm, "_request", AsyncMock(return_value=resp))
        assert await _initial_stage_id(MagicMock(), 9) == 3

    @pytest.mark.asyncio
    async def test_no_stages_returns_none(self, monkeypatch):
        """No stages → None so the caller omits the stage and lets Krayin default it."""
        resp = MagicMock()
        resp.json.return_value = {"data": {"stages": []}}
        monkeypatch.setattr(crm, "_request", AsyncMock(return_value=resp))
        assert await _initial_stage_id(MagicMock(), 10) is None
