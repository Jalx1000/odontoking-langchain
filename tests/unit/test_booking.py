"""Unit tests for the deterministic Phase-2 booking controller (booking.py).

The tool wrappers (_fetch_*, _book_crm) are patched so no network/LLM is involved. The focus
is the guarantees the LLM failed to provide: exact (non-fabricated, de-duplicated) slot
rendering, the right day grouping, an explicit-confirmation gate, and correct CRM payloads.
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

import app.core.langgraph.booking as bk
from app.core.langgraph.booking import advance_booking, motivo_is_deterministic
from app.core.langgraph.intake import new_state

_SPECIALTIES = [{"id": 10, "name": "Periodoncia"}, {"id": 20, "name": "General"}]
_SERVICES = [{"id": 174, "name": "Limpieza de toda la cavidad bucal", "duration_minutes": None}]
_DOCTORS = [
    {"id": 12, "name": "Susana Urrusti", "specialties": [{"id": 10, "name": "Periodoncia"}]},
    {"id": 99, "name": "Ortodoncista X", "specialties": [{"id": 6, "name": "Ortodoncia"}]},
]
_SCHEDULE = [
    {
        "date": "2026-06-18",
        "day_label": "jueves 18/06",
        "slots": [
            {"start_time": "17:00:00", "end_time": "18:00:00"},
            {"start_time": "15:00:00", "end_time": "16:00:00"},
            {"start_time": "15:00:00", "end_time": "16:00:00"},  # duplicate, must collapse
        ],
    },
]


@contextmanager
def _mocked_tools(*, schedule=_SCHEDULE, doctors=_DOCTORS, crm=None):
    crm = crm if crm is not None else {"success": True, "appointment_registered": True}
    book = AsyncMock(return_value=crm)
    with (
        patch.object(bk, "_fetch_specialties", AsyncMock(return_value=_SPECIALTIES)),
        patch.object(bk, "_fetch_services", AsyncMock(return_value=_SERVICES)),
        patch.object(bk, "_fetch_doctors", AsyncMock(return_value=doctors)),
        patch.object(bk, "_fetch_schedule", AsyncMock(return_value=schedule)),
        patch.object(bk, "_book_crm", book),
    ):
        yield book


def _third_party_state() -> dict:
    state = new_state("Danitza Alvarez")
    state.update({
        "edad": 28, "is_for_self": False, "tercero_nombre": "ADELINO RODA", "tercero_edad": 21,
        "es_antiguo": True, "seguro": "Membresía Odontoking", "ci": "2848219",
        "seguro_estado": "VIGENTE", "motivo": "Limpieza", "completo": True, "wa_id": "591",
    })
    return state


class TestMotivoRouting:
    """Routing: fixed motivos use keywords; free-text is classified by the bounded LLM."""

    def test_fixed_motivos_are_deterministic(self):
        """The 4 fixed motivos route to the deterministic booking flow."""
        assert motivo_is_deterministic("Limpieza")
        assert motivo_is_deterministic("Encía inflamada")
        assert motivo_is_deterministic("dolor dental")

    def test_otro_and_free_text_are_not(self):
        """'Otro', free text, and None fall back to the LLM."""
        assert not motivo_is_deterministic("Otro")
        assert not motivo_is_deterministic("me duele al masticar")
        assert not motivo_is_deterministic(None)


class TestBookingFlow:
    """The deterministic doctor → día → hora → confirmar flow."""

    @pytest.mark.asyncio
    async def test_happy_path_books_with_exact_slot_and_third_party_carnet(self):
        """Full flow books with the exact slot and the third person's carnet."""
        with _mocked_tools() as book:
            st = _third_party_state()
            r = await advance_booking(st, None)        # → doctor list
            assert "Susana Urrusti" in r.reply
            r = await advance_booking(r.state, "1")     # pick doctor → days
            assert "jueves 18/06" in r.reply
            r = await advance_booking(r.state, "1")     # pick day → times
            r = await advance_booking(r.state, "2")     # pick 17:00 → confirm
            assert "Responda" in r.reply and "17:00" in r.reply
            book.assert_not_called()                    # gate: not booked until explicit yes
            r = await advance_booking(r.state, "sí")    # confirm
            assert r.done is True
            assert "agendada exitosamente" in r.reply

        payload = book.call_args.args[0]
        assert payload["es_cita_confirmada"] is True
        assert payload["doctor_id"] == 12
        assert payload["horario_cita"] == "18/06/2026 17:00"
        assert payload["numero_carnet"] == "2848219"            # the THIRD person's carnet
        assert payload["nombre_paciente_de_otra_persona"] == "ADELINO RODA"

    @pytest.mark.asyncio
    async def test_only_specialty_matching_doctors_are_offered(self):
        """Only doctors of the matched specialty are offered."""
        with _mocked_tools():
            r = await advance_booking(_third_party_state(), None)
        assert "Susana Urrusti" in r.reply
        assert "Ortodoncista X" not in r.reply  # different specialty → excluded

    @pytest.mark.asyncio
    async def test_slots_are_deduped_exact_and_ascending(self):
        """Slots are de-duplicated, ascending, and exactly the API's (no fabrication)."""
        with _mocked_tools():
            st = _third_party_state()
            r = await advance_booking(st, None)
            r = await advance_booking(r.state, "1")  # doctor → days
            r = await advance_booking(r.state, "1")  # day → times
        # exactly two unique slots, ascending, exact API times (no fabricated 18:00-19:00)
        assert "1. 15:00 - 16:00" in r.reply
        assert "2. 17:00 - 18:00" in r.reply
        assert "18:00 - 19:00" not in r.reply
        assert r.reply.count("15:00 - 16:00") == 1

    @pytest.mark.asyncio
    async def test_confirmation_gate_blocks_without_explicit_yes(self):
        """No CRM booking happens without an explicit affirmative."""
        with _mocked_tools() as book:
            st = _third_party_state()
            r = await advance_booking(st, None)
            r = await advance_booking(r.state, "1")
            r = await advance_booking(r.state, "1")
            r = await advance_booking(r.state, "2")   # → confirmar
            r = await advance_booking(r.state, "el jueves mejor")  # not affirmative
            assert r.done is False
            book.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_schedule_falls_back_to_choosing_another_doctor(self):
        """An empty schedule sends the patient back to pick another doctor."""
        with _mocked_tools(schedule=[]):
            st = _third_party_state()
            r = await advance_booking(st, None)
            r = await advance_booking(r.state, "1")  # pick doctor → empty schedule
        assert r.state["booking_phase"] == "doctor"
        assert "no tiene horarios disponibles" in r.reply

    @pytest.mark.asyncio
    async def test_fallback_to_all_doctors_when_no_specialty_match(self):
        """No specialty match → all available doctors are offered (never dead-end)."""
        only_ortho = [{"id": 99, "name": "Ortodoncista X", "specialties": [{"id": 6, "name": "Ortodoncia"}]}]
        with _mocked_tools(doctors=only_ortho):
            r = await advance_booking(_third_party_state(), None)
        # specialty (Periodoncia/General) matches nobody → offer all available doctors
        assert "Ortodoncista X" in r.reply


