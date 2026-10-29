"""Unit tests for the deterministic direct-cancel short-circuit (una sola cita activa).

Covers the pure intent/format helpers and the 2-turn flow on the OdontokingAgent:
confirm with the cita detail → on "Sí" cancel directly via the CRM (no form), on "No" keep it.
"""

import pytest

from app.core.cache import cache_service
from app.core.langgraph import odontoking_graph as og
from app.core.langgraph.odontoking_graph import (
    _CANCEL_CONFIRM_PREFIX,
    _fmt_cita_detail,
    _is_cancel_no,
    _is_cancel_yes,
    _single_active_cita,
    _wants_direct_cancel,
)

_WA = "59170000123"


def _cita(estado="Agendado", lead_id=501, fecha="2026-06-18", hora="14:00:00", doctor="Eloy"):
    return {"lead_id": lead_id, "fecha": fecha, "hora": hora, "doctor": doctor,
            "servicio": "Limpieza", "estado": estado}


class TestCancelIntent:
    def test_plain_cancel_is_direct(self):
        """A plain cancel wish counts as a direct-cancel intent."""
        assert _wants_direct_cancel("quiero cancelar mi cita")
        assert _wants_direct_cancel("anúlela por favor")
        assert _wants_direct_cancel("ya no quiero la cita")

    def test_reschedule_is_not_cancel(self):
        """A reschedule wish (even if it says 'cancelar') must NOT be direct-cancel."""
        assert not _wants_direct_cancel("cancele la cita y muévala para mañana")
        assert not _wants_direct_cancel("cámbiela al viernes")
        assert not _wants_direct_cancel("quiero reprogramar mi cita")

    def test_non_cancel_text(self):
        assert not _wants_direct_cancel("hola, ¿qué horarios tienen?")
        assert not _wants_direct_cancel("")

    def test_yes_no_detection(self):
        assert _is_cancel_yes("sí") and _is_cancel_yes("Si, cancélala")
        assert _is_cancel_no("no") and _is_cancel_no("no, mejor no")
        assert not _is_cancel_yes("no")
        assert not _is_cancel_no("sí")


class TestSingleActiveCita:
    def test_exactly_one_active(self):
        citas = [_cita(estado="Cancelado", lead_id=1), _cita(estado="Agendado", lead_id=2)]
        assert _single_active_cita(citas, None)["lead_id"] == 2

    def test_two_active_returns_none(self):
        citas = [_cita(estado="Agendado", lead_id=1), _cita(estado="Agendado", lead_id=2)]
        assert _single_active_cita(citas, None) is None

    def test_no_active_returns_none(self):
        assert _single_active_cita([_cita(estado="Cancelado")], None) is None
        assert _single_active_cita([], None) is None
        assert _single_active_cita(None, None) is None

    def test_fmt_detail_renders_ddmmyyyy(self):
        detail = _fmt_cita_detail(_cita())
        assert "18/06/2026" in detail and "14:00" in detail and "Eloy" in detail


