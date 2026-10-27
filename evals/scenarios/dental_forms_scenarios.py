"""Forms-mode evaluation scenarios for OdontokingAgent.

Covers every case from "Escenarios del agente Odontoking.md": the cross-cutting rules,
the service→specialty mapping for the prefill, reactive insurance, prices, cancel vs
reschedule, reminders (C1/C2), and reading context before asking.

These run against the CRM TEST base (sandbox). Identities below are the seeded sandbox
contacts/conversations (CRM guide §10); the booking/reminder/insurance tools hit the real
test CRM, so the scenarios need real conversation_ids and seeded data:

    sandbox contacts
    - 59100000001 / conv 5303 / person 18935  → "confirma" (has a pending reminder)
    - 59100000002 / conv 5304 / person 18936  → "no_asiste" (has a pending reminder)
    - 59176616013 / conv 1    / person 971     → real number (reminder 128401)
    insurance test CIs: 0000001 Nacional Vida · 0000002 Alianza · 0000003 Membresía · 0000004 sin seguro
    doctors: Adriana Soria = id 44 · agenda loaded through 09/11

Reminder activity_ids CHANGE on every re-seed (`whatsapp:eval-seed`). Evals bypass the webhook
that normally pushes patient_context, so the two reminder scenarios inject the seeded activity_id
inline — update them after each re-seed (or add a CRM GET-reminders endpoint to load them live).

Run with WHATSAPP_FLOWS_ENABLED=true. Graded by the LLM judge against `success_criteria`.
    make eval-scenarios SCENARIOS=forms_limpieza_general,forms_recordatorio_confirma
"""

# Seeded sandbox identities (CRM test base). Keep in sync with CRM guide §10.
_SANDBOX_CONFIRMA = {"wa_id": "59100000001", "conversation_id": 5303, "person_id": 18935}
_SANDBOX_NO_ASISTE = {"wa_id": "59100000002", "conversation_id": 5304, "person_id": 18936}
# A generic sandbox identity for routing scenarios (no reminder needed). Reuse the confirma contact.
_SANDBOX = _SANDBOX_CONFIRMA

