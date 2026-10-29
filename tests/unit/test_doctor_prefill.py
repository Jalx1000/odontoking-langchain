"""Unit tests for the deterministic doctor→id prefill resolution.

When the booking form is sent (flow=paciente_tercero), a doctor NAME the model passed must be
resolved to its CRM id (the form pre-selects by id), and the doctor's specialty carried when the
model gave none — so "con el Dr. Eloy" opens the form on Eloy + his especialidad.
"""

import json

import pytest

from app.core.langgraph import odontoking_graph as og

_DOCTORS = {
    "data": [
        {"id": 21, "name": "ELOY MONTERO", "specialties": [{"id": 3, "name": "Cirugia"}]},
        {"id": 22, "name": "ANA PEREZ", "specialties": [{"id": 5, "name": "Ortodoncia"}]},
        {"id": 23, "name": "ELOY GUTIERREZ", "specialties": [{"id": 1, "name": "General"}]},
    ]
}


@pytest.fixture
def stub_doctors(monkeypatch):
    class _Tool:
        async def ainvoke(self, _):
            return json.dumps(_DOCTORS)

    monkeypatch.setattr(og, "get_doctors", _Tool())


@pytest.mark.asyncio
class TestResolveDoctorPrefill:
    async def test_name_resolves_to_id_and_carries_specialty(self, stub_doctors):
        """A unique name → the doctor id, plus the doctor's specialty when none was given."""
        out = await og._resolve_doctor_in_prefill({"flow": "paciente_tercero", "doctor": "Eloy Montero"})
        assert out["doctor"] == "21"
        assert out["specialty"] == "Cirugia"

    async def test_ambiguous_first_name_not_guessed(self, stub_doctors):
        """'Eloy' matches two doctors → ambiguous, keep as-is and don't guess."""
        out = await og._resolve_doctor_in_prefill({"flow": "paciente_tercero", "doctor": "Eloy"})
        assert out["doctor"] == "Eloy"  # unchanged (ambiguous)
        assert "specialty" not in out

    async def test_explicit_specialty_is_preserved(self, stub_doctors):
        """A specialty the model already set is not overwritten by the doctor's."""
        out = await og._resolve_doctor_in_prefill(
            {"flow": "paciente_tercero", "doctor": "Ana Perez", "specialty": "Estetica"}
        )
        assert out["doctor"] == "22"
        assert out["specialty"] == "Estetica"

    async def test_numeric_id_passthrough_fills_specialty(self, stub_doctors):
        """An id the model already resolved stays, and its specialty is carried."""
        out = await og._resolve_doctor_in_prefill({"flow": "paciente_tercero", "doctor": "22"})
        assert out["doctor"] == "22"
        assert out["specialty"] == "Ortodoncia"

    async def test_unknown_doctor_kept(self, stub_doctors):
        """A name that matches nobody is left as-is (form degrades gracefully)."""
        out = await og._resolve_doctor_in_prefill({"flow": "paciente_tercero", "doctor": "Zzz"})
        assert out["doctor"] == "Zzz"

    async def test_no_doctor_noop(self, stub_doctors):
        """No doctor → nothing to resolve."""
        out = await og._resolve_doctor_in_prefill({"flow": "paciente_tercero", "specialty": "General"})
        assert out == {"flow": "paciente_tercero", "specialty": "General"}