@pytest.mark.asyncio
class TestDirectCancelFlow:
    async def _clear(self):
        await cache_service.delete(f"{_CANCEL_CONFIRM_PREFIX}{_WA}")

    async def test_two_turn_yes_cancels_directly(self, monkeypatch):
        """Turn 1 asks to confirm with detail; turn 2 'Sí' cancels directly via the CRM (no form)."""
        await self._clear()
        calls = {}

        async def _fake_cancel(wa_id, lead_id=None):
            calls["wa_id"], calls["lead_id"] = wa_id, lead_id
            return {"success": True, "lead_id": lead_id}

        monkeypatch.setattr(og, "cancel_appointment", _fake_cancel)
        agent = og.OdontokingAgent()
        citas = [_cita(lead_id=777)]

        # Turn 1: confirmation prompt with the cita detail.
        r1 = await agent._handle_direct_cancel(_WA, "quiero cancelar mi cita", citas, 777)
        assert r1 is not None and "seguro" in r1.lower() and "18/06/2026" in r1
        assert await cache_service.get(f"{_CANCEL_CONFIRM_PREFIX}{_WA}") is not None

        # Turn 2: "Sí" → direct CRM cancel with the remembered lead_id, pending flag cleared.
        r2 = await agent._handle_direct_cancel(_WA, "Sí", citas, 777)
        assert "cancelada" in r2.lower()
        assert calls == {"wa_id": _WA, "lead_id": 777}
        assert await cache_service.get(f"{_CANCEL_CONFIRM_PREFIX}{_WA}") is None

    async def test_two_turn_no_keeps_cita(self, monkeypatch):
        """Turn 2 'No' keeps the cita and never calls the CRM cancel."""
        await self._clear()
        called = {"n": 0}

        async def _fake_cancel(wa_id, lead_id=None):
            called["n"] += 1
            return {"success": True}

        monkeypatch.setattr(og, "cancel_appointment", _fake_cancel)
        agent = og.OdontokingAgent()
        citas = [_cita(lead_id=888)]

        await agent._handle_direct_cancel(_WA, "cancelar", citas, 888)
        r2 = await agent._handle_direct_cancel(_WA, "no", citas, 888)
        assert "sin cambios" in r2.lower()
        assert called["n"] == 0
        assert await cache_service.get(f"{_CANCEL_CONFIRM_PREFIX}{_WA}") is None

    async def test_two_active_citas_falls_through(self):
        """With 2+ active citas there is no single target → fall through to the LLM/form."""
        await self._clear()
        agent = og.OdontokingAgent()
        citas = [_cita(lead_id=1), _cita(lead_id=2)]
        assert await agent._handle_direct_cancel(_WA, "cancelar mi cita", citas, None) is None

    async def test_reschedule_intent_falls_through(self):
        """A reschedule phrasing is never captured by the direct-cancel path."""
        await self._clear()
        agent = og.OdontokingAgent()
        citas = [_cita(lead_id=1)]
        assert await agent._handle_direct_cancel(_WA, "cancélela y muévala al viernes", citas, 1) is None

    async def test_crm_failure_falls_through(self, monkeypatch):
        """If the CRM cancel fails on 'Sí', return None so the LLM can offer the form fallback."""
        await self._clear()

        async def _fail_cancel(wa_id, lead_id=None):
            return {"success": False, "error": "boom"}

        monkeypatch.setattr(og, "cancel_appointment", _fail_cancel)
        agent = og.OdontokingAgent()
        citas = [_cita(lead_id=999)]
        await agent._handle_direct_cancel(_WA, "cancelar", citas, 999)
        assert await agent._handle_direct_cancel(_WA, "sí", citas, 999) is None

    async def test_citas_activas_dependent_preferred_over_wa_id_citas(self, monkeypatch):
        """citas_activas (CRM, incl. dependents) is used even when the wa_id owner has no cita."""
        await self._clear()
        calls = {}

        async def _fake_cancel(wa_id, lead_id=None):
            calls["lead_id"] = lead_id
            return {"success": True, "lead_id": lead_id}

        monkeypatch.setattr(og, "cancel_appointment", _fake_cancel)
        agent = og.OdontokingAgent()
        # wa_id owner has NO citas; the single active cita (a child's) comes from citas_activas.
        hijo = _cita(lead_id=321, doctor="Ana")
        r1 = await agent._handle_direct_cancel(_WA, "quiero cancelar la cita", [], None, [hijo])
        assert r1 is not None and "seguro" in r1.lower()
        r2 = await agent._handle_direct_cancel(_WA, "sí", [], None, [hijo])
        assert "cancelada" in r2.lower()
        assert calls["lead_id"] == 321  # the dependent's cita, not the wa_id owner's latest

    async def test_citas_activas_two_falls_through(self):
        """With 2+ active citas (incl. dependents) there is no single target → fall through."""
        await self._clear()
        agent = og.OdontokingAgent()
        dos = [_cita(lead_id=1), _cita(lead_id=2)]
        assert await agent._handle_direct_cancel(_WA, "cancelar mi cita", [], None, dos) is None
