"""Unit tests for the Odontoking system-prompt builder (patient context + name resolution).

Regression coverage for the bug where a patient that already existed in the CRM with the
placeholder name "Paciente WhatsApp" was treated as a returning patient, so the agent
skipped name/age/insurance and leaked the literal "[Nombre]" in the confirmation.
"""

from app.core.langgraph.odontoking_graph import _load_odontoking_prompt


class TestPatientContextRendering:
    """Render rules for the '# Contexto del paciente' block."""

    def test_real_registered_name_keeps_returning_patient(self):
        """A real CRM name keeps paciente_nuevo=false and injects the name."""
        prompt = _load_odontoking_prompt(
            "591700",
            is_new_patient=False,
            nombre_registrado="Javier Mogro",
        )
        assert "paciente_nuevo: false" in prompt
        assert "nombre_registrado: Javier Mogro" in prompt

    def test_no_real_name_is_treated_as_new(self):
        """Existing in CRM but without a real name → forced to paciente_nuevo=true."""
        prompt = _load_odontoking_prompt(
            "591700",
            is_new_patient=False,
            nombre_registrado=None,
            nombre_whatsapp="Alejandro",
        )
        assert "paciente_nuevo: true" in prompt
        assert "nombre_whatsapp: Alejandro" in prompt

    def test_whatsapp_name_used_without_asking(self):
        """The WhatsApp profile name is injected and the [Nombre] literal never leaks."""
        prompt = _load_odontoking_prompt("591700", nombre_whatsapp="Alejandro")
        assert "nombre_whatsapp: Alejandro" in prompt
        assert "[Nombre]" not in prompt.split("# Contexto del paciente")[-1]

    def test_no_name_asks_for_it(self):
        """With no name at all the agent is told to ask for the full name."""
        prompt = _load_odontoking_prompt("591700", is_new_patient=False)
        assert "paciente_nuevo: true" in prompt
        assert "pedir el nombre completo" in prompt
