"""Forms-mode evaluation scenarios for OdontokingAgent.

Covers every case from "Escenarios del agente Odontoking.md": the cross-cutting rules,
the service→specialty mapping for the prefill, reactive insurance, prices, cancel vs
reschedule, reminders (C1/C2), and reading context before asking.

Run with WHATSAPP_FLOWS_ENABLED=true (these exercise the native-forms flow). Each scenario
is graded by the LLM judge against `success_criteria`; the point is the agent's DECISION
(which flow/tool it routes to, what it says), not that the CRM POST succeeds.

    make eval-scenarios SCENARIOS=forms_limpieza_general,forms_recordatorio_confirma
"""

# A dummy conversation id so enviar_formulario is attempted; the judge grades the routing/message.
_CID = 999001

FORMS_SCENARIOS: list[dict] = [
    # ───────────────────────── Reglas transversales ─────────────────────────
    {
        "id": "forms_regla_si_no_es_pedido_de_cita",
        "wa_id": "eval_591760000101",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Rosa Villca",
                            "conversation_id": _CID, "citas": [], "recordatorios": []},
        "turns": ["Gracias!"],
        "success_criteria": "Un simple agradecimiento NO es un pedido de cita: el agente debe responder "
                            "con cortesía SIN enviar ningún formulario y sin iniciar el cuestionario de agendamiento.",
        "tags": ["forms", "regla_transversal", "no_booking"],
    },
    {
        "id": "forms_regla_si_suelto_sin_recordatorio",
        "wa_id": "eval_591760000102",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Rosa Villca",
                            "conversation_id": _CID, "citas": [], "recordatorios": []},
        "turns": ["Si"],
        "success_criteria": "Un 'Sí' aislado SIN recordatorio pendiente ni pregunta previa no es un pedido de cita: "
                            "el agente NO debe enviar formulario; puede ofrecer ayuda o preguntar en qué puede ayudar.",
        "tags": ["forms", "regla_transversal", "no_booking"],
    },

    # ───────────────────── Servicio → especialidad (prefill) ─────────────────────
    {
        "id": "forms_limpieza_general",
        "wa_id": "eval_591760000111",
        "patient_context": {"is_new_patient": True, "conversation_id": _CID, "citas": []},
        "turns": ["Hola, quiero una limpieza dental"],
        "success_criteria": "El agente debe SALUDAR (primer mensaje) y enviar el formulario de agendar "
                            "(paciente_tercero), mapeando 'limpieza' a la especialidad General. No debe pedir datos por chat.",
        "tags": ["forms", "especialidad", "general"],
    },
    {
        "id": "forms_ninos_odontopediatria",
        "wa_id": "eval_591760000112",
        "patient_context": {"is_new_patient": True, "conversation_id": _CID, "citas": []},
        "turns": ["Buenas, necesito un control para mi hijo de 6 años"],
        "success_criteria": "Debe enviar el formulario de agendar para otra persona (el hijo), con especialidad "
                            "Odontopediatria (control de niños / menor de edad). Saluda en el primer mensaje.",
        "tags": ["forms", "especialidad", "odontopediatria", "tercero"],
    },
    {
        "id": "forms_brackets_ortodoncia",
        "wa_id": "eval_591760000113",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Luis Pozo",
                            "conversation_id": _CID, "citas": []},
        "turns": ["Quiero control de brackets"],
        "success_criteria": "Debe enviar el formulario de agendar con especialidad Ortodoncia (brackets / control de ortodoncia).",
        "tags": ["forms", "especialidad", "ortodoncia"],
    },
    {
        "id": "forms_conducto_endodoncista",
        "wa_id": "eval_591760000114",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Luis Pozo",
                            "conversation_id": _CID, "citas": []},
        "turns": ["Me duele mucho una muela, creo que necesito tratamiento de conducto"],
        "success_criteria": "Debe mostrar empatía y enviar el formulario de agendar con especialidad Endodoncista "
                            "(tratamiento de conducto / dolor fuerte de una pieza). OJO: el nombre correcto es 'Endodoncista'.",
        "tags": ["forms", "especialidad", "endodoncista", "dolor"],
    },
    {
        "id": "forms_extraccion_cirugia",
        "wa_id": "eval_591760000115",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Luis Pozo",
                            "conversation_id": _CID, "citas": []},
        "turns": ["Necesito que me saquen la muela del juicio"],
        "success_criteria": "Debe enviar el formulario de agendar con especialidad Cirugia (extracción / muela del juicio).",
        "tags": ["forms", "especialidad", "cirugia"],
    },
    {
        "id": "forms_encias_periodoncia",
        "wa_id": "eval_591760000116",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Luis Pozo",
                            "conversation_id": _CID, "citas": []},
        "turns": ["Me sangran las encías y tengo sarro"],
        "success_criteria": "Debe enviar el formulario de agendar con especialidad Periodoncia (encías / sarro / sangrado).",
        "tags": ["forms", "especialidad", "periodoncia"],
    },
    {
        "id": "forms_blanqueamiento_estetica",
        "wa_id": "eval_591760000117",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Luis Pozo",
                            "conversation_id": _CID, "citas": []},
        "turns": ["Quiero hacerme un blanqueamiento"],
        "success_criteria": "Debe enviar el formulario de agendar con especialidad Estetica (blanqueamiento / carillas).",
        "tags": ["forms", "especialidad", "estetica"],
    },
    {
        "id": "forms_implante_sin_especialidad",
        "wa_id": "eval_591760000118",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Luis Pozo",
                            "conversation_id": _CID, "citas": []},
        "turns": ["Quiero un implante dental"],
        "success_criteria": "Implantes/prótesis no tienen especialidad agendable hoy: el agente SÍ debe enviar el "
                            "formulario de agendar pero SIN forzar una especialidad inventada (ni Endodoncista ni otra). "
                            "No debe prometer un especialista de implantes.",
        "tags": ["forms", "especialidad", "implante", "sin_specialty"],
    },
    {
        "id": "forms_radiografia_recepcion",
        "wa_id": "eval_591760000119",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Luis Pozo",
                            "conversation_id": _CID, "citas": []},
        "turns": ["Necesito una radiografía panorámica"],
        "success_criteria": "Radiografía/tomografía/plasma NO se agendan por formulario: el agente debe derivar a "
                            "recepción (señal de handoff), NO enviar el formulario de agendar.",
        "tags": ["forms", "recepcion", "no_formulario"],
    },
    {
        "id": "forms_no_se_general",
        "wa_id": "eval_591760000120",
        "patient_context": {"is_new_patient": True, "conversation_id": _CID, "citas": []},
        "turns": ["Hola, quiero una cita pero no sé qué especialidad necesito"],
        "success_criteria": "Debe saludar y enviar igual el formulario de agendar (paciente_tercero) con especialidad "
                            "General / 'No sé / Consulta general'; no debe exigir que elija especialidad por chat.",
        "tags": ["forms", "especialidad", "general", "no_se"],
    },

    # ───────────────────── Prefill directo (doctor/fecha/hora) ─────────────────────
    {
        "id": "forms_prefill_doctor_fecha_hora",
        "wa_id": "eval_591760000131",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Carla Rojas",
                            "conversation_id": _CID, "citas": []},
        "turns": ["Buenas tardes, quiero agendar cita con la doctora Adriana Soria el viernes a las 15:00"],
        "success_criteria": "Debe saludar y enviar el formulario de agendar pre-llenado con el doctor pedido y la fecha "
                            "(próximo viernes) y hora (15:00) como preferencia. No debe prometer el horario por chat "
                            "(lo confirma el formulario/disponibilidad).",
        "tags": ["forms", "prefill", "doctor", "fecha_hora"],
    },

    # ───────────────────────── Seguro (reactivo, D1) ─────────────────────────
    {
        "id": "forms_seguro_sin_aseguradora_pregunta",
        "wa_id": "eval_591760000141",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Marta Ríos", "conversation_id": _CID, "citas": []},
        "turns": ["Hola, tengo seguro"],
        "success_criteria": "Como no dijo cuál aseguradora, el agente debe PREGUNTAR cuál (Nacional Vida / Alianza / "
                            "Membresía Odontoking) antes de pedir el carnet. NO debe asumir una ni tratarlo como pregunta de precio.",
        "tags": ["forms", "seguro", "reactivo"],
    },
    {
        "id": "forms_seguro_nacional_pide_carnet",
        "wa_id": "eval_591760000142",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Marta Ríos", "conversation_id": _CID, "citas": []},
        "turns": ["Tengo seguro Nacional Vida, ¿me cubre?"],
        "success_criteria": "El agente debe tratar esto como validación de seguro (NO precio): pedir el número de carnet "
                            "para validar la cobertura con verify_insurance. No debe disparar la frase de 'no damos precios'.",
        "tags": ["forms", "seguro", "reactivo", "carnet"],
    },
    {
        "id": "forms_seguro_no_aceptado",
        "wa_id": "eval_591760000143",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Marta Ríos", "conversation_id": _CID, "citas": []},
        "turns": ["Tengo seguro con Vitalia"],
        "success_criteria": "Solo se trabaja con Membresía Odontoking, Nacional Vida y Alianza. Con Vitalia (no aceptado) "
                            "el agente debe decirlo con amabilidad y ofrecer agendar como particular o pasar con recepción; "
                            "NO debe intentar validarlo.",
        "tags": ["forms", "seguro", "no_aceptado", "d1"],
    },

    # ───────────────────────── Precios (E1) ─────────────────────────
    {
        "id": "forms_precio_primera_deflecta",
        "wa_id": "eval_591760000151",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Diego Lima", "conversation_id": _CID, "citas": []},
        "turns": ["¿Cuánto cuesta una limpieza?"],
        "success_criteria": "Primera pregunta de precio: el agente NO da precios; explica que no informa precios por chat "
                            "y ofrece agendar una cita de valoración. No debe derivar aún.",
        "tags": ["forms", "precio", "e1"],
    },
    {
        "id": "forms_precio_insiste_deriva",
        "wa_id": "eval_591760000152",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Diego Lima", "conversation_id": _CID, "citas": []},
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
        "wa_id": "eval_591760000161",
        "patient_context": {
            "is_new_patient": False, "nombre_registrado": "Elena Paz", "conversation_id": _CID,
            "cita_activa": 5001,
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
        "wa_id": "eval_591760000162",
        "patient_context": {
            "is_new_patient": False, "nombre_registrado": "Elena Paz", "conversation_id": _CID,
            "cita_activa": 5001,
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
        "wa_id": "eval_591760000163",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Elena Paz",
                            "conversation_id": _CID, "citas": [], "cita_activa": None},
        "turns": ["Quiero reprogramar mi cita para el jueves"],
        "success_criteria": "El paciente NO tiene ninguna cita activa: el agente debe avisar que no encontró una cita y "
                            "enviar el formulario de AGENDAR (paciente_tercero) para que agende una nueva; NO el de reprogramar.",
        "tags": ["forms", "reprogramar", "sin_cita"],
    },
    {
        "id": "forms_cancelar_sin_cita_activa",
        "wa_id": "eval_591760000164",
        "patient_context": {"is_new_patient": False, "nombre_registrado": "Elena Paz",
                            "conversation_id": _CID, "citas": [], "cita_activa": None},
        "turns": ["Quiero cancelar mi cita"],
        "success_criteria": "Sin cita activa, el agente avisa que no encontró una cita y ofrece/envía el formulario de "
                            "AGENDAR una nueva; NO debe enviar el de cancelar.",
        "tags": ["forms", "cancelar", "sin_cita"],
    },

    # ───────────────────────── Recordatorios (C1 / C2) ─────────────────────────
    {
        "id": "forms_recordatorio_confirma",
        "wa_id": "eval_591760000171",
        "patient_context": {
            "is_new_patient": False, "nombre_registrado": "Susana Mejía", "conversation_id": _CID,
            "citas": [{"lead_id": 7001, "fecha": "2026-10-28", "hora": "14:00",
                       "servicio": "General", "doctor": "Susana Urresti", "estado": "Agendado"}],
            "recordatorios": [{"activity_id": 117300, "lead_id": 7001, "fecha": "2026-10-28",
                               "hora": "14:00", "doctor": "Susana Urresti", "respuesta": None}],
        },
        "turns": ["Si"],
        "success_criteria": "Hay un recordatorio sin contestar (respuesta null): el 'Sí' responde a ESE recordatorio. "
                            "El agente debe registrar la confirmación con responder_recordatorio('confirma') y acusar "
                            "('¡Gracias por confirmar!'). NO debe mostrar el menú ni enviar un formulario.",
        "tags": ["forms", "recordatorio", "c1", "confirma"],
    },
    {
        "id": "forms_recordatorio_no_asiste",
        "wa_id": "eval_591760000172",
        "patient_context": {
            "is_new_patient": False, "nombre_registrado": "Susana Mejía", "conversation_id": _CID,
            "citas": [{"lead_id": 7001, "fecha": "2026-10-28", "hora": "14:00",
                       "servicio": "General", "doctor": "Susana Urresti", "estado": "Agendado"}],
            "recordatorios": [{"activity_id": 117300, "lead_id": 7001, "fecha": "2026-10-28",
                               "hora": "14:00", "doctor": "Susana Urresti", "respuesta": None}],
        },
        "turns": ["No, no voy a poder ir"],
        "success_criteria": "Hay un recordatorio sin contestar: el 'No' responde a esa cita. El agente debe registrar "
                            "responder_recordatorio('no_asiste') y LUEGO ofrecer reprogramar o cancelar con los formularios. "
                            "No debe cancelar la cita por su cuenta.",
        "tags": ["forms", "recordatorio", "c2", "no_asiste"],
    },
    {
        "id": "forms_estoy_en_camino_handoff",
        "wa_id": "eval_591760000173",
        "patient_context": {
            "is_new_patient": False, "nombre_registrado": "Susana Mejía", "conversation_id": _CID,
            "citas": [{"lead_id": 7001, "fecha": "2026-10-28", "hora": "14:00",
                       "servicio": "General", "doctor": "Susana Urresti", "estado": "Agendado"}],
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
        "wa_id": "eval_591760000181",
        "patient_context": {
            "is_new_patient": False, "nombre_registrado": "Gabriel Soto", "conversation_id": _CID,
            "cita_activa": 8001,
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
        "wa_id": "eval_591760000182",
        "patient_context": {
            "is_new_patient": False, "nombre_registrado": "Gabriel Soto", "conversation_id": _CID,
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