FORMS_SCENARIOS: list[dict] = [
    # ───────────────────────── Reglas transversales ─────────────────────────
    {
        "id": "forms_regla_gracias_no_es_pedido_de_cita",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Rosa Villca",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        "turns": ["Gracias!"],
        "success_criteria": "Un simple agradecimiento NO es un pedido de cita: el agente debe responder "
                            "con cortesía SIN enviar ningún formulario y sin iniciar el cuestionario de agendamiento.",
        "tags": ["forms", "regla_transversal", "no_booking"],
    },

    # ───────────────────── Servicio → especialidad (prefill) ─────────────────────
    {
        "id": "forms_limpieza_general",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": True, "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        "turns": ["Hola, quiero una limpieza dental"],
        "success_criteria": "El agente debe SALUDAR (primer mensaje) y enviar el formulario de agendar "
                            "(paciente_tercero), mapeando 'limpieza' a la especialidad General. No debe pedir datos por chat.",
        "tags": ["forms", "especialidad", "general"],
    },
    {
        "id": "forms_ninos_odontopediatria",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": True, "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        "turns": ["Buenas, necesito un control para mi hijo de 6 años"],
        "success_criteria": "Debe enviar el formulario de agendar para otra persona (el hijo), con especialidad "
                            "Odontopediatria (control de niños / menor de edad). Saluda en el primer mensaje.",
        "tags": ["forms", "especialidad", "odontopediatria", "tercero"],
    },
    {
        "id": "forms_brackets_ortodoncia",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Luis Pozo",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        "turns": ["Quiero control de brackets"],
        "success_criteria": "Debe enviar el formulario de agendar con especialidad Ortodoncia (brackets / control de ortodoncia).",
        "tags": ["forms", "especialidad", "ortodoncia"],
    },
    {
        "id": "forms_conducto_endodoncista",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Luis Pozo",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        "turns": ["Me duele mucho una muela, creo que necesito tratamiento de conducto"],
        "success_criteria": "Debe mostrar empatía y enviar el formulario de agendar con especialidad Endodoncista "
                            "(tratamiento de conducto / dolor fuerte de una pieza). OJO: el nombre correcto es 'Endodoncista'.",
        "tags": ["forms", "especialidad", "endodoncista", "dolor"],
    },
    {
        "id": "forms_extraccion_cirugia",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Luis Pozo",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        "turns": ["Necesito que me saquen la muela del juicio"],
        "success_criteria": "Debe enviar el formulario de agendar con especialidad Cirugia (extracción / muela del juicio).",
        "tags": ["forms", "especialidad", "cirugia"],
    },
    {
        "id": "forms_encias_periodoncia",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Luis Pozo",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        "turns": ["Me sangran las encías y tengo sarro"],
        "success_criteria": "Debe enviar el formulario de agendar con especialidad Periodoncia (encías / sarro / sangrado).",
        "tags": ["forms", "especialidad", "periodoncia"],
    },
    {
        "id": "forms_blanqueamiento_estetica",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Luis Pozo",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        "turns": ["Quiero hacerme un blanqueamiento"],
        "success_criteria": "Debe enviar el formulario de agendar con especialidad Estetica (blanqueamiento / carillas).",
        "tags": ["forms", "especialidad", "estetica"],
    },
    {
        "id": "forms_implante_sin_especialidad",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Luis Pozo",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        "turns": ["Quiero un implante dental"],
        "success_criteria": "Implantes/prótesis no tienen especialidad agendable hoy: el agente SÍ debe enviar el "
                            "formulario de agendar pero SIN forzar una especialidad inventada. No debe prometer un especialista de implantes.",
        "tags": ["forms", "especialidad", "implante", "sin_specialty"],
    },
    {
        "id": "forms_radiografia_recepcion",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Luis Pozo",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        "turns": ["Necesito una radiografía panorámica"],
        "success_criteria": "Radiografía/tomografía/plasma NO se agendan por formulario: el agente debe derivar a "
                            "recepción (señal de handoff), NO enviar el formulario de agendar.",
        "tags": ["forms", "recepcion", "no_formulario"],
    },
    {
        "id": "forms_no_se_general",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": True, "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        "turns": ["Hola, quiero una cita pero no sé qué especialidad necesito"],
        "success_criteria": "Debe saludar y enviar igual el formulario de agendar (paciente_tercero) con especialidad "
                            "General / 'No sé / Consulta general'; no debe exigir que elija especialidad por chat.",
        "tags": ["forms", "especialidad", "general", "no_se"],
    },

    # ───────────────────── Prefill directo (doctor/fecha/hora) ─────────────────────
    {
        "id": "forms_prefill_doctor_fecha_hora",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Carla Rojas",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        # Adriana Soria = doctor id 44; agenda cargada hasta 09/11.
        "turns": ["Buenas tardes, quiero agendar cita con la doctora Adriana Soria el viernes a las 15:00"],
        "success_criteria": "Debe saludar y enviar el formulario de agendar pre-llenado con el doctor pedido (Adriana "
                            "Soria) y la fecha (próximo viernes) y hora (15:00) como preferencia. No debe prometer el "
                            "horario por chat (lo confirma el formulario/disponibilidad).",
        "tags": ["forms", "prefill", "doctor", "fecha_hora"],
    },

    # ───────────────────────── Seguro (reactivo, D1) ─────────────────────────
    {
        "id": "forms_seguro_sin_aseguradora_pregunta",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Marta Ríos",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        "turns": ["Hola, tengo seguro"],
        "success_criteria": "Como no dijo cuál aseguradora, el agente debe PREGUNTAR cuál (Nacional Vida / Alianza / "
                            "Membresía Odontoking) antes de pedir el carnet. NO debe asumir una ni tratarlo como pregunta de precio.",
        "tags": ["forms", "seguro", "reactivo"],
    },
    {
        "id": "forms_seguro_nacional_vigente",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Marta Ríos",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        # CI 0000001 = vigente con Nacional Vida en la base de pruebas.
        "turns": [
            "Tengo seguro Nacional Vida, ¿me cubre?",
            "Mi carnet es 0000001",
        ],
        "success_criteria": "Primero pide el carnet (no trata esto como precio). Tras recibir 0000001, como la "
                            "cobertura de Nacional Vida da VIGENTE, debe confirmarle al paciente que su cobertura figura "
                            "vigente y ofrecer agendar. (El agente NO debe mencionar nombres de herramientas internas al "
                            "paciente; basta con que confirme la vigencia correctamente.)",
        "tags": ["forms", "seguro", "reactivo", "vigente"],
    },
    {
        "id": "forms_seguro_no_aceptado",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Marta Ríos",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        "turns": ["Tengo seguro con Vitalia"],
        "success_criteria": "Solo se trabaja con Membresía Odontoking, Nacional Vida y Alianza. Con Vitalia (no aceptado) "
                            "el agente debe decirlo con amabilidad y ofrecer agendar como particular o pasar con recepción; "
                            "NO debe intentar validarlo.",
        "tags": ["forms", "seguro", "no_aceptado", "d1"],
    },

    # ───────────────────────── Precios (E1) ─────────────────────────
    {
        "id": "forms_precio_primera_deflecta",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Diego Lima",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        "turns": ["¿Cuánto cuesta una limpieza?"],
        "success_criteria": "Primera pregunta de precio: el agente NO da precios; explica que no informa precios por chat "
                            "y ofrece agendar una cita de valoración. No debe derivar aún.",
        "tags": ["forms", "precio", "e1"],
    },
    {
        "id": "forms_precio_insiste_deriva",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Diego Lima",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": []},
        "turns": [
            "¿Cuánto cuesta una limpieza?",
            "Necesito el precio igual, aunque sea un aproximado por favor",
        ],
        "success_criteria": "Tras deflectar la primera vez, el paciente INSISTE con el precio: el agente debe derivar a "
                            "recepción (señal de handoff) en el segundo turno, sin inventar un precio.",
        "tags": ["forms", "precio", "e1", "handoff"],
    },

    # ──────────────── Cancelar vs reprogramar + sin cita activa ────────────────
    {
        "id": "forms_cancelar_y_mover_es_reprogramar",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {
            "is_new_patient": False, "nombre_registrado": "Elena Paz",
            "conversation_id": _SANDBOX["conversation_id"], "cita_activa": 5001,
            "citas": [{"lead_id": 5001, "fecha": "2026-11-03", "hora": "10:00",
                       "servicio": "Ortodoncia", "doctor": "Adriana Soria", "estado": "Agendado"}],
        },
        "turns": ["Quiero cancelar mi cita y moverla para mañana"],
        "success_criteria": "Aunque dice 'cancelar', el resultado pedido es MOVER la cita a otra fecha: el agente debe "
                            "enviar el formulario de REPROGRAMAR (reprogramar_cita), NO el de cancelar.",
        "tags": ["forms", "reprogramar", "desambiguacion"],
    },
    {
        "id": "forms_cancelar_sin_nueva_fecha",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {
            "is_new_patient": False, "nombre_registrado": "Elena Paz",
            "conversation_id": _SANDBOX["conversation_id"], "cita_activa": 5001,
            "citas": [{"lead_id": 5001, "fecha": "2026-11-03", "hora": "10:00",
                       "servicio": "Ortodoncia", "doctor": "Adriana Soria", "estado": "Agendado"}],
        },
        "turns": ["Ya no voy a poder ir a mi cita, anúlela por favor"],
        "success_criteria": "El paciente quiere ELIMINAR la cita sin pedir otra fecha: el agente debe enviar el formulario "
                            "de CANCELAR (cancelar_cita).",
        "tags": ["forms", "cancelar"],
    },
    {
        "id": "forms_reprogramar_sin_cita_activa",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Elena Paz",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": [], "cita_activa": None},
        "turns": ["Quiero reprogramar mi cita para el jueves"],
        "success_criteria": "El paciente NO tiene ninguna cita activa: el agente debe avisar que no encontró una cita y "
                            "enviar el formulario de AGENDAR (paciente_tercero) para que agende una nueva; NO el de reprogramar.",
        "tags": ["forms", "reprogramar", "sin_cita"],
    },
    {
        "id": "forms_cancelar_sin_cita_activa",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Elena Paz",
                            "conversation_id": _SANDBOX["conversation_id"], "citas": [], "cita_activa": None},
        "turns": ["Quiero cancelar mi cita"],
        "success_criteria": "Sin cita activa, el agente avisa que no encontró una cita y ofrece/envía el formulario de "
                            "AGENDAR una nueva; NO debe enviar el de cancelar.",
        "tags": ["forms", "cancelar", "sin_cita"],
    },

    # ───────────────────────── Recordatorios (C1 / C2) ─────────────────────────
    # El recordatorio vive en el CRM (respuesta null). Como los evals llaman get_response directo
    # (sin webhook que empuje patient_context), inyectamos el recordatorio aquí. El runner re-siembra
    # con POST /api/v1/whatsapp/eval/seed antes de la corrida y reemplaza `activity_id` por el fresco
    # (matcheando por conversation_id); el 0 de abajo es solo placeholder.
    {
        "id": "forms_recordatorio_confirma",
        "wa_id": _SANDBOX_CONFIRMA["wa_id"],
        "patient_context": {
            "is_new_patient": False, "nombre_registrado": "Susana Mejía",
            "conversation_id": _SANDBOX_CONFIRMA["conversation_id"],
            "recordatorios": [{"activity_id": 0, "lead_id": None, "respuesta": None}],
        },
        "turns": ["Si"],
        "success_criteria": "Hay un recordatorio sin contestar (respuesta null): el 'Sí' responde a ESE recordatorio. "
                            "El agente debe registrar la confirmación (confirma) y acusar ('¡Gracias por confirmar!'). "
                            "NO debe mostrar el menú ni enviar un formulario.",
        "tags": ["forms", "recordatorio", "c1", "confirma"],
    },
    {
        "id": "forms_recordatorio_no_asiste",
        "wa_id": _SANDBOX_NO_ASISTE["wa_id"],
        "patient_context": {
            "is_new_patient": False, "nombre_registrado": "Pedro Gil",
            "conversation_id": _SANDBOX_NO_ASISTE["conversation_id"],
            "recordatorios": [{"activity_id": 0, "lead_id": None, "respuesta": None}],
        },
        "turns": ["No, no voy a poder ir"],
        "success_criteria": "Hay un recordatorio sin contestar: el 'No' responde a esa cita. El agente debe registrar "
                            "no_asiste y LUEGO ofrecer reprogramar o cancelar con los formularios. No debe cancelar la cita por su cuenta.",
        "tags": ["forms", "recordatorio", "c2", "no_asiste"],
    },
    {
        "id": "forms_estoy_en_camino_handoff",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {
            "is_new_patient": False, "nombre_registrado": "Susana Mejía",
            "conversation_id": _SANDBOX["conversation_id"],
            "citas": [{"lead_id": 7001, "fecha": "2026-10-28", "hora": "14:00",
                       "servicio": "General", "doctor": "Adriana Soria", "estado": "Agendado"}],
            "recordatorios": [],
        },
        "turns": ["Estoy en camino, voy a llegar un poco tarde"],
        "success_criteria": "'Estoy en camino / llego tarde' no es un pedido de cita: el agente debe derivar a recepción "
                            "(señal de handoff) para avisar; NO debe enviar formulario ni iniciar agendamiento.",
        "tags": ["forms", "c6", "handoff"],
    },

    # ───────────────────────── Leer contexto antes de preguntar ─────────────────────────
    {
        "id": "forms_cuando_es_mi_cita",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {
            "is_new_patient": False, "nombre_registrado": "Gabriel Soto",
            "conversation_id": _SANDBOX["conversation_id"], "cita_activa": 8001,
            "citas": [{"lead_id": 8001, "fecha": "2026-11-05", "hora": "09:30",
                       "servicio": "General", "doctor": "Adriana Soria", "estado": "Agendado"}],
        },
        "turns": ["¿Cuándo es mi cita?"],
        "success_criteria": "El agente debe responder con los datos de la cita (fecha 05/11, 09:30, Dra. Adriana Soria) "
                            "leyéndolos del contexto, SIN enviar formulario y SIN volver a preguntar.",
        "tags": ["forms", "leer_contexto", "get_citas"],
    },
    {
        "id": "forms_cita_de_mi_hijo",
        "wa_id": _SANDBOX["wa_id"],
        "patient_context": {
            "is_new_patient": False, "nombre_registrado": "Gabriel Soto",
            "conversation_id": _SANDBOX["conversation_id"],
            "pacientes_a_cargo": [{"nombre": "Mateo Soto", "relacion": "hijo"}],
            "citas": [{"lead_id": 8002, "fecha": "2026-11-06", "hora": "11:00", "paciente": "Mateo Soto",
                       "servicio": "Odontopediatria", "doctor": "Susana Urresti", "estado": "Agendado"}],
        },
        "turns": ["¿Cuándo es la cita de mi hijo Mateo?"],
        "success_criteria": "Usando pacientes_a_cargo y citas del contexto, el agente debe responder con la cita de Mateo "
                            "(06/11, 11:00) SIN enviar formulario ni volver a preguntar quién es.",
        "tags": ["forms", "leer_contexto", "pacientes_a_cargo"],
    },
]
