"""Unit tests for the deterministic intake controller (steps 1-6).

Covers the guarantee that the flow is ordered, never skips, parses answers deterministically,
and enforces the insurance barrier — without any network/LLM calls.
"""

import pytest

from app.core.langgraph.intake import (
    advance_intake,
    apply_answer,
    handoff_context,
    is_booking_intent,
    new_state,
    next_pending,
)


async def _vigente(ci, seguro):
    return {"has_insurance": True, "status": "VIGENTE"}


async def _vencido(ci, seguro):
    return {"has_insurance": True, "status": "VENCIDO"}


class TestBookingIntent:
    def test_detects_booking_keywords(self):
        """Messages with booking words trigger the intake."""
        assert is_booking_intent("Quiero agendar una cita")
        assert is_booking_intent("quiero una consulta de ortodoncia")
        assert is_booking_intent("necesito un turno")

    def test_ignores_non_booking(self):
        """Greetings / general questions do not trigger the intake."""
        assert not is_booking_intent("hola buenos días")
        assert not is_booking_intent("¿dónde están ubicados?")


class TestNextPending:
    def test_order_without_seed(self):
        """With nothing known, the first slot is the name."""
        assert next_pending(new_state()) == "nombre"

    def test_seeded_name_skips_to_age(self):
        """A known name is not re-asked."""
        assert next_pending(new_state("Javier Mogro")) == "edad"

    def test_third_party_branch(self):
        """When the cita is for someone else, ask that person's name/age."""
        state = new_state("Javier Mogro")
        state["edad"] = 30
        state["is_for_self"] = False
        assert next_pending(state) == "tercero_nombre"


class TestApplyAnswer:
    def test_age_parsing(self):
        state = new_state("Javier Mogro")
        assert apply_answer(state, "edad", "27 años") is None
        assert state["edad"] == 27

    def test_age_invalid_reasks(self):
        state = new_state("Javier Mogro")
        assert apply_answer(state, "edad", "no sé") is not None
        assert state["edad"] is None

    def test_for_whom_choices(self):
        state = new_state("X")
        apply_answer(state, "para_quien", "Para mí")
        assert state["is_for_self"] is True
        state2 = new_state("X")
        apply_answer(state2, "para_quien", "Para otra persona")
        assert state2["is_for_self"] is False

    def test_seguro_no_insurance_sets_particular(self):
        state = new_state("X")
        apply_answer(state, "seguro", "No tengo seguro")
        assert state["seguro"] == "No tengo seguro"
        assert state["seguro_estado"] == "PARTICULAR"


class TestAdvanceIntakeFlow:
    @pytest.mark.asyncio
    async def test_full_happy_path_with_insurance(self):
        """Ordered 1-6 with a valid insurer ends complete."""
        state = new_state("Javier Mogro")
        r = await advance_intake(state, None)  # start
        assert "edad" in r.reply.lower()

        r = await advance_intake(r.state, "27")
        assert r.state["edad"] == 27 and "para quién" in r.reply.lower()

        r = await advance_intake(r.state, "Para mí")
        assert r.state["is_for_self"] is True

        r = await advance_intake(r.state, "Primera vez")
        assert r.state["es_antiguo"] is False

        r = await advance_intake(r.state, "Membresía Odontoking")
        assert r.state["seguro"] == "Membresía Odontoking"
        assert "carnet" in r.reply.lower()

        r = await advance_intake(r.state, "12387735", verify_fn=_vigente)
        assert r.state["seguro_estado"] == "VIGENTE"
        assert "molestia" in r.reply.lower() or "servicio" in r.reply.lower()

        r = await advance_intake(r.state, "Limpieza")
        assert r.completed is True
        assert r.state["motivo"] == "Limpieza"

    @pytest.mark.asyncio
    async def test_insurance_not_vigente_blocks(self):
        """An expired insurer is blocked and the CI is cleared for retry."""
        state = new_state("Javier Mogro")
        state.update({"edad": 27, "is_for_self": True, "es_antiguo": False, "seguro": "Alianza", "pending": "ci"})
        r = await advance_intake(state, "12387735", verify_fn=_vencido)
        assert r.completed is False
        assert r.state["seguro_estado"] is None
        assert r.state["ci"] is None  # cleared so the patient can resend after regularizing

    @pytest.mark.asyncio
    async def test_no_insurance_skips_ci(self):
        """'No tengo seguro' goes straight to motivo (particular)."""
        state = new_state("Javier Mogro")
        state.update({"edad": 27, "is_for_self": True, "es_antiguo": False, "pending": "seguro"})
        r = await advance_intake(state, "No tengo seguro")
        assert r.state["seguro_estado"] == "PARTICULAR"
        assert "molestia" in r.reply.lower() or "servicio" in r.reply.lower()


class TestHandoffContext:
    def test_includes_collected_data(self):
        state = new_state("Javier Mogro")
        state.update({
            "edad": 27, "is_for_self": True, "es_antiguo": False,
            "seguro": "Membresía Odontoking", "seguro_estado": "VIGENTE",
            "ci": "12387735", "motivo": "Limpieza",
        })
        ctx = handoff_context(state)
        assert "Javier Mogro" in ctx
        assert "Limpieza" in ctx
        assert "PASO 7" in ctx
