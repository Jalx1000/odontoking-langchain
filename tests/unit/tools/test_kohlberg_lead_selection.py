"""Tests for Kohlberg lead-selection: stage taxonomy + reuse-vs-new-pedido decision.

Covers the rule the business defined: leads in ACTIVE stages (No atendido, Cliente contactado,
Cliente confirmado) are the in-progress order and may be reused; leads in TERMINAL stages (Cliente
sin interés, Pedidos entregados, Pedidos cancelados, Otros Servicios) are closed, so a returning
client opens a NEW pedido. Confirmed orders reuse only for corrections; a separate order is new.
"""

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.core.langgraph.tools import kohlberg as K


# ── Stage stubs ────────────────────────────────────────────────────────────────

def _lead(stage_id=None, code=None, name=None, products=None):
    """A minimal lead dict shaped like the CRM GET response."""
    stage = {}
    if stage_id is not None:
        stage["id"] = stage_id
    if code is not None:
        stage["code"] = code
    if name is not None:
        stage["name"] = name
    lead = {"id": 100, "lead_pipeline_stage": stage}
    if products is not None:
        lead["products"] = products
    return lead


_TARIJA = K._CITY_STAGES["tarija"]  # no_atendido=1, contactado=66, confirmado=2, entregado=5, cancelado=6, sin_interes=39, otros=58


# ── Taxonomy / classifiers (pure, no I/O) ───────────────────────────────────────

class TestStageTaxonomy:
    def test_contactado_mapped_for_every_city(self):
        for city, s in K._CITY_STAGES.items():
            assert "contactado" in s, f"{city} sin contactado"

    def test_active_vs_terminal_sets_are_disjoint_and_complete(self):
        assert K._STAGE_ACTIVE_IDS.isdisjoint(K._STAGE_TERMINAL_IDS)
        # contactado ids (59..66) live in ACTIVE, never in TERMINAL
        contactado_ids = {s["contactado"] for s in K._CITY_STAGES.values()}
        assert contactado_ids <= K._STAGE_ACTIVE_IDS
        assert contactado_ids.isdisjoint(K._STAGE_TERMINAL_IDS)

    @pytest.mark.parametrize("key", ["sin_interes", "entregado", "cancelado", "otros"])
    def test_is_terminal_true_for_terminal_ids(self, key):
        for s in K._CITY_STAGES.values():
            assert K._is_terminal(_lead(stage_id=s[key])) is True

    @pytest.mark.parametrize("key", ["no_atendido", "contactado", "confirmado"])
    def test_is_terminal_false_for_active_ids(self, key):
        for s in K._CITY_STAGES.values():
            assert K._is_terminal(_lead(stage_id=s[key])) is False

    def test_is_confirmed_only_for_confirmado(self):
        assert K._is_confirmed(_lead(stage_id=_TARIJA["confirmado"])) is True
        assert K._is_confirmed(_lead(stage_id=_TARIJA["no_atendido"])) is False
        assert K._is_confirmed(_lead(stage_id=_TARIJA["contactado"])) is False

    def test_is_unattended_only_for_no_atendido(self):
        assert K._is_unattended(_lead(stage_id=_TARIJA["no_atendido"])) is True
        assert K._is_unattended(_lead(stage_id=_TARIJA["confirmado"])) is False

    def test_is_delivered_only_for_entregado(self):
        assert K._is_delivered(_lead(stage_id=_TARIJA["entregado"])) is True
        assert K._is_delivered(_lead(stage_id=_TARIJA["cancelado"])) is False

    def test_terminal_fallback_by_code_when_no_id(self):
        assert K._is_terminal(_lead(code="pedidos-entregados")) is True
        assert K._is_terminal(_lead(code="otros-servicios")) is True
        assert K._is_terminal(_lead(code="no-atendido")) is False

    def test_terminal_fallback_by_accented_name_when_no_id_no_code(self):
        assert K._is_terminal(_lead(name="Cliente sin interés")) is True
        assert K._is_terminal(_lead(name="Pedidos cancelados")) is True
        assert K._is_terminal(_lead(name="Cliente confirmado")) is False


class TestDraftReset:
    def test_reset_clears_products_keeps_client_data(self):
        draft = {
            "ciudad": "Tarija", "nombre": "Ana", "edad": 30, "interes": "boda",
            "ids": [1, 2], "names": ["A", "B"], "qtys": [1, 1], "_seeded": True,
        }
        K._reset_draft_products(draft)
        assert "ids" not in draft and "names" not in draft and "qtys" not in draft
        assert draft["_seeded"] is True
        # client data survives so the NEXT pedido keeps city/name/age
        assert draft["ciudad"] == "Tarija" and draft["nombre"] == "Ana" and draft["edad"] == 30


# ── Behavioral decision (registrar_pedido / actualizar_pedido) ──────────────────

CFG = {"metadata": {"lead_id": 100, "person_id": 99, "wa_id": "59170000777"}}
CATALOG = [{"id": 201, "price": "100.00", "precio_promocion": "90.00"}]


@pytest.fixture(autouse=True)
def _clean_state():
    """Isolate the per-process caches between tests."""
    K._LAST_LEAD_BY_WA.clear()
    K._DRAFT_BY_WA.clear()
    yield
    K._LAST_LEAD_BY_WA.clear()
    K._DRAFT_BY_WA.clear()


@contextmanager
def _patches(cur_lead, upsert_return):
    """Patch every network helper the tools touch; yields the mocks (_upsert_lead records target+body)."""
    mocks = {
        "_get_lead": AsyncMock(return_value=cur_lead),
        "_get_products_cached": AsyncMock(return_value=CATALOG),
        "_resolve_person": AsyncMock(return_value=99),
        "_set_person_attrs": AsyncMock(return_value=None),
        "_move_lead": AsyncMock(return_value=None),
        "_add_lead_note": AsyncMock(return_value=None),
        "_upsert_lead": AsyncMock(return_value=upsert_return),
    }
    with patch.multiple(K, **mocks):
        yield mocks


