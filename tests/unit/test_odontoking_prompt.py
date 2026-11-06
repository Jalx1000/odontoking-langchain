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


class TestInsuranceGate:
    """Insurance must be a hard gate before booking, including returning patients."""

    def test_gate_present_when_insurance_missing(self):
        """Returning patient with name but no insurance/ci → insurance gate is injected."""
        prompt = _load_odontoking_prompt(
            "591700",
            is_new_patient=False,
            nombre_registrado="Javier Mogro",
        )
        assert "SEGURO NO VERIFICADO" in prompt

    def test_gate_present_when_only_ci_missing(self):
        """Having only the insurance company (no ci) still triggers the gate."""
        prompt = _load_odontoking_prompt(
            "591700",
            is_new_patient=False,
            nombre_registrado="Javier Mogro",
            seguro_paciente="Membresía Odontoking",
        )
        assert "SEGURO NO VERIFICADO" in prompt

    def test_gate_absent_when_insurance_fully_registered(self):
        """Both ci and insurance in context → no gate, values are shown."""
        prompt = _load_odontoking_prompt(
            "591700",
            is_new_patient=False,
            nombre_registrado="Javier Mogro",
            ci_paciente="12387735",
            seguro_paciente="Membresía Odontoking",
        )
        assert "SEGURO NO VERIFICADO" not in prompt
        assert "ci_paciente_registrada: 12387735" in prompt
        assert "seguro_registrado: Membresía Odontoking" in prompt


class TestCitasActivasContext:
    """Upcoming citas and CRM messages reach the model as context."""

    def test_citas_activas_rendered_even_without_history(self):
        """No past history must not read as 'no appointment' when an upcoming one exists."""
        prompt = _load_odontoking_prompt(
            "591700",
            citas_activas=[{"lead_id": 9, "fecha": "09/11/2026", "hora": "09:30", "doctor": "Rojas", "paciente": "Ana"}],
        )
        assert "lead_id=9 | 09/11/2026 09:30 | Dr. Rojas | Ana" in prompt
        assert "no tiene citas registradas" not in prompt

    def test_forms_prompt_trusts_citas_activas(self):
        """Production prompt (forms mode), which tests do not load by default."""
        with open("app/core/prompts/odontoking.md") as f:
            template = f.read()
        assert "NUNCA digas que el paciente no tiene cita si `citas_activas` trae alguna" in template
        assert "responde con `citas_previas`" not in template

    def test_mensajes_crm_rendered(self):
        """Messages sent outside the agent are shown with their origin."""
        prompt = _load_odontoking_prompt("591700", mensajes_crm=[{"origen": "recepción", "texto": "ya le agendé"}])
        assert "mensajes_crm:" in prompt
        assert "- recepción: ya le agendé" in prompt


class TestAgentAuditRules:
    """Rules added after auditing the conversations of 06/11."""

    def _prompt(self):
        with open("app/core/prompts/odontoking.md") as f:
            return f.read()

    def test_existing_appointment_is_mentioned_before_offering_a_new_one(self):
        assert "dísela ANTES de ofrecer" in self._prompt()

    def test_appointment_under_another_name_goes_to_reception(self):
        """Conversación 4116: "no encontré una cita" for a cita registered under another number."""
        assert "puede estar a otro nombre" in self._prompt()

    def test_confirming_an_appointment_uses_its_lead_id(self):
        """Conversación 4020: "confirmo los 3" received the booking form."""
        assert 'responder_recordatorio("confirma", lead_id=' in self._prompt()

    def test_coverage_terms_are_not_improvised(self):
        """Conversación 4013: "generalmente la membresía dura un año"."""
        assert "ni \"lo normal\" o \"generalmente\"" in self._prompt()

    def test_address_names_the_city(self):
        """Conversación 4118: a patient from La Paz booked without knowing the clinic is in Santa Cruz."""
        assert "Taller Bavaria, Santa Cruz de la Sierra" in self._prompt()

    def test_reminder_no_cancels_but_another_date_reschedules(self):
        assert "ESTO CANCELA LA CITA" in self._prompt()
        assert "NO uses no_asiste: envía reprogramar_cita" in self._prompt()

    def test_booking_rescheduling_and_cancelling_follow_the_patient_goal(self):
        prompt = self._prompt()
        assert "decide por lo que el paciente quiere lograr" in prompt
        assert 'aunque diga "cancelar" o "anular"' in prompt
        assert "pregunta UNA vez antes de enviar formulario" in prompt

