"""Unit tests for IMPRIMIR city routing + quote helpers (agent-quotes.md §1–§2).

Pure logic only — no live CRM calls. The HTTP-touching helper (_initial_stage_id) is exercised
against a stubbed _request.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.langgraph.tools import crm
from app.core.langgraph.tools.crm import (
    _build_quote_items,
    _build_quote_line_items,
    _ctx_contact_name,
    _ctx_ids,
    _initial_stage_id,
    _is_unattended,
    _lead_stage_name,
    _resolve_pipeline_id,
    crear_quote,
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


class TestQuoteItems:
    """_build_quote_items normalises items and forces prices to 0 (agent never quotes money)."""

    def test_prices_forced_to_zero_and_sku_generated(self):
        """Prices/totals are 0 and a sku is derived from the name when absent."""
        out = _build_quote_items([{"name": "Bolsa Pouch", "quantity": 5000}])
        assert out == [{"sku": "BOLSA-POUCH", "name": "Bolsa Pouch", "quantity": 5000, "price": 0, "total": 0}]

    def test_provided_sku_kept_and_bad_quantity_defaults(self):
        """An explicit sku is kept; an unparseable quantity defaults to 1."""
        out = _build_quote_items([{"name": "Tarjetas", "quantity": "x", "sku": "TARJ-500"}])
        assert out[0]["sku"] == "TARJ-500"
        assert out[0]["quantity"] == 1

    def test_empty_and_nameless_entries_skipped(self):
        """Entries without a name (and non-dicts) are dropped; empty input yields []."""
        assert _build_quote_items([{"name": "", "quantity": 1}, "junk", 3]) == []
        assert _build_quote_items([]) == []
        assert _build_quote_items(None) == []


class TestQuoteLineItemsValidation:
    """_build_quote_line_items validates items against the catalog and attaches product_id."""

    @pytest.mark.asyncio
    async def test_attaches_product_id_when_resolved(self, monkeypatch):
        """A recognised product gets its product_id; prices stay 0."""
        async def fake_resolve(_client, name):
            return 12 if name == "Bolsa Pouch" else None
        monkeypatch.setattr(crm, "_resolve_product_id", fake_resolve)
        lines, unresolved = await _build_quote_line_items(
            None, [{"name": "Bolsa Pouch", "quantity": 5000}]
        )
        assert lines[0]["product_id"] == 12
        assert lines[0]["price"] == 0 and lines[0]["total"] == 0
        assert unresolved == []

    @pytest.mark.asyncio
    async def test_reports_unresolved_products(self, monkeypatch):
        """A name that matches no catalog product is kept but reported as unresolved."""
        async def fake_resolve(_client, name):
            return 12 if name == "Bolsa Pouch" else None
        monkeypatch.setattr(crm, "_resolve_product_id", fake_resolve)
        lines, unresolved = await _build_quote_line_items(
            None, [{"name": "Bolsa Pouch", "quantity": 10}, {"name": "Producto Inventado", "quantity": 1}]
        )
        assert len(lines) == 2
        assert "product_id" not in lines[1]
        assert unresolved == ["Producto Inventado"]


class TestQuoteSubject:
    """The quote subject is the company name, with a graceful fallback.

    With no company it falls back to the contact name from context, never to a generic
    'Cotización WhatsApp' (the bug seen on Messenger with individual clients).
    """

    def test_ctx_contact_name_prefers_registered(self):
        """Registered name wins over the WhatsApp/Messenger profile name."""
        assert _ctx_contact_name({"metadata": {"nombre_registrado": "Acme", "nombre_whatsapp": "Ana"}}) == "Acme"
        assert _ctx_contact_name({"metadata": {"nombre_whatsapp": "Ana"}}) == "Ana"
        assert _ctx_contact_name({"metadata": {}}) is None

    def _post_capture(self, monkeypatch):
        """Stub product validation + the POST, capturing the request body."""
        monkeypatch.setattr(
            crm, "_build_quote_line_items",
            AsyncMock(return_value=([{"sku": "BANNER", "name": "Banner", "quantity": 1, "price": 0, "total": 0}], [])),
        )
        captured: dict = {}

        async def fake_request(_client, _method, path, **kw):
            captured["path"] = path
            captured["json"] = kw.get("json")
            return MagicMock(json=MagicMock(return_value={"data": {"id": 55}}))

        monkeypatch.setattr(crm, "_request", fake_request)
        return captured

    @pytest.mark.asyncio
    async def test_empty_company_uses_contact_name(self, monkeypatch):
        """No company passed → subject becomes the contact name from context (not 'Cotización WhatsApp')."""
        captured = self._post_capture(monkeypatch)
        cfg = {"metadata": {"conversation_id": 1, "lead_id": 890, "person_id": 412, "nombre_whatsapp": "Ana Pérez"}}
        out = await crear_quote.ainvoke({"nombre_empresa": "", "items": [{"name": "Banner", "quantity": 1}]}, cfg)
        assert captured["json"]["subject"] == "Ana Pérez"
        assert captured["path"] == "/api/v1/quotes"
        assert '"quote_id": 55' in out

    @pytest.mark.asyncio
    async def test_company_used_as_subject_when_present(self, monkeypatch):
        """A company name is used verbatim as the subject."""
        captured = self._post_capture(monkeypatch)
        cfg = {"metadata": {"conversation_id": 1, "person_id": 412, "nombre_whatsapp": "Ana Pérez"}}
        await crear_quote.ainvoke({"nombre_empresa": "Imprenta Sur SRL", "items": [{"name": "Banner", "quantity": 1}]}, cfg)
        assert captured["json"]["subject"] == "Imprenta Sur SRL"


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