class TestRegistrarPedidoTargeting:
    async def test_first_confirm_reuses_no_atendido_draft_no_phantom(self):
        """A no_atendido lead that already carries the live-built products is REUSED on confirm."""
        cur = _lead(stage_id=_TARIJA["no_atendido"], code="no-atendido",
                    products={"product_0": {"product_id": "201", "name": "X", "quantity": 1}})
        with _patches(cur, upsert_return=100) as m:
            res = json.loads(await K.registrar_pedido.coroutine(
                config=CFG, product_id=[201], product_name=["X"], cantidad_product=[1],
                ciudad_del_cliente="Tarija", es_pedido_confirmado=True,
            ))
        target = m["_upsert_lead"].call_args.args[1]
        body = m["_upsert_lead"].call_args.args[2]
        assert target == 100                       # reused, not a new POST
        assert res["nuevo_lead"] is False
        assert body["lead_pipeline_stage_id"] == _TARIJA["confirmado"]

    async def test_confirmed_lead_starts_separate_new_pedido(self):
        """A second order while the lead is already Confirmado POSTs a new lead (pedidos separados)."""
        cur = _lead(stage_id=_TARIJA["confirmado"], code="cliente-confirmado",
                    products={"product_0": {"product_id": "201", "name": "X", "quantity": 1}})
        with _patches(cur, upsert_return=500) as m:
            res = json.loads(await K.registrar_pedido.coroutine(
                config=CFG, product_id=[201], product_name=["X"], cantidad_product=[1],
                ciudad_del_cliente="Tarija", es_pedido_confirmado=True, es_correccion=False,
            ))
        assert m["_upsert_lead"].call_args.args[1] is None   # POST new
        assert res["nuevo_lead"] is True

    async def test_terminal_lead_starts_new_pedido(self):
        """Delivered order + returning client → new pedido, never reuse the terminal lead."""
        cur = _lead(stage_id=_TARIJA["entregado"], code="pedidos-entregados",
                    products={"product_0": {"product_id": "201", "name": "X", "quantity": 1}})
        with _patches(cur, upsert_return=600) as m:
            await K.registrar_pedido.coroutine(
                config=CFG, product_id=[201], product_name=["X"], cantidad_product=[1],
                ciudad_del_cliente="Tarija", es_pedido_confirmado=True,
            )
        assert m["_upsert_lead"].call_args.args[1] is None

    async def test_correction_reuses_remembered_active_lead(self):
        cur = _lead(stage_id=_TARIJA["confirmado"], code="cliente-confirmado",
                    products={"product_0": {"product_id": "201", "name": "X", "quantity": 1}})
        K._LAST_LEAD_BY_WA[CFG["metadata"]["wa_id"]] = 100
        with _patches(cur, upsert_return=100) as m:
            await K.registrar_pedido.coroutine(
                config=CFG, product_id=[201], product_name=["X"], cantidad_product=[2],
                ciudad_del_cliente="Tarija", es_pedido_confirmado=True, es_correccion=True,
            )
        assert m["_upsert_lead"].call_args.args[1] == 100   # PUT onto the same lead

    async def test_cancel_order_with_products_now_works(self):
        """Regression: an order WITH products (no_atendido) can be cancelled (used to fail)."""
        cur = _lead(stage_id=_TARIJA["no_atendido"], code="no-atendido",
                    products={"product_0": {"product_id": "201", "name": "X", "quantity": 1}})
        with _patches(cur, upsert_return=100) as m:
            res = json.loads(await K.registrar_pedido.coroutine(
                config=CFG, ciudad_del_cliente="Tarija", es_pedido_cancelado=True,
            ))
        assert res["cancelado"] is True
        assert m["_move_lead"].call_args.args[3] == "cancelado"

    async def test_cancel_delivered_is_refused(self):
        cur = _lead(stage_id=_TARIJA["entregado"], code="pedidos-entregados")
        with _patches(cur, upsert_return=100) as m:
            res = json.loads(await K.registrar_pedido.coroutine(
                config=CFG, ciudad_del_cliente="Tarija", es_pedido_cancelado=True,
            ))
        assert res["cancelado"] is False
        assert res["note"] == "pedido_entregado_inmutable"
        m["_move_lead"].assert_not_called()


class TestActualizarPedidoTargeting:
    async def test_terminal_lead_posts_new_not_overwrite(self):
        """Live update when the current lead is terminal → NEW lead, never resurrect the closed one."""
        cur = _lead(stage_id=_TARIJA["cancelado"], code="pedidos-cancelados",
                    products={"product_0": {"product_id": "201", "name": "X", "quantity": 1}})
        with _patches(cur, upsert_return=700) as m:
            await K.actualizar_pedido.coroutine(
                config=CFG, ciudad="Tarija", product_id=[201], product_name=["X"], cantidad_product=[1],
            )
        assert m["_upsert_lead"].call_args.args[1] is None   # POST new

    async def test_no_atendido_lead_is_reused(self):
        cur = _lead(stage_id=_TARIJA["no_atendido"], code="no-atendido")
        with _patches(cur, upsert_return=100) as m:
            await K.actualizar_pedido.coroutine(
                config=CFG, ciudad="Tarija", product_id=[201], product_name=["X"], cantidad_product=[1],
            )
        assert m["_upsert_lead"].call_args.args[1] == 100    # PUT onto the in-progress lead
