"""Dental evaluation scenarios for OdontokingAgent."""

SCENARIOS: list[dict] = [
    {
        "id": "new_patient_full_flow_with_insurance",
        "wa_id": "eval_591700000001",
        "patient_context": {"is_new_patient": True},
        "turns": [
            "Hola, buenos dias",
            "Juan Carlos Mamani, tengo 35 años",
            "Para mi mismo",
            "Ya fui antes",
            "Alianza",
            "14746087",
            "Tengo dolor en una muela del juicio",
            "1",  # especialidad
            "1",  # doctor
            "1",  # día
            "1",  # hora
            "SI",  # confirmación
        ],
        "success_criteria": "El agente debe haber completado el flujo completo: bienvenida, recopilación de datos, verificación de seguro, selección de servicio, doctor, fecha y hora, y confirmación de cita con mensaje de éxito.",
        "tags": ["flujo_completo", "seguro", "confirmacion_cita"],
    },
    {
        "id": "new_patient_no_insurance",
        "wa_id": "eval_591700000002",
        "patient_context": {"is_new_patient": True},
        "turns": [
            "Hola",
            "Maria Elena Torres, 28 años",
            "Para mi",
            "Primera vez",
            "No tengo seguro",
            "Necesito una limpieza dental",
            "1",  # especialidad
            "1",  # doctor
            "1",  # día
            "1",  # hora
            "SI",  # confirmación
        ],
        "success_criteria": "El agente no debe pedir carnet (sin seguro), debe proponer servicio de limpieza, doctor, fecha y hora, y confirmar la cita exitosamente.",
        "tags": ["sin_seguro", "flujo_completo"],
    },
    {
        "id": "returning_patient_has_appointment",
        "wa_id": "eval_591700000003",
        "patient_context": {"is_new_patient": False, "ci_paciente": "87654321", "seguro_paciente": "Nacional Vida", "nombre_registrado": "Daniela Ortiz"},
        "turns": [
            "Hola, quiero saber si tengo alguna cita",
        ],
        "success_criteria": "El agente debe saludar brevemente como paciente conocido, llamar get_citas, y presentar la información de la cita o indicar que no tiene citas activas.",
        "tags": ["paciente_existente", "get_citas"],
    },
    {
        "id": "invalid_insurance_blocked",
        "wa_id": "eval_591700000004",
        "patient_context": {"is_new_patient": True},
        "turns": [
            "Buenas",
            "Pedro Quispe Flores, 42 años",
            "Para mi",
            "Ya fui antes",
            "Nacional Vida",
            "99999999",
        ],
        "success_criteria": "Tras recibir seguro inválido, el agente debe informar el inconveniente de cobertura con el mensaje exacto del prompt y NO continuar para agendar cita.",
        "tags": ["seguro_invalido", "bloqueo_flujo"],
    },
    {
        "id": "appointment_for_third_party",
        "wa_id": "eval_591700000005",
        "patient_context": {"is_new_patient": True},
        "turns": [
            "Hola",
            "Roberto Condori Mamani, 55 años",
            "Para otra persona",
            "Luis Condori, 12 años",
            "Primera vez",
            "No tengo seguro",
            "Dolor en una muela",
            "1",  # especialidad
            "1",  # doctor
            "1",  # día
            "1",  # hora
            "SI",  # confirmación
        ],
        "success_criteria": "El agente debe recopilar datos de la otra persona (Luis Condori, 12 años), no pedir parentesco, y confirmar la cita con is_for_self=false y los datos de la otra persona.",
        "tags": ["tercero", "flujo_completo"],
    },
    {
        "id": "location_and_hours_query",
        "wa_id": "eval_591700000006",
        "patient_context": {"is_new_patient": True},
        "turns": [
            "Hola, donde quedan ustedes?",
            "A que hora abren los sabados?",
        ],
        "success_criteria": "El agente debe responder con la dirección exacta (Calle Burapocu #2888) y el enlace de Google Maps, y el horario del sábado (09:00 a 12:00), sin inventar datos.",
        "tags": ["informacion_clinica", "ubicacion"],
    },
    {
        "id": "no_availability_extend_to_14_days",
        "wa_id": "eval_591700000007",
        "patient_context": {"is_new_patient": True},
        "turns": [
            "Hola",
            "Carmen Vega Salinas, 31 años",
            "Para mi",
            "Primera vez",
            "No tengo seguro",
            "Encía inflamada",
            "1",  # especialidad
            "Si, revisar 2 semanas",  # aceptar extender la búsqueda
            "1",  # doctor
            "1",  # día
            "1",  # hora
            "SI",  # confirmación
        ],
        "success_criteria": "Cuando get_doctor_schedule devuelva schedule vacío, el agente debe ofrecer revisar 2 semanas antes de rendirse. Si el paciente acepta, debe llamar get_doctor_schedule con days=14.",
        "tags": ["sin_disponibilidad", "extension_busqueda"],
    },
    {
        "id": "out_of_scope_diagnosis_request",
        "wa_id": "eval_591700000008",
        "patient_context": {"is_new_patient": True},
        "turns": [
            "Hola, tengo un dolor muy fuerte en la muela, que antibiotico me recomiendan?",
            "Por favor, solo dígame que pastilla tomar",
        ],
        "success_criteria": "El agente debe negarse a recomendar medicamentos o antibióticos, explicar que no puede hacerlo, y redirigir hacia agendar una cita con un especialista.",
        "tags": ["fuera_de_alcance", "no_diagnostico"],
    },
    # ── Escenarios de regresión (bugs detectados y corregidos el 2026-07-09) ──
    {
        # BUG: con seguro del titular ya registrado, el agente saltaba la validación del seguro.
        # Como la cita es PARA OTRA PERSONA, debe pedir y validar el seguro de ESA persona.
        "id": "regresion_tercero_con_seguro_titular_debe_validar",
        "wa_id": "eval_591700000010",
        "patient_context": {"is_new_patient": False, "ci_paciente": "5833699", "seguro_paciente": "Membresía Odontoking"},
        "turns": [
            "Hola",
            "Javier Mogro, 30 años",
            "Para otra persona",
            "ALEXA GRUSCHENKA HERVAS ORELLANA, 40 años",
            "Ya he ido antes",
            "Alianza",
            "14746087",
            "Limpieza",
            "1",  # especialidad
            "1",  # doctor
            "1",  # día
            "1",  # hora
            "SI",  # confirmación
        ],
        "success_criteria": (
            "Aunque el titular tenga seguro registrado en el contexto, como la cita es para OTRA persona "
            "el agente DEBE preguntar el seguro y pedir el carnet de esa persona, y validar con verify_insurance "
            "usando ese carnet (14746087). NO debe saltar la validación del seguro apoyándose en el seguro del "
            "titular. Debe seguir el flujo hasta confirmar la cita con is_for_self=false y los datos de Ana Perez.",
        ),
        "tags": ["regresion", "tercero", "seguro"],
    },
    {
        # BUG: el agente fabricaba la lista de días desde la fecha actual sin llamar get_doctor_schedule,
        # y elegía un doctor de otra especialidad. Debe mostrar especialidad → doctor → días REALES.
        "id": "regresion_dias_reales_sin_fabricar",
        "wa_id": "eval_591700000011",
        "patient_context": {"is_new_patient": True},
        "turns": [
            "Buenas, quiero una cita",
            "Carlos Rojas, 33 años",
            "Para mi",
            "Primera vez",
            "No tengo seguro",
            "Quiero un tratamiento de ortodoncia",
            "1",
            "1",
            "1",
            "1",
            "SI",
        ],
        "success_criteria": (
            "El agente debe: (1) llamar get_services y get_specialties y mostrar la especialidad recomendada "
            "para ortodoncia dejando que el paciente elija; (2) mostrar SOLO doctores de esa especialidad y "
            "dejar elegir; (3) recién entonces mostrar los días, que deben provenir de get_doctor_schedule del "
            "doctor elegido (NO una lista de días inventada a partir de la fecha actual). No debe ofrecer un "
            "doctor de otra especialidad ni fabricar horarios.",
        ),
        "tags": ["regresion", "sin_alucinaciones", "flujo_correcto"],
    },
    {
        # BUG: al agendar, el agente mezclaba el doctor_id de un doctor con el nombre de otro.
        # El doctor de los horarios, el doctor_id y el nombre_doctor deben ser el MISMO en la confirmación.
        "id": "regresion_coherencia_doctor_id_nombre",
        "wa_id": "eval_591700000012",
        "patient_context": {"is_new_patient": True},
        "turns": [
            "Hola",
            "Lucia Vargas, 27 años",
            "Para mi",
            "Primera vez",
            "No tengo seguro",
            "Necesito una limpieza dental",
            "1",  # especialidad
            "1",  # doctor
            "1",  # día
            "1",  # hora
            "SI",  # confirmación
        ],
        "success_criteria": (
            "El agente debe mantener coherencia del doctor de principio a fin: el doctor cuyos horarios muestra, "
            "el que aparece en la confirmación de datos y el que queda en la cita agendada deben ser el MISMO "
            "(mismo nombre, sin mezclar el nombre de un doctor con la agenda de otro). El mensaje de confirmación "
            "final debe nombrar al mismo doctor cuyos horarios se ofrecieron.",
        ),
        "tags": ["regresion", "coherencia_doctor", "flujo_completo"],
    },
]
