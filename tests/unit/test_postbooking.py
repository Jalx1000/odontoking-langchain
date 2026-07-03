"""Unit tests for the deterministic post-booking controller (postbooking.py).

The CRM helpers (cancel_appointment / rename_person) are patched so no network is involved.
Focus: intent detection, the explicit-confirmation gate for cancelling, and the rename flow
(validate → confirm → persist) — none of which the LLM provided before.
"""

from unittest.mock import AsyncMock, patch

import pytest

import app.core.langgraph.postbooking as pb
from app.core.langgraph.intake import new_state
from app.core.langgraph.postbooking import (
    advance_postbooking,
    is_cancel_intent,
    is_name_change_intent,
    is_reschedule_intent,
)


def _booked_state() -> dict:
    state = new_state("Maria Galarza")
    state.update({
        "edad": 24, "is_for_self": True, "es_antiguo": True, "seguro": "No tengo seguro",
        "seguro_estado": "PARTICULAR", "motivo": "Encía inflamada", "completo": True, "wa_id": "591",
        "booking_phase": "done", "booking_confirmado": True, "doctor_name": "Susana Urrusti",
        "chosen_date": "2026-06-23", "chosen_start": "14:30:00",
    })
    return state


class TestIntentDetection:
    """Cancel and name-change intents are recognized without false positives."""

    def test_cancel_intent(self):
        """Cancellation phrasings are detected."""
        assert is_cancel_intent("quiero cancelar mi cita")
        assert is_cancel_intent("ya no quiero la cita")
        assert not is_cancel_intent("quiero agendar una cita")

    def test_name_change_intent(self):
        """Name-correction phrasings are detected."""
        assert is_name_change_intent("quiero cambiar mi nombre")
        assert is_name_change_intent("ese no es mi nombre")
        assert not is_name_change_intent("quiero cambiar el horario")

    def test_reschedule_intent(self):
        """Day/time-change phrasings are detected, but a rename is not a reschedule."""
        assert is_reschedule_intent("quiero cambiar mi horario para el jueves")
        assert is_reschedule_intent("quiero que se corrija mi hora")
        assert is_reschedule_intent("puedo reprogramar mi cita?")
        assert not is_reschedule_intent("quiero cambiar mi nombre")  # rename ≠ reschedule
        assert not is_reschedule_intent("quiero agendar una cita")


class TestCancelFlow:
    """Cancelling requires an explicit confirmation and only then deletes the cita."""

    @pytest.mark.asyncio
    async def test_cancel_requires_explicit_yes_then_cancels(self):
        """A cancel intent asks for confirmation; only an explicit 'sí' deletes the cita."""
        with patch.object(pb, "cancel_appointment", AsyncMock(return_value={"success": True})) as cancel:
            r = await advance_postbooking(_booked_state(), "quiero cancelar mi cita")
            assert r.state["post_phase"] == "cancel_confirm"
            assert "Susana Urrusti" in r.reply and "23/06/2026" in r.reply
            cancel.assert_not_called()

            r = await advance_postbooking(r.state, "sí")
            cancel.assert_awaited_once_with("591")
            assert r.done is True and r.clear is True
            assert "cancelada" in r.reply.lower()

    @pytest.mark.asyncio
    async def test_double_tap_yes_confirms_cancel(self):
        """A buffered double tap ("sí\\nsí") at the gate still cancels — the last line decides."""
        with patch.object(pb, "cancel_appointment", AsyncMock(return_value={"success": True})) as cancel:
            r = await advance_postbooking(_booked_state(), "quiero cancelar mi cita")
            r = await advance_postbooking(r.state, "sí\nsí")
            cancel.assert_awaited_once_with("591")
            assert r.done is True and r.clear is True

    @pytest.mark.asyncio
    async def test_cancel_declined_keeps_appointment(self):
        """A non-affirmative answer at the gate keeps the cita and does not call the CRM."""
        with patch.object(pb, "cancel_appointment", AsyncMock(return_value={"success": True})) as cancel:
            r = await advance_postbooking(_booked_state(), "cancelar")
            r = await advance_postbooking(r.state, "no, mejor no")
            cancel.assert_not_called()
            assert r.state["post_phase"] is None
            assert r.clear is False

    @pytest.mark.asyncio
    async def test_cancel_crm_failure_keeps_phase(self):
        """If the CRM cancel fails, the flow stays in the confirm phase for a retry."""
        with patch.object(pb, "cancel_appointment", AsyncMock(return_value={"success": False})):
            r = await advance_postbooking(_booked_state(), "cancelar")
            r = await advance_postbooking(r.state, "sí")
            assert r.done is False and r.clear is False
            assert "inconveniente" in r.reply.lower()


class TestRenameFlow:
    """The rename flow validates the name, confirms, and persists the corrected name."""

    @pytest.mark.asyncio
    async def test_rename_validates_then_persists(self):
        """Rename asks for the name, rejects a question, then confirms and persists."""
        with patch.object(pb, "rename_person", AsyncMock(return_value={"success": True})) as rename:
            r = await advance_postbooking(_booked_state(), "quiero cambiar mi nombre")
            assert r.state["post_phase"] == "rename_ask"

            # A question is not accepted as a name.
            r = await advance_postbooking(r.state, "cual es mi nombre?")
            assert r.state["post_phase"] == "rename_ask"
            rename.assert_not_called()

            r = await advance_postbooking(r.state, "Maria Isabel Galarza")
            assert r.state["post_phase"] == "rename_confirm"
            assert "Maria Isabel Galarza" in r.reply

            r = await advance_postbooking(r.state, "sí")
            rename.assert_awaited_once()
            assert r.done is True
            assert r.state["nombre"] == "Maria Isabel Galarza"
            assert "actualicé" in r.reply.lower() or "actualice" in r.reply.lower()

    @pytest.mark.asyncio
    async def test_rename_uses_combined_crm_name(self):
        """The CRM rename uses the '<perfil> - <real>' combined name when a profile name exists."""
        state = _booked_state()
        state["nombre_whatsapp"] = "Ale"
        with patch.object(pb, "rename_person", AsyncMock(return_value={"success": True})) as rename:
            r = await advance_postbooking(state, "cambiar mi nombre")
            r = await advance_postbooking(r.state, "Maria Isabel Galarza")
            r = await advance_postbooking(r.state, "sí")
        assert rename.await_args.args[1] == "Ale - Maria Isabel Galarza"

    @pytest.mark.asyncio
    async def test_rename_correction_at_confirm(self):
        """Typing a different name at the confirm step re-confirms the corrected name."""
        with patch.object(pb, "rename_person", AsyncMock(return_value={"success": True})):
            r = await advance_postbooking(_booked_state(), "cambiar mi nombre")
            r = await advance_postbooking(r.state, "Juan Perez")
            assert r.state["pending_new_name"] == "Juan Perez"
            # Not a 'sí' but a valid new name → update the pending name and re-confirm.
            r = await advance_postbooking(r.state, "Juan Carlos Perez")
            assert r.state["pending_new_name"] == "Juan Carlos Perez"
            assert r.state["post_phase"] == "rename_confirm"