class TestFreeTextMotivo:
    """Free-text molestias are classified into a specialty by the bounded LLM classifier."""

    @pytest.mark.asyncio
    async def test_free_text_uses_classifier_to_pick_doctor(self):
        """A free-text motivo routes to the classifier's specialty and offers its doctor."""
        ortho_specialties = [{"id": 6, "name": "Ortodoncia"}, {"id": 20, "name": "General"}]
        docs = [
            {"id": 7, "name": "Ortodoncista Real", "specialties": [{"id": 6, "name": "Ortodoncia"}]},
            {"id": 8, "name": "Periodoncista", "specialties": [{"id": 10, "name": "Periodoncia"}]},
        ]
        state = new_state("Leo")
        state.update({
            "edad": 30, "is_for_self": True, "es_antiguo": False, "seguro": "No tengo seguro",
            "seguro_estado": "PARTICULAR", "motivo": "quiero ponerme brackets",
            "completo": True, "wa_id": "591",
        })
        with (
            patch.object(bk, "_fetch_specialties", AsyncMock(return_value=ortho_specialties)),
            patch.object(bk, "_fetch_services", AsyncMock(return_value=[])),
            patch.object(bk, "_fetch_doctors", AsyncMock(return_value=docs)),
            patch.object(bk, "classify_molestia", AsyncMock(return_value=(6, None))) as clf,
        ):
            r = await advance_booking(state, None)
        clf.assert_awaited_once()  # free-text → classifier was consulted
        assert "Ortodoncista Real" in r.reply
        assert "Periodoncista" not in r.reply  # only the classified specialty's doctor


class TestPastSlotFiltering:
    """Slots earlier than the current local time are never offered for today."""

    def test_future_slots_filters_today_past_hours(self):
        """For today, slots starting before now are dropped; future ones are kept."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        fixed_now = datetime(2026, 6, 17, 10, 30, tzinfo=ZoneInfo("America/La_Paz"))
        slots = [
            {"start_time": "08:00:00", "end_time": "09:00:00"},  # past
            {"start_time": "11:00:00", "end_time": "12:00:00"},  # future
        ]
        with patch.object(bk, "datetime") as dt:
            dt.now.return_value = fixed_now
            kept = bk._future_slots("2026-06-17", slots)
        assert [s["start_time"] for s in kept] == ["11:00:00"]

    def test_future_slots_keeps_all_for_other_days(self):
        """Slots for a day other than today pass through unchanged."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        fixed_now = datetime(2026, 6, 17, 10, 30, tzinfo=ZoneInfo("America/La_Paz"))
        slots = [{"start_time": "08:00:00", "end_time": "09:00:00"}]
        with patch.object(bk, "datetime") as dt:
            dt.now.return_value = fixed_now
            kept = bk._future_slots("2026-06-18", slots)
        assert kept == slots
