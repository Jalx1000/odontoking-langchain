{
  "inputs": {
    "input": {
      "goto": [],
      "graph": null,
      "resume": "si",
      "update": null
    }
  },
  "outputs": {
    "messages": [
      {
        "content": "ABRAM FRIESEN WALL\n36",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "human",
        "id": "2fd7d5ea-c49c-40a7-8490-74b349b653e1"
      },
      {
        "content": "",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 88,
            "prompt_tokens": 8767,
            "total_tokens": 8855,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 6016
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPUPVgGOBeZn4W0FFDXbi44NJAYv",
          "service_tier": "default",
          "finish_reason": "tool_calls",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--9f1a4c15-69ac-43f9-a0d5-ac0fd51b3aeb-0",
        "tool_calls": [
          {
            "name": "save_patient",
            "args": {
              "wa_id": "59170088388",
              "person_name": "ABRAM FRIESEN WALL",
              "person_phone": "59170088388",
              "edad_paciente": 36,
              "is_for_self": true
            },
            "id": "call_xHHAitkeQ24iQ3qZFcAxKn9x",
            "type": "tool_call"
          },
          {
            "name": "get_services",
            "args": {},
            "id": "call_sEMr0XAk5r7xHRBHBZxVRPYk",
            "type": "tool_call"
          },
          {
            "name": "get_specialties",
            "args": {},
            "id": "call_vp4eYqE0GtsIhnW71cZffxci",
            "type": "tool_call"
          }
        ],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 8767,
          "output_tokens": 88,
          "total_tokens": 8855,
          "input_token_details": {
            "audio": 0,
            "cache_read": 6016
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "{\"success\": true, \"person_id\": 2824, \"lead_id\": 2716}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "save_patient",
        "id": "e89af2d8-f064-4e1a-bf3b-766b64af24b6",
        "tool_call_id": "call_xHHAitkeQ24iQ3qZFcAxKn9x",
        "status": "success"
      },
      {
        "content": "{\"data\": [{\"id\": 306, \"name\": \"Sedacion Conciente\", \"duration_minutes\": null}, {\"id\": 182, \"name\": \"Consultas Ortodoncia\", \"duration_minutes\": null}, {\"id\": 180, \"name\": \"Radiografías en tratamientos de endodoncia\", \"duration_minutes\": null}, {\"id\": 179, \"name\": \"Protección pulpar\", \"duration_minutes\": null}, {\"id\": 178, \"name\": \"Aplicación de cariostatico en dientes de leche por consulta\", \"duration_minutes\": null}, {\"id\": 177, \"name\": \"Aplicación tópica de ﬂúor\", \"duration_minutes\": null}, {\"id\": 176, \"name\": \"Aplicación de revelador de placa bacteriana 2 x año\", \"duration_minutes\": null}, {\"id\": 175, \"name\": \"Profilaxis dental 2 x año\", \"duration_minutes\": null}, {\"id\": 174, \"name\": \"Limpieza de toda la cavidad bucal (eliminación de la placa bacteriana)\", \"duration_minutes\": null}, {\"id\": 173, \"name\": \"Consulta de emergencias\", \"duration_minutes\": null}, {\"id\": 172, \"name\": \"RX periapical en trat. endodóntico\", \"duration_minutes\": null}, {\"id\": 171, \"name\": \"Tomografía articulación temporo maxilar\", \"duration_minutes\": null}, {\"id\": 170, \"name\": \"Tomografía maxilo facial completa\", \"duration_minutes\": null}, {\"id\": 169, \"name\": \"Tomografía maxilo facial de un cuadrante\", \"duration_minutes\": null}, {\"id\": 168, \"name\": \"RX lateral de cráneo\", \"duration_minutes\": null}, {\"id\": 167, \"name\": \"RX  PANORÁMICA\", \"duration_minutes\": null}, {\"id\": 166, \"name\": \"Restauraciones estéticas compuestas (por cambio de amalgama)\", \"duration_minutes\": null}, {\"id\": 165, \"name\": \"Restauraciones estéticas simples (por cambio de amalgama)\", \"duration_minutes\": null}, {\"id\": 164, \"name\": \"Pulido de resina P/ Brakets\", \"duration_minutes\": null}, {\"id\": 163, \"name\": \"Placa complementaria en ortodoncias\", \"duration_minutes\": null}, {\"id\": 162, \"name\": \"Placas de Contención\", \"duration_minutes\": null}, {\"id\": 161, \"name\": \"Estudio cefalométrico\", \"duration_minutes\": null}, {\"id\": 160, \"name\": \"Ortodoncia (casos graves malformaciones c-1, c-2, c-3)\", \"duration_minutes\": null}, {\"id\": 159, \"name\": \"Ortodoncia (casos moderados   malformaciones c-1, c-2, c-3)\", \"duration_minutes\": null}, {\"id\": 158, \"name\": \"Ortopedia (casos graves de malformaciones c-1, c-2, c-3)\", \"duration_minutes\": null}, {\"id\": 157, \"name\": \"Ortopedia (casos moderados de malformaciones c-1, c-2, c-3)\", \"duration_minutes\": null}, {\"id\": 156, \"name\": \"Levantamiento de Seno Maxilar sin Injerto\", \"duration_minutes\": null}, {\"id\": 155, \"name\": \"Regeneración Ósea con Malla de Titanio o Teflón\", \"duration_minutes\": null}, {\"id\": 154, \"name\": \"Prótesis hibrida con 6 Implantes\", \"duration_minutes\": null}, {\"id\": 153, \"name\": \"Prótesis hibrida con 5 Implantes\", \"duration_minutes\": null}, {\"id\": 152, \"name\": \"Prótesis hibrida con 4 Implantes\", \"duration_minutes\": null}, {\"id\": 151, \"name\": \"Prótesis sobre implante (por elemento)\", \"duration_minutes\": null}, {\"id\": 150, \"name\": \"Cirugía pre-protésica (por elemento)\", \"duration_minutes\": null}, {\"id\": 149, \"name\": \"Cirugía de colocación de implante (por elemento)\", \"duration_minutes\": null}, {\"id\": 148, \"name\": \"Injerto óseo (hasta 3 piezas)\", \"duration_minutes\": null}, {\"id\": 147, \"name\": \"Injerto óseo  (1 pieza)\", \"duration_minutes\": null}, {\"id\": 146, \"name\": \"Exodoncia pre-implante  (PM - M)\", \"duration_minutes\": null}, {\"id\": 145, \"name\": \"Exodoncia pre-implante anteriores\", \"duration_minutes\": null}, {\"id\": 144, \"name\": \"Consulta, diagnóstico y ﬁcha odontograﬁca\", \"duration_minutes\": null}, {\"id\": 143, \"name\": \"Bichectomía\", \"duration_minutes\": null}, {\"id\": 142, \"name\": \"Colocación de botón quirúrgico\", \"duration_minutes\": null}, {\"id\": 141, \"name\": \"Mucocele con láser\", \"duration_minutes\": null}, {\"id\": 139, \"name\": \"L-PRF (Plasma)\", \"duration_minutes\": null}, {\"id\": 138, \"name\": \"Regularización ósea por exodoncia múltiple por arcada\", \"duration_minutes\": null}, {\"id\": 137, \"name\": \"Cirugía de piezas supernumerarios Incluida\", \"duration_minutes\": null}, {\"id\": 136, \"name\": \"Cirugía de piezas supernumerarios semi-Inclusos\", \"duration_minutes\": null}, {\"id\": 135, \"name\": \"Apicectomía (por elemento; remanente de raíz)\", \"duration_minutes\": null}, {\"id\": 134, \"name\": \"Ostectomía (corte de hueso)\", \"duration_minutes\": null}, {\"id\": 133, \"name\": \"Odontoseccion (por pieza dentaria)\", \"duration_minutes\": null}, {\"id\": 132, \"name\": \"Exodoncia con alveolectomía\", \"duration_minutes\": null}, {\"id\": 131, \"name\": \"Cirugía 3ros. molares inclusos o el caninos retenidos\", \"duration_minutes\": null}, {\"id\": 130, \"name\": \"Cirugía 3ros. molares semi-Inclusos\", \"duration_minutes\": null}, {\"id\": 129, \"name\": \"Cirugía 3ros. molares erupcionados\", \"duration_minutes\": null}, {\"id\": 128, \"name\": \"Extracciones dentarias\", \"duration_minutes\": null}, {\"id\": 127, \"name\": \"Cromo flex\", \"duration_minutes\": null}, {\"id\": 126, \"name\": \"Rebasado de prótesis totales en consultorio\", \"duration_minutes\": null}, {\"id\": 125, \"name\": \"Rebasado de prótesis totales (laboratorio)\", \"duration_minutes\": null}, {\"id\": 124, \"name\": \"Reparación de placas de cromo-cobalto (laboratorio acrílico)\", \"duration_minutes\": null}, {\"id\": 123, \"name\": \"Reparación de placas de acrílico (laboratorio)\", \"duration_minutes\": null}, {\"id\": 122, \"name\": \"Prótesis acrílico parciales de una a dos piezas\", \"duration_minutes\": null}, {\"id\": 121, \"name\": \"Prótesis ﬂexible estética de 6 a más dientes\", \"duration_minutes\": null}, {\"id\": 120, \"name\": \"Prótesis ﬂexible estética de 2 a 5 dientes\", \"duration_minutes\": null}, {\"id\": 119, \"name\": \"Prótesis ﬂexible estética un diente\", \"duration_minutes\": null}, {\"id\": 118, \"name\": \"Prótesis esquelética cromo-cobalto (con dientes ivoclar)\", \"duration_minutes\": null}, {\"id\": 117, \"name\": \"Prótesis esquelética cromo-cobalto (con dientes normales)\", \"duration_minutes\": null}, {\"id\": 116, \"name\": \"Prótesis total superior inferior (acrílico) dientes ivoclar\", \"duration_minutes\": null}, {\"id\": 115, \"name\": \"Prótesis total superior o inferior (acrílico) dientes normales\", \"duration_minutes\": null}, {\"id\": 114, \"name\": \"Prótesis parcial removible (acrílico) (Ac lucitone porcelana ivoclar)\", \"duration_minutes\": null}, {\"id\": 113, \"name\": \"Prótesis parcial removible (acrílico) dientes normales\", \"duration_minutes\": null}, {\"id\": 112, \"name\": \"Coronas provisionales (por pieza)\", \"duration_minutes\": null}, {\"id\": 111, \"name\": \"Endocorona de resina\", \"duration_minutes\": null}, {\"id\": 110, \"name\": \"Retiro de perno\", \"duration_minutes\": null}, {\"id\": 109, \"name\": \"Cementado de coronas con RL. U200\", \"duration_minutes\": null}, {\"id\": 108, \"name\": \"Coronas 3/4\", \"duration_minutes\": null}, {\"id\": 107, \"name\": \"Incrustaciones porcelana empress\", \"duration_minutes\": null}, {\"id\": 106, \"name\": \"Incrustaciones\", \"duration_minutes\": null}, {\"id\": 105, \"name\": \"Puente metal porcelana (por elemento) design\", \"duration_minutes\": null}, {\"id\": 104, \"name\": \"Puente metal cerámico (por elemento) empress II (hombro cerámico)\", \"duration_minutes\": null}, {\"id\": 103, \"name\": \"Carilla de porcelana\", \"duration_minutes\": null}, {\"id\": 101, \"name\": \"Carilla de ivocron\", \"duration_minutes\": null}, {\"id\": 100, \"name\": \"Carilla de resina - Laboratorio\", \"duration_minutes\": null}, {\"id\": 99, \"name\": \"Corona metal cerámica - porcelana\", \"duration_minutes\": null}, {\"id\": 98, \"name\": \"Corona Metálica Total o Completa (Remanium)\", \"duration_minutes\": null}, {\"id\": 97, \"name\": \"Corona veener metal porcelana-cerámica\", \"duration_minutes\": null}, {\"id\": 96, \"name\": \"Corona Veneer Metal\", \"duration_minutes\": null}, {\"id\": 95, \"name\": \"Corona de zirconio\", \"duration_minutes\": null}, {\"id\": 94, \"name\": \"Corona Porcelana Pura\", \"duration_minutes\": null}, {\"id\": 93, \"name\": \"Corona Jacket de Ivocron\", \"duration_minutes\": null}, {\"id\": 92, \"name\": \"Perno muñón estético blanco\", \"duration_minutes\": null}, {\"id\": 91, \"name\": \"Perno Muñón Multirradicular\", \"duration_minutes\": null}, {\"id\": 90, \"name\": \"Perno muñón unirradicular\", \"duration_minutes\": null}, {\"id\": 89, \"name\": \"Injerto de tejido conectivo 1 a 3 piezas\", \"duration_minutes\": null}, {\"id\": 88, \"name\": \"Osteoplastia\", \"duration_minutes\": null}, {\"id\": 87, \"name\": \"Tratamiento no quirúrgico periodontal por sextante\", \"duration_minutes\": null}, {\"id\": 86, \"name\": \"Despigmentación melanina por arcada con láser\", \"duration_minutes\": null}, {\"id\": 85, \"name\": \"Frenectomía normal con láser\", \"duration_minutes\": null}, {\"id\": 84, \"name\": \"Frenectomía  compuesta\", \"duration_minutes\": null}, {\"id\": 83, \"name\": \"Frenectomía normal\", \"duration_minutes\": null}, {\"id\": 82, \"name\": \"Raspado y Alisado Radicular x seción\", \"duration_minutes\": null}, {\"id\": 81, \"name\": \"Placa miorrelajante de acrilico II\", \"duration_minutes\": null}, {\"id\": 80, \"name\": \"Placa de mordida miorrelajante rígida\", \"duration_minutes\": null}, {\"id\": 79, \"name\": \"Placa de mordida miorrelajante ﬂexible\", \"duration_minutes\": null}, {\"id\": 77, \"name\": \"Ajuste oclusal\", \"duration_minutes\": null}, {\"id\": 76, \"name\": \"Ferulización/inmovilización  con resina fotopolimerizable (por pieza)\", \"duration_minutes\": null}, {\"id\": 75, \"name\": \"Ulectomia (corte de capuchón para erupción dentaria) 1pza/año\", \"duration_minutes\": null}, {\"id\": 74, \"name\": \"Gingivoplastía 1Pza/año\", \"duration_minutes\": null}, {\"id\": 73, \"name\": \"Gingivectomia 1 Pza/año\", \"duration_minutes\": null}, {\"id\": 72, \"name\": \"Retratamiento multiradicular\", \"duration_minutes\": null}, {\"id\": 71, \"name\": \"Retratamiento en biradiculares\", \"duration_minutes\": null}, {\"id\": 70, \"name\": \"Retratamientos endodonticos uniradiculares\", \"duration_minutes\": null}, {\"id\": 69, \"name\": \"Por inasistencia prolongada  (más de tres semanas a partir de la última cita)\", \"duration_minutes\": null}, {\"id\": 68, \"name\": \"Proceso periapical (infección crónica)\", \"duration_minutes\": null}, {\"id\": 67, \"name\": \"Inducción a la apicoformación\", \"duration_minutes\": null}, {\"id\": 66, \"name\": \"Tratamientos endodonticos en piezas calciﬁcadas\", \"duration_minutes\": null}, {\"id\": 65, \"name\": \"Radiografías adicionales en tratamientos endodonticos\", \"duration_minutes\": null}, {\"id\": 64, \"name\": \"Pulpotomía cameral  (en caso de caries avanzadas)\", \"duration_minutes\": null}, {\"id\": 63, \"name\": \"Tratamiento de conducto en molares (superiores e inferiores)\", \"duration_minutes\": null}, {\"id\": 62, \"name\": \"Tratamiento de conducto en premolares (superiores e inferiores)\", \"duration_minutes\": null}, {\"id\": 61, \"name\": \"Tratamiento de conducto en piezas anteriores (incisivos y caninos)\", \"duration_minutes\": null}, {\"id\": 60, \"name\": \"Radiografías panorámica\", \"duration_minutes\": null}, {\"id\": 59, \"name\": \"Radiografías periapical en tratamientos endodocicos\", \"duration_minutes\": null}, {\"id\": 58, \"name\": \"Mantenimiento de carilla\", \"duration_minutes\": null}, {\"id\": 57, \"name\": \"Diseño de sonrisa Digital\", \"duration_minutes\": null}, {\"id\": 56, \"name\": \"Diseño de sonrisa (Mockup)\", \"duration_minutes\": null}, {\"id\": 55, \"name\": \"Bordes incisales clase IV alargamiento de corona\", \"duration_minutes\": null}, {\"id\": 54, \"name\": \"Carillas lentes de contacto\", \"duration_minutes\": null}, {\"id\": 53, \"name\": \"Carillas de resinas\", \"duration_minutes\": null}, {\"id\": 52, \"name\": \"Carillas de porcelana\", \"duration_minutes\": null}, {\"id\": 51, \"name\": \"Cierre de diastemas\", \"duration_minutes\": null}, {\"id\": 50, \"name\": \"1 sesión de blanqueamiento total\", \"duration_minutes\": null}, {\"id\": 49, \"name\": \"Blanqueamiento ambulatorio  (cubetas)\", \"duration_minutes\": null}, {\"id\": 48, \"name\": \"Blanqueamiento inmediato con láser\", \"duration_minutes\": null}, {\"id\": 47, \"name\": \"Blanqueamiento interno (por pieza dentaria)\", \"duration_minutes\": null}, {\"id\": 46, \"name\": \"Pegado de botón quirúrgico\", \"duration_minutes\": null}, {\"id\": 45, \"name\": \"Refuerzo con  perno de ﬁbra de vidrio\", \"duration_minutes\": null}, {\"id\": 44, \"name\": \"Refuerzo con pin\", \"duration_minutes\": null}, {\"id\": 43, \"name\": \"Carillas de resina fotopolimerizable\", \"duration_minutes\": null}, {\"id\": 42, \"name\": \"Corona de resina fotopolimerizable\", \"duration_minutes\": null}, {\"id\": 41, \"name\": \"Obturación  3º grado resina fotopolimerizable\", \"duration_minutes\": null}, {\"id\": 40, \"name\": \"Obturación  1º grado resina fotopolimerizable\", \"duration_minutes\": null}, {\"id\": 39, \"name\": \"Restauración con Ionomero de vidrio\", \"duration_minutes\": null}, {\"id\": 38, \"name\": \"Obturación  2º grado resina fotopolimerizable\", \"duration_minutes\": null}, {\"id\": 34, \"name\": \"Sellantes en dentadura permanente\", \"duration_minutes\": null}, {\"id\": 33, \"name\": \"Radiograﬁa panorámica digital\", \"duration_minutes\": null}, {\"id\": 32, \"name\": \"Barnis de Fluor\", \"duration_minutes\": null}, {\"id\": 31, \"name\": \"Pulpectomía\", \"duration_minutes\": null}, {\"id\": 30, \"name\": \"Pulpotomía\", \"duration_minutes\": null}, {\"id\": 29, \"name\": \"Extracción compleja\", \"duration_minutes\": null}, {\"id\": 28, \"name\": \"Extracción simple (sin anestesia)\", \"duration_minutes\": null}, {\"id\": 27, \"name\": \"Cariostaticos en dientes deciduos por pieza\", \"duration_minutes\": null}, {\"id\": 26, \"name\": \"Sellantes\", \"duration_minutes\": null}, {\"id\": 25, \"name\": \"Restauraciones compuestas\", \"duration_minutes\": null}, {\"id\": 24, \"name\": \"Restauración de resina, fotopolemirezable de una superﬁcie\", \"duration_minutes\": null}, {\"id\": 23, \"name\": \"Obturación con Ionomeros\", \"duration_minutes\": null}, {\"id\": 22, \"name\": \"Atención pos natal odontológico\", \"duration_minutes\": null}, {\"id\": 21, \"name\": \"Atención prenatal odontológico\", \"duration_minutes\": null}, {\"id\": 20, \"name\": \"Radiografía panorámica digital\", \"duration_minutes\": null}, {\"id\": 19, \"name\": \"Corona provisoria sin perno\", \"duration_minutes\": null}, {\"id\": 18, \"name\": \"Retiro de mantenedores de espacio\", \"duration_minutes\": null}, {\"id\": 17, \"name\": \"Retiro de puentes\", \"duration_minutes\": null}, {\"id\": 16, \"name\": \"Retiro de coronas y 3/4 Metálica\", \"duration_minutes\": null}, {\"id\": 15, \"name\": \"Cementado de coronas Jackets con cemento fosfato\", \"duration_minutes\": null}, {\"id\": 14, \"name\": \"Técnica de cepillado dental - clínico\", \"duration_minutes\": null}, {\"id\": 13, \"name\": \"Extracción dental en niños\", \"duration_minutes\": null}, {\"id\": 12, \"name\": \"Drenaje de absceso pulpar\", \"duration_minutes\": null}, {\"id\": 11, \"name\": \"Radiografía periapical\", \"duration_minutes\": null}, {\"id\": 10, \"name\": \"Tratamiento con láser en cicatrización postcirugia\", \"duration_minutes\": null}, {\"id\": 9, \"name\": \"Tratamiento con láser para aftas en cavidad bucal\", \"duration_minutes\": null}, {\"id\": 8, \"name\": \"Tratamiento con láser de inflamación de encías\", \"duration_minutes\": null}]}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "get_services",
        "id": "5e016198-bcce-4e32-bd8f-f9e80589e63a",
        "tool_call_id": "call_sEMr0XAk5r7xHRBHBZxVRPYk",
        "status": "success"
      },
      {
        "content": "{\"data\": [{\"id\": 2, \"name\": \"Endodoncia\", \"slug\": \"endodoncia\", \"created_at\": \"2026-01-27T12:13:08.000000Z\", \"updated_at\": \"2026-07-07T20:46:28.000000Z\", \"description\": \"<p>Especialidad dedicada al tratamiento del interior del diente cuando la pulpa est&aacute; inflamada o infectada. Realiza tratamientos de conducto para aliviar el dolor, eliminar la infecci&oacute;n y conservar el diente.</p>\"}, {\"id\": 3, \"name\": \"Odontopediatria\", \"slug\": \"odontopediatria\", \"created_at\": \"2026-01-28T10:46:36.000000Z\", \"updated_at\": \"2026-03-18T07:20:25.000000Z\", \"description\": \"<p>La odontopediatr&iacute;a enfocada en la atenci&oacute;n integral de beb&eacute;s, ni&ntilde;os y adolescentes, encargada de prevenir, diagnosticar y tratar problemas dentales desde las primeras etapas de crecimiento.</p>\"}, {\"id\": 6, \"name\": \"Ortodoncia\", \"slug\": \"ortodoncia\", \"created_at\": \"2026-03-18T06:51:35.000000Z\", \"updated_at\": \"2026-03-18T07:19:56.000000Z\", \"description\": \"<p>La ortodoncia encargada de diagnosticar, prevenir y corregir la posici&oacute;n de los dientes y la mordida.</p>\"}, {\"id\": 9, \"name\": \"Protesista\", \"slug\": \"protesista\", \"created_at\": \"2026-03-18T07:14:03.000000Z\", \"updated_at\": \"2026-03-18T07:14:03.000000Z\", \"description\": \"<p>La protesista dental se encarga de la rehabilitaci&oacute;n y restauraci&oacute;n de dientes perdidos o da&ntilde;ados mediante pr&oacute;tesis dentales, ya sean fijas o removibles.</p>\"}, {\"id\": 10, \"name\": \"Periodontologia\", \"slug\": \"periodontologia\", \"created_at\": \"2026-03-18T07:14:44.000000Z\", \"updated_at\": \"2026-07-07T20:57:47.000000Z\", \"description\": \"<p>Especialidad dedicada a la prevenci&oacute;n, diagn&oacute;stico y tratamiento de las enfermedades de las enc&iacute;as y los tejidos que sostienen los dientes, como gingivitis, periodontitis, sangrado, inflamaci&oacute;n y movilidad dental.</p>\"}, {\"id\": 11, \"name\": \"Estética Dental\", \"slug\": \"estetica-dental\", \"created_at\": \"2026-03-18T07:15:42.000000Z\", \"updated_at\": \"2026-07-07T20:54:58.000000Z\", \"description\": \"<p>Especialidad enfocada en mejorar la apariencia de la sonrisa mediante tratamientos como blanqueamiento dental, dise&ntilde;o de sonrisa, carillas, contorneado dental y otros procedimientos est&eacute;ticos.</p>\"}, {\"id\": 12, \"name\": \"Sedacion\", \"slug\": \"sedacion\", \"created_at\": \"2026-03-18T07:16:22.000000Z\", \"updated_at\": \"2026-04-09T15:43:43.000000Z\", \"description\": \"<p>La sedaci&oacute;n consciente que permite al paciente mantenerse relajado y tranquilo durante los procedimientos, sin perder la capacidad de responder a est&iacute;mulos o indicaciones del profesional.</p>\"}, {\"id\": 13, \"name\": \"Ortopedia\", \"slug\": \"ortopedia\", \"created_at\": \"2026-03-18T07:17:35.000000Z\", \"updated_at\": \"2026-07-07T20:49:36.000000Z\", \"description\": \"<p>La ortopedia dental enfocada en guiar el crecimiento y desarrollo de los maxilares, principalmente en ni&ntilde;os y adolescentes, para corregir alteraciones en la mordida y favorecer una adecuada alineaci&oacute;n dental mediante aparatos ortop&eacute;dicos.</p>\"}, {\"id\": 14, \"name\": \"Implantologia\", \"slug\": \"implantologia\", \"created_at\": \"2026-03-18T07:18:27.000000Z\", \"updated_at\": \"2026-03-18T07:18:27.000000Z\", \"description\": \"<p>La implantolog&iacute;a encargada de reemplazar piezas dentales perdidas mediante la colocaci&oacute;n de implantes dentales, que act&uacute;an como ra&iacute;ces artificiales sobre las cuales se fijan pr&oacute;tesis.</p>\"}, {\"id\": 15, \"name\": \"Rehabilitacion\", \"slug\": \"rehabilitacion\", \"created_at\": \"2026-03-18T07:19:23.000000Z\", \"updated_at\": \"2026-04-09T15:50:08.000000Z\", \"description\": \"<p>La rehabilitaci&oacute;n oral encargada de restaurar la funci&oacute;n y est&eacute;tica de la boca mediante la recuperaci&oacute;n integral de los dientes y estructuras afectadas.</p>\"}, {\"id\": 20, \"name\": \"Odontología General\", \"slug\": \"odontologia-general\", \"created_at\": \"2026-04-07T15:48:12.000000Z\", \"updated_at\": \"2026-07-07T20:43:20.000000Z\", \"description\": \"<p>Atenci&oacute;n integral para el diagn&oacute;stico, prevenci&oacute;n y tratamiento de los problemas dentales m&aacute;s comunes. Incluye revisi&oacute;n dental, limpieza, tratamiento de caries, restauraciones (empastes), dolor de dientes, sensibilidad dental y evaluaci&oacute;n para derivar a otras especialidades si es necesario.&nbsp;</p>\"}, {\"id\": 24, \"name\": \"Radiologo\", \"slug\": \"radiologo\", \"created_at\": \"2026-04-09T15:41:43.000000Z\", \"updated_at\": \"2026-07-07T20:41:56.000000Z\", \"description\": \"<p>Especialidad encargada de realizar estudios de imagen para el diagn&oacute;stico y planificaci&oacute;n de tratamientos odontol&oacute;gicos, como radiograf&iacute;as panor&aacute;micas, periapicales y tomograf&iacute;as dentales.</p>\"}, {\"id\": 26, \"name\": \"Cirugia Dental\", \"slug\": \"cirugia-dental\", \"created_at\": \"2026-04-15T21:10:13.000000Z\", \"updated_at\": \"2026-07-07T20:40:51.000000Z\", \"description\": \"<p>Especialidad encargada de la extracci&oacute;n de dientes y muelas, especialmente muelas del juicio, as&iacute; como del tratamiento de infecciones, quistes, dientes retenidos y otros procedimientos quir&uacute;rgicos de la boca.</p>\"}, {\"id\": 242, \"name\": \"General\", \"slug\": \"general\", \"created_at\": \"2026-07-07T21:01:38.000000Z\", \"updated_at\": \"2026-07-07T21:01:38.000000Z\", \"description\": null}]}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "get_specialties",
        "id": "b01851fd-d08f-485f-8ffd-635548a08f7d",
        "tool_call_id": "call_vp4eYqE0GtsIhnW71cZffxci",
        "status": "success"
      },
      {
        "content": "¿La consulta es para usted o para otra persona? 📝\n\n1. Para mí\n2. Para otra persona",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 25,
            "prompt_tokens": 15086,
            "total_tokens": 15111,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 8832
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPUVasfrdajzPaLtap6m1EIAJnbM",
          "service_tier": "default",
          "finish_reason": "stop",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--baf0295f-8dd2-4105-9baa-b04d8661c6f1-0",
        "tool_calls": [],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 15086,
          "output_tokens": 25,
          "total_tokens": 15111,
          "input_token_details": {
            "audio": 0,
            "cache_read": 8832
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "Para mí",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "human",
        "id": "6b6b316c-3eb1-4786-8456-44ea55da488a"
      },
      {
        "content": "¿Vino antes a la clínica o es primera vez? \n\n1. Primera vez\n2. Ya he ido antes",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 25,
            "prompt_tokens": 15124,
            "total_tokens": 15149,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 6016
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPUhgTcy6NtsNCJdQjEfbN1VYcHw",
          "service_tier": "default",
          "finish_reason": "stop",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--0bc05202-839e-4364-9ec4-9e281085b272-0",
        "tool_calls": [],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 15124,
          "output_tokens": 25,
          "total_tokens": 15149,
          "input_token_details": {
            "audio": 0,
            "cache_read": 6016
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "Ya he ido antes",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "human",
        "id": "1989b760-4d86-4a56-91e0-5e41c59a09f4"
      },
      {
        "content": "¿Cuenta con algún seguro dental? 🦷📄\n\n1. Alianza\n2. Nacional Vida\n3. Membresía Odontoking\n4. No tengo seguro",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 41,
            "prompt_tokens": 15160,
            "total_tokens": 15201,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 0
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPUsycosrXdwHioRnjIm3wYZ0ebS",
          "service_tier": "default",
          "finish_reason": "stop",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--4e087254-3dbf-4587-b85b-b5e2db338648-0",
        "tool_calls": [],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 15160,
          "output_tokens": 41,
          "total_tokens": 15201,
          "input_token_details": {
            "audio": 0,
            "cache_read": 0
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "Membresía Odontoking",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "human",
        "id": "8faa1150-1992-4d04-9504-b78d3cd77c2e"
      },
      {
        "content": "Para poder validar su seguro, ¿nos podría compartir su número de carnet de identidad, por favor? 🪪",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 24,
            "prompt_tokens": 15215,
            "total_tokens": 15239,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 15104
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPVFQPIUwTkv9lEl4QuMoym6kqHl",
          "service_tier": "default",
          "finish_reason": "stop",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--6d29b717-6f1c-45be-ac20-512ecad7fa90-0",
        "tool_calls": [],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 15215,
          "output_tokens": 24,
          "total_tokens": 15239,
          "input_token_details": {
            "audio": 0,
            "cache_read": 15104
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "5833699",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "human",
        "id": "b7538648-07ff-43e6-a4d9-00ef2b0a9fbf"
      },
      {
        "content": "",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 40,
            "prompt_tokens": 15249,
            "total_tokens": 15289,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 15104
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPVT5fMr3OGf9A4GLV9G7zaoH3w2",
          "service_tier": "default",
          "finish_reason": "tool_calls",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--ad8836a3-fd9d-4cd9-aabf-54b96773bb50-0",
        "tool_calls": [
          {
            "name": "verify_insurance",
            "args": {
              "wa_id": "59170088388",
              "ci_paciente": "5833699",
              "seguro_paciente": "Membresía Odontoking"
            },
            "id": "call_MlBIjX48EocPHBmwBgUeaR6g",
            "type": "tool_call"
          }
        ],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 15249,
          "output_tokens": 40,
          "total_tokens": 15289,
          "input_token_details": {
            "audio": 0,
            "cache_read": 15104
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "{\"has_insurance\": true, \"status\": \"VIGENTE\", \"message\": \"¡Membresía activa! El paciente puede ser atendido con los beneficios Odontoking.\", \"seguro_name\": \"Membresía Odontoking\", \"patient_name\": \"ABRAM FRIESEN WALL\"}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "verify_insurance",
        "id": "93509178-9a5f-4f1d-818f-9b572dafcb4b",
        "tool_call_id": "call_MlBIjX48EocPHBmwBgUeaR6g",
        "status": "success"
      },
      {
        "content": "",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 68,
            "prompt_tokens": 15363,
            "total_tokens": 15431,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 15232
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPVgYAoydwjdTyaTdUMuhAqa7DoB",
          "service_tier": "default",
          "finish_reason": "tool_calls",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--9e291f31-eef9-45e0-a81f-f2075b52f872-0",
        "tool_calls": [
          {
            "name": "save_insurance",
            "args": {
              "wa_id": "59170088388",
              "seguro_de_vida": "Membresía Odontoking",
              "numero_carnet": "5833699",
              "estado_seguro": "VIGENTE",
              "person_name": "ABRAM FRIESEN WALL",
              "person_phone": "59170088388"
            },
            "id": "call_0snXBjA42UMLK2ngqTZhcBpF",
            "type": "tool_call"
          }
        ],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 15363,
          "output_tokens": 68,
          "total_tokens": 15431,
          "input_token_details": {
            "audio": 0,
            "cache_read": 15232
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "{\"success\": true, \"person_id\": 2824, \"lead_id\": 2716}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "save_insurance",
        "id": "bf6ce82e-3ae2-425f-8ed3-0614fa4cf6cb",
        "tool_call_id": "call_0snXBjA42UMLK2ngqTZhcBpF",
        "status": "success"
      },
      {
        "content": "¿Qué molestia o servicio necesita? 🦷💬\n\n1. Dolor dental\n2. Diente quebrado\n3. Encía inflamada\n4. Limpieza\n5. Otro",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 42,
            "prompt_tokens": 15461,
            "total_tokens": 15503,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 0
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPVjMPl0kT7VuUI63c0OjF0MDjAk",
          "service_tier": "default",
          "finish_reason": "stop",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--49a8159c-cbac-42ec-a0db-b6ee1e650e25-0",
        "tool_calls": [],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 15461,
          "output_tokens": 42,
          "total_tokens": 15503,
          "input_token_details": {
            "audio": 0,
            "cache_read": 0
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "Quiero consulta con ortodoncia",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "human",
        "id": "ebc386d1-ba07-42f8-a6e3-6f34de23bfd2"
      },
      {
        "content": "",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 40,
            "prompt_tokens": 15451,
            "total_tokens": 15491,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 6016
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPWFfJkfOnXScbM4yCe8lxASFdcW",
          "service_tier": "default",
          "finish_reason": "tool_calls",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--9e32091d-e3e8-4952-ad99-d4c29d36ff39-0",
        "tool_calls": [
          {
            "name": "get_services",
            "args": {},
            "id": "call_W8w1yHNk9xqgsx8BraWXy1KZ",
            "type": "tool_call"
          },
          {
            "name": "get_specialties",
            "args": {},
            "id": "call_5grh3vK6RWcay7RNsRIrgZln",
            "type": "tool_call"
          }
        ],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 15451,
          "output_tokens": 40,
          "total_tokens": 15491,
          "input_token_details": {
            "audio": 0,
            "cache_read": 6016
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "{\"data\": [{\"id\": 306, \"name\": \"Sedacion Conciente\", \"duration_minutes\": null}, {\"id\": 182, \"name\": \"Consultas Ortodoncia\", \"duration_minutes\": null}, {\"id\": 180, \"name\": \"Radiografías en tratamientos de endodoncia\", \"duration_minutes\": null}, {\"id\": 179, \"name\": \"Protección pulpar\", \"duration_minutes\": null}, {\"id\": 178, \"name\": \"Aplicación de cariostatico en dientes de leche por consulta\", \"duration_minutes\": null}, {\"id\": 177, \"name\": \"Aplicación tópica de ﬂúor\", \"duration_minutes\": null}, {\"id\": 176, \"name\": \"Aplicación de revelador de placa bacteriana 2 x año\", \"duration_minutes\": null}, {\"id\": 175, \"name\": \"Profilaxis dental 2 x año\", \"duration_minutes\": null}, {\"id\": 174, \"name\": \"Limpieza de toda la cavidad bucal (eliminación de la placa bacteriana)\", \"duration_minutes\": null}, {\"id\": 173, \"name\": \"Consulta de emergencias\", \"duration_minutes\": null}, {\"id\": 172, \"name\": \"RX periapical en trat. endodóntico\", \"duration_minutes\": null}, {\"id\": 171, \"name\": \"Tomografía articulación temporo maxilar\", \"duration_minutes\": null}, {\"id\": 170, \"name\": \"Tomografía maxilo facial completa\", \"duration_minutes\": null}, {\"id\": 169, \"name\": \"Tomografía maxilo facial de un cuadrante\", \"duration_minutes\": null}, {\"id\": 168, \"name\": \"RX lateral de cráneo\", \"duration_minutes\": null}, {\"id\": 167, \"name\": \"RX  PANORÁMICA\", \"duration_minutes\": null}, {\"id\": 166, \"name\": \"Restauraciones estéticas compuestas (por cambio de amalgama)\", \"duration_minutes\": null}, {\"id\": 165, \"name\": \"Restauraciones estéticas simples (por cambio de amalgama)\", \"duration_minutes\": null}, {\"id\": 164, \"name\": \"Pulido de resina P/ Brakets\", \"duration_minutes\": null}, {\"id\": 163, \"name\": \"Placa complementaria en ortodoncias\", \"duration_minutes\": null}, {\"id\": 162, \"name\": \"Placas de Contención\", \"duration_minutes\": null}, {\"id\": 161, \"name\": \"Estudio cefalométrico\", \"duration_minutes\": null}, {\"id\": 160, \"name\": \"Ortodoncia (casos graves malformaciones c-1, c-2, c-3)\", \"duration_minutes\": null}, {\"id\": 159, \"name\": \"Ortodoncia (casos moderados   malformaciones c-1, c-2, c-3)\", \"duration_minutes\": null}, {\"id\": 158, \"name\": \"Ortopedia (casos graves de malformaciones c-1, c-2, c-3)\", \"duration_minutes\": null}, {\"id\": 157, \"name\": \"Ortopedia (casos moderados de malformaciones c-1, c-2, c-3)\", \"duration_minutes\": null}, {\"id\": 156, \"name\": \"Levantamiento de Seno Maxilar sin Injerto\", \"duration_minutes\": null}, {\"id\": 155, \"name\": \"Regeneración Ósea con Malla de Titanio o Teflón\", \"duration_minutes\": null}, {\"id\": 154, \"name\": \"Prótesis hibrida con 6 Implantes\", \"duration_minutes\": null}, {\"id\": 153, \"name\": \"Prótesis hibrida con 5 Implantes\", \"duration_minutes\": null}, {\"id\": 152, \"name\": \"Prótesis hibrida con 4 Implantes\", \"duration_minutes\": null}, {\"id\": 151, \"name\": \"Prótesis sobre implante (por elemento)\", \"duration_minutes\": null}, {\"id\": 150, \"name\": \"Cirugía pre-protésica (por elemento)\", \"duration_minutes\": null}, {\"id\": 149, \"name\": \"Cirugía de colocación de implante (por elemento)\", \"duration_minutes\": null}, {\"id\": 148, \"name\": \"Injerto óseo (hasta 3 piezas)\", \"duration_minutes\": null}, {\"id\": 147, \"name\": \"Injerto óseo  (1 pieza)\", \"duration_minutes\": null}, {\"id\": 146, \"name\": \"Exodoncia pre-implante  (PM - M)\", \"duration_minutes\": null}, {\"id\": 145, \"name\": \"Exodoncia pre-implante anteriores\", \"duration_minutes\": null}, {\"id\": 144, \"name\": \"Consulta, diagnóstico y ﬁcha odontograﬁca\", \"duration_minutes\": null}, {\"id\": 143, \"name\": \"Bichectomía\", \"duration_minutes\": null}, {\"id\": 142, \"name\": \"Colocación de botón quirúrgico\", \"duration_minutes\": null}, {\"id\": 141, \"name\": \"Mucocele con láser\", \"duration_minutes\": null}, {\"id\": 139, \"name\": \"L-PRF (Plasma)\", \"duration_minutes\": null}, {\"id\": 138, \"name\": \"Regularización ósea por exodoncia múltiple por arcada\", \"duration_minutes\": null}, {\"id\": 137, \"name\": \"Cirugía de piezas supernumerarios Incluida\", \"duration_minutes\": null}, {\"id\": 136, \"name\": \"Cirugía de piezas supernumerarios semi-Inclusos\", \"duration_minutes\": null}, {\"id\": 135, \"name\": \"Apicectomía (por elemento; remanente de raíz)\", \"duration_minutes\": null}, {\"id\": 134, \"name\": \"Ostectomía (corte de hueso)\", \"duration_minutes\": null}, {\"id\": 133, \"name\": \"Odontoseccion (por pieza dentaria)\", \"duration_minutes\": null}, {\"id\": 132, \"name\": \"Exodoncia con alveolectomía\", \"duration_minutes\": null}, {\"id\": 131, \"name\": \"Cirugía 3ros. molares inclusos o el caninos retenidos\", \"duration_minutes\": null}, {\"id\": 130, \"name\": \"Cirugía 3ros. molares semi-Inclusos\", \"duration_minutes\": null}, {\"id\": 129, \"name\": \"Cirugía 3ros. molares erupcionados\", \"duration_minutes\": null}, {\"id\": 128, \"name\": \"Extracciones dentarias\", \"duration_minutes\": null}, {\"id\": 127, \"name\": \"Cromo flex\", \"duration_minutes\": null}, {\"id\": 126, \"name\": \"Rebasado de prótesis totales en consultorio\", \"duration_minutes\": null}, {\"id\": 125, \"name\": \"Rebasado de prótesis totales (laboratorio)\", \"duration_minutes\": null}, {\"id\": 124, \"name\": \"Reparación de placas de cromo-cobalto (laboratorio acrílico)\", \"duration_minutes\": null}, {\"id\": 123, \"name\": \"Reparación de placas de acrílico (laboratorio)\", \"duration_minutes\": null}, {\"id\": 122, \"name\": \"Prótesis acrílico parciales de una a dos piezas\", \"duration_minutes\": null}, {\"id\": 121, \"name\": \"Prótesis ﬂexible estética de 6 a más dientes\", \"duration_minutes\": null}, {\"id\": 120, \"name\": \"Prótesis ﬂexible estética de 2 a 5 dientes\", \"duration_minutes\": null}, {\"id\": 119, \"name\": \"Prótesis ﬂexible estética un diente\", \"duration_minutes\": null}, {\"id\": 118, \"name\": \"Prótesis esquelética cromo-cobalto (con dientes ivoclar)\", \"duration_minutes\": null}, {\"id\": 117, \"name\": \"Prótesis esquelética cromo-cobalto (con dientes normales)\", \"duration_minutes\": null}, {\"id\": 116, \"name\": \"Prótesis total superior inferior (acrílico) dientes ivoclar\", \"duration_minutes\": null}, {\"id\": 115, \"name\": \"Prótesis total superior o inferior (acrílico) dientes normales\", \"duration_minutes\": null}, {\"id\": 114, \"name\": \"Prótesis parcial removible (acrílico) (Ac lucitone porcelana ivoclar)\", \"duration_minutes\": null}, {\"id\": 113, \"name\": \"Prótesis parcial removible (acrílico) dientes normales\", \"duration_minutes\": null}, {\"id\": 112, \"name\": \"Coronas provisionales (por pieza)\", \"duration_minutes\": null}, {\"id\": 111, \"name\": \"Endocorona de resina\", \"duration_minutes\": null}, {\"id\": 110, \"name\": \"Retiro de perno\", \"duration_minutes\": null}, {\"id\": 109, \"name\": \"Cementado de coronas con RL. U200\", \"duration_minutes\": null}, {\"id\": 108, \"name\": \"Coronas 3/4\", \"duration_minutes\": null}, {\"id\": 107, \"name\": \"Incrustaciones porcelana empress\", \"duration_minutes\": null}, {\"id\": 106, \"name\": \"Incrustaciones\", \"duration_minutes\": null}, {\"id\": 105, \"name\": \"Puente metal porcelana (por elemento) design\", \"duration_minutes\": null}, {\"id\": 104, \"name\": \"Puente metal cerámico (por elemento) empress II (hombro cerámico)\", \"duration_minutes\": null}, {\"id\": 103, \"name\": \"Carilla de porcelana\", \"duration_minutes\": null}, {\"id\": 101, \"name\": \"Carilla de ivocron\", \"duration_minutes\": null}, {\"id\": 100, \"name\": \"Carilla de resina - Laboratorio\", \"duration_minutes\": null}, {\"id\": 99, \"name\": \"Corona metal cerámica - porcelana\", \"duration_minutes\": null}, {\"id\": 98, \"name\": \"Corona Metálica Total o Completa (Remanium)\", \"duration_minutes\": null}, {\"id\": 97, \"name\": \"Corona veener metal porcelana-cerámica\", \"duration_minutes\": null}, {\"id\": 96, \"name\": \"Corona Veneer Metal\", \"duration_minutes\": null}, {\"id\": 95, \"name\": \"Corona de zirconio\", \"duration_minutes\": null}, {\"id\": 94, \"name\": \"Corona Porcelana Pura\", \"duration_minutes\": null}, {\"id\": 93, \"name\": \"Corona Jacket de Ivocron\", \"duration_minutes\": null}, {\"id\": 92, \"name\": \"Perno muñón estético blanco\", \"duration_minutes\": null}, {\"id\": 91, \"name\": \"Perno Muñón Multirradicular\", \"duration_minutes\": null}, {\"id\": 90, \"name\": \"Perno muñón unirradicular\", \"duration_minutes\": null}, {\"id\": 89, \"name\": \"Injerto de tejido conectivo 1 a 3 piezas\", \"duration_minutes\": null}, {\"id\": 88, \"name\": \"Osteoplastia\", \"duration_minutes\": null}, {\"id\": 87, \"name\": \"Tratamiento no quirúrgico periodontal por sextante\", \"duration_minutes\": null}, {\"id\": 86, \"name\": \"Despigmentación melanina por arcada con láser\", \"duration_minutes\": null}, {\"id\": 85, \"name\": \"Frenectomía normal con láser\", \"duration_minutes\": null}, {\"id\": 84, \"name\": \"Frenectomía  compuesta\", \"duration_minutes\": null}, {\"id\": 83, \"name\": \"Frenectomía normal\", \"duration_minutes\": null}, {\"id\": 82, \"name\": \"Raspado y Alisado Radicular x seción\", \"duration_minutes\": null}, {\"id\": 81, \"name\": \"Placa miorrelajante de acrilico II\", \"duration_minutes\": null}, {\"id\": 80, \"name\": \"Placa de mordida miorrelajante rígida\", \"duration_minutes\": null}, {\"id\": 79, \"name\": \"Placa de mordida miorrelajante ﬂexible\", \"duration_minutes\": null}, {\"id\": 77, \"name\": \"Ajuste oclusal\", \"duration_minutes\": null}, {\"id\": 76, \"name\": \"Ferulización/inmovilización  con resina fotopolimerizable (por pieza)\", \"duration_minutes\": null}, {\"id\": 75, \"name\": \"Ulectomia (corte de capuchón para erupción dentaria) 1pza/año\", \"duration_minutes\": null}, {\"id\": 74, \"name\": \"Gingivoplastía 1Pza/año\", \"duration_minutes\": null}, {\"id\": 73, \"name\": \"Gingivectomia 1 Pza/año\", \"duration_minutes\": null}, {\"id\": 72, \"name\": \"Retratamiento multiradicular\", \"duration_minutes\": null}, {\"id\": 71, \"name\": \"Retratamiento en biradiculares\", \"duration_minutes\": null}, {\"id\": 70, \"name\": \"Retratamientos endodonticos uniradiculares\", \"duration_minutes\": null}, {\"id\": 69, \"name\": \"Por inasistencia prolongada  (más de tres semanas a partir de la última cita)\", \"duration_minutes\": null}, {\"id\": 68, \"name\": \"Proceso periapical (infección crónica)\", \"duration_minutes\": null}, {\"id\": 67, \"name\": \"Inducción a la apicoformación\", \"duration_minutes\": null}, {\"id\": 66, \"name\": \"Tratamientos endodonticos en piezas calciﬁcadas\", \"duration_minutes\": null}, {\"id\": 65, \"name\": \"Radiografías adicionales en tratamientos endodonticos\", \"duration_minutes\": null}, {\"id\": 64, \"name\": \"Pulpotomía cameral  (en caso de caries avanzadas)\", \"duration_minutes\": null}, {\"id\": 63, \"name\": \"Tratamiento de conducto en molares (superiores e inferiores)\", \"duration_minutes\": null}, {\"id\": 62, \"name\": \"Tratamiento de conducto en premolares (superiores e inferiores)\", \"duration_minutes\": null}, {\"id\": 61, \"name\": \"Tratamiento de conducto en piezas anteriores (incisivos y caninos)\", \"duration_minutes\": null}, {\"id\": 60, \"name\": \"Radiografías panorámica\", \"duration_minutes\": null}, {\"id\": 59, \"name\": \"Radiografías periapical en tratamientos endodocicos\", \"duration_minutes\": null}, {\"id\": 58, \"name\": \"Mantenimiento de carilla\", \"duration_minutes\": null}, {\"id\": 57, \"name\": \"Diseño de sonrisa Digital\", \"duration_minutes\": null}, {\"id\": 56, \"name\": \"Diseño de sonrisa (Mockup)\", \"duration_minutes\": null}, {\"id\": 55, \"name\": \"Bordes incisales clase IV alargamiento de corona\", \"duration_minutes\": null}, {\"id\": 54, \"name\": \"Carillas lentes de contacto\", \"duration_minutes\": null}, {\"id\": 53, \"name\": \"Carillas de resinas\", \"duration_minutes\": null}, {\"id\": 52, \"name\": \"Carillas de porcelana\", \"duration_minutes\": null}, {\"id\": 51, \"name\": \"Cierre de diastemas\", \"duration_minutes\": null}, {\"id\": 50, \"name\": \"1 sesión de blanqueamiento total\", \"duration_minutes\": null}, {\"id\": 49, \"name\": \"Blanqueamiento ambulatorio  (cubetas)\", \"duration_minutes\": null}, {\"id\": 48, \"name\": \"Blanqueamiento inmediato con láser\", \"duration_minutes\": null}, {\"id\": 47, \"name\": \"Blanqueamiento interno (por pieza dentaria)\", \"duration_minutes\": null}, {\"id\": 46, \"name\": \"Pegado de botón quirúrgico\", \"duration_minutes\": null}, {\"id\": 45, \"name\": \"Refuerzo con  perno de ﬁbra de vidrio\", \"duration_minutes\": null}, {\"id\": 44, \"name\": \"Refuerzo con pin\", \"duration_minutes\": null}, {\"id\": 43, \"name\": \"Carillas de resina fotopolimerizable\", \"duration_minutes\": null}, {\"id\": 42, \"name\": \"Corona de resina fotopolimerizable\", \"duration_minutes\": null}, {\"id\": 41, \"name\": \"Obturación  3º grado resina fotopolimerizable\", \"duration_minutes\": null}, {\"id\": 40, \"name\": \"Obturación  1º grado resina fotopolimerizable\", \"duration_minutes\": null}, {\"id\": 39, \"name\": \"Restauración con Ionomero de vidrio\", \"duration_minutes\": null}, {\"id\": 38, \"name\": \"Obturación  2º grado resina fotopolimerizable\", \"duration_minutes\": null}, {\"id\": 34, \"name\": \"Sellantes en dentadura permanente\", \"duration_minutes\": null}, {\"id\": 33, \"name\": \"Radiograﬁa panorámica digital\", \"duration_minutes\": null}, {\"id\": 32, \"name\": \"Barnis de Fluor\", \"duration_minutes\": null}, {\"id\": 31, \"name\": \"Pulpectomía\", \"duration_minutes\": null}, {\"id\": 30, \"name\": \"Pulpotomía\", \"duration_minutes\": null}, {\"id\": 29, \"name\": \"Extracción compleja\", \"duration_minutes\": null}, {\"id\": 28, \"name\": \"Extracción simple (sin anestesia)\", \"duration_minutes\": null}, {\"id\": 27, \"name\": \"Cariostaticos en dientes deciduos por pieza\", \"duration_minutes\": null}, {\"id\": 26, \"name\": \"Sellantes\", \"duration_minutes\": null}, {\"id\": 25, \"name\": \"Restauraciones compuestas\", \"duration_minutes\": null}, {\"id\": 24, \"name\": \"Restauración de resina, fotopolemirezable de una superﬁcie\", \"duration_minutes\": null}, {\"id\": 23, \"name\": \"Obturación con Ionomeros\", \"duration_minutes\": null}, {\"id\": 22, \"name\": \"Atención pos natal odontológico\", \"duration_minutes\": null}, {\"id\": 21, \"name\": \"Atención prenatal odontológico\", \"duration_minutes\": null}, {\"id\": 20, \"name\": \"Radiografía panorámica digital\", \"duration_minutes\": null}, {\"id\": 19, \"name\": \"Corona provisoria sin perno\", \"duration_minutes\": null}, {\"id\": 18, \"name\": \"Retiro de mantenedores de espacio\", \"duration_minutes\": null}, {\"id\": 17, \"name\": \"Retiro de puentes\", \"duration_minutes\": null}, {\"id\": 16, \"name\": \"Retiro de coronas y 3/4 Metálica\", \"duration_minutes\": null}, {\"id\": 15, \"name\": \"Cementado de coronas Jackets con cemento fosfato\", \"duration_minutes\": null}, {\"id\": 14, \"name\": \"Técnica de cepillado dental - clínico\", \"duration_minutes\": null}, {\"id\": 13, \"name\": \"Extracción dental en niños\", \"duration_minutes\": null}, {\"id\": 12, \"name\": \"Drenaje de absceso pulpar\", \"duration_minutes\": null}, {\"id\": 11, \"name\": \"Radiografía periapical\", \"duration_minutes\": null}, {\"id\": 10, \"name\": \"Tratamiento con láser en cicatrización postcirugia\", \"duration_minutes\": null}, {\"id\": 9, \"name\": \"Tratamiento con láser para aftas en cavidad bucal\", \"duration_minutes\": null}, {\"id\": 8, \"name\": \"Tratamiento con láser de inflamación de encías\", \"duration_minutes\": null}]}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "get_services",
        "id": "d4d5ec3a-9f8d-4ded-a192-778ad9194270",
        "tool_call_id": "call_W8w1yHNk9xqgsx8BraWXy1KZ",
        "status": "success"
      },
      {
        "content": "{\"data\": [{\"id\": 2, \"name\": \"Endodoncia\", \"slug\": \"endodoncia\", \"created_at\": \"2026-01-27T12:13:08.000000Z\", \"updated_at\": \"2026-07-07T20:46:28.000000Z\", \"description\": \"<p>Especialidad dedicada al tratamiento del interior del diente cuando la pulpa est&aacute; inflamada o infectada. Realiza tratamientos de conducto para aliviar el dolor, eliminar la infecci&oacute;n y conservar el diente.</p>\"}, {\"id\": 3, \"name\": \"Odontopediatria\", \"slug\": \"odontopediatria\", \"created_at\": \"2026-01-28T10:46:36.000000Z\", \"updated_at\": \"2026-03-18T07:20:25.000000Z\", \"description\": \"<p>La odontopediatr&iacute;a enfocada en la atenci&oacute;n integral de beb&eacute;s, ni&ntilde;os y adolescentes, encargada de prevenir, diagnosticar y tratar problemas dentales desde las primeras etapas de crecimiento.</p>\"}, {\"id\": 6, \"name\": \"Ortodoncia\", \"slug\": \"ortodoncia\", \"created_at\": \"2026-03-18T06:51:35.000000Z\", \"updated_at\": \"2026-03-18T07:19:56.000000Z\", \"description\": \"<p>La ortodoncia encargada de diagnosticar, prevenir y corregir la posici&oacute;n de los dientes y la mordida.</p>\"}, {\"id\": 9, \"name\": \"Protesista\", \"slug\": \"protesista\", \"created_at\": \"2026-03-18T07:14:03.000000Z\", \"updated_at\": \"2026-03-18T07:14:03.000000Z\", \"description\": \"<p>La protesista dental se encarga de la rehabilitaci&oacute;n y restauraci&oacute;n de dientes perdidos o da&ntilde;ados mediante pr&oacute;tesis dentales, ya sean fijas o removibles.</p>\"}, {\"id\": 10, \"name\": \"Periodontologia\", \"slug\": \"periodontologia\", \"created_at\": \"2026-03-18T07:14:44.000000Z\", \"updated_at\": \"2026-07-07T20:57:47.000000Z\", \"description\": \"<p>Especialidad dedicada a la prevenci&oacute;n, diagn&oacute;stico y tratamiento de las enfermedades de las enc&iacute;as y los tejidos que sostienen los dientes, como gingivitis, periodontitis, sangrado, inflamaci&oacute;n y movilidad dental.</p>\"}, {\"id\": 11, \"name\": \"Estética Dental\", \"slug\": \"estetica-dental\", \"created_at\": \"2026-03-18T07:15:42.000000Z\", \"updated_at\": \"2026-07-07T20:54:58.000000Z\", \"description\": \"<p>Especialidad enfocada en mejorar la apariencia de la sonrisa mediante tratamientos como blanqueamiento dental, dise&ntilde;o de sonrisa, carillas, contorneado dental y otros procedimientos est&eacute;ticos.</p>\"}, {\"id\": 12, \"name\": \"Sedacion\", \"slug\": \"sedacion\", \"created_at\": \"2026-03-18T07:16:22.000000Z\", \"updated_at\": \"2026-04-09T15:43:43.000000Z\", \"description\": \"<p>La sedaci&oacute;n consciente que permite al paciente mantenerse relajado y tranquilo durante los procedimientos, sin perder la capacidad de responder a est&iacute;mulos o indicaciones del profesional.</p>\"}, {\"id\": 13, \"name\": \"Ortopedia\", \"slug\": \"ortopedia\", \"created_at\": \"2026-03-18T07:17:35.000000Z\", \"updated_at\": \"2026-07-07T20:49:36.000000Z\", \"description\": \"<p>La ortopedia dental enfocada en guiar el crecimiento y desarrollo de los maxilares, principalmente en ni&ntilde;os y adolescentes, para corregir alteraciones en la mordida y favorecer una adecuada alineaci&oacute;n dental mediante aparatos ortop&eacute;dicos.</p>\"}, {\"id\": 14, \"name\": \"Implantologia\", \"slug\": \"implantologia\", \"created_at\": \"2026-03-18T07:18:27.000000Z\", \"updated_at\": \"2026-03-18T07:18:27.000000Z\", \"description\": \"<p>La implantolog&iacute;a encargada de reemplazar piezas dentales perdidas mediante la colocaci&oacute;n de implantes dentales, que act&uacute;an como ra&iacute;ces artificiales sobre las cuales se fijan pr&oacute;tesis.</p>\"}, {\"id\": 15, \"name\": \"Rehabilitacion\", \"slug\": \"rehabilitacion\", \"created_at\": \"2026-03-18T07:19:23.000000Z\", \"updated_at\": \"2026-04-09T15:50:08.000000Z\", \"description\": \"<p>La rehabilitaci&oacute;n oral encargada de restaurar la funci&oacute;n y est&eacute;tica de la boca mediante la recuperaci&oacute;n integral de los dientes y estructuras afectadas.</p>\"}, {\"id\": 20, \"name\": \"Odontología General\", \"slug\": \"odontologia-general\", \"created_at\": \"2026-04-07T15:48:12.000000Z\", \"updated_at\": \"2026-07-07T20:43:20.000000Z\", \"description\": \"<p>Atenci&oacute;n integral para el diagn&oacute;stico, prevenci&oacute;n y tratamiento de los problemas dentales m&aacute;s comunes. Incluye revisi&oacute;n dental, limpieza, tratamiento de caries, restauraciones (empastes), dolor de dientes, sensibilidad dental y evaluaci&oacute;n para derivar a otras especialidades si es necesario.&nbsp;</p>\"}, {\"id\": 24, \"name\": \"Radiologo\", \"slug\": \"radiologo\", \"created_at\": \"2026-04-09T15:41:43.000000Z\", \"updated_at\": \"2026-07-07T20:41:56.000000Z\", \"description\": \"<p>Especialidad encargada de realizar estudios de imagen para el diagn&oacute;stico y planificaci&oacute;n de tratamientos odontol&oacute;gicos, como radiograf&iacute;as panor&aacute;micas, periapicales y tomograf&iacute;as dentales.</p>\"}, {\"id\": 26, \"name\": \"Cirugia Dental\", \"slug\": \"cirugia-dental\", \"created_at\": \"2026-04-15T21:10:13.000000Z\", \"updated_at\": \"2026-07-07T20:40:51.000000Z\", \"description\": \"<p>Especialidad encargada de la extracci&oacute;n de dientes y muelas, especialmente muelas del juicio, as&iacute; como del tratamiento de infecciones, quistes, dientes retenidos y otros procedimientos quir&uacute;rgicos de la boca.</p>\"}, {\"id\": 242, \"name\": \"General\", \"slug\": \"general\", \"created_at\": \"2026-07-07T21:01:38.000000Z\", \"updated_at\": \"2026-07-07T21:01:38.000000Z\", \"description\": null}]}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "get_specialties",
        "id": "1af90e73-7edb-4f79-893a-d9da3884a0ae",
        "tool_call_id": "call_5grh3vK6RWcay7RNsRIrgZln",
        "status": "success"
      },
      {
        "content": "",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 11,
            "prompt_tokens": 21701,
            "total_tokens": 21712,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 15360
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPWIPlVUSI71LVJ3eFBXMWmIHPSZ",
          "service_tier": "default",
          "finish_reason": "tool_calls",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--69b1e62a-38e6-4798-86d9-e9d1543b439a-0",
        "tool_calls": [
          {
            "name": "get_doctors",
            "args": {},
            "id": "call_7LDmfduo3YBkoM0TfJgUO7eU",
            "type": "tool_call"
          }
        ],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 21701,
          "output_tokens": 11,
          "total_tokens": 21712,
          "input_token_details": {
            "audio": 0,
            "cache_read": 15360
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "{\"data\": [{\"id\": 4, \"name\": \"Jessica Fabiola Montero Murillo\", \"status\": true, \"has_availability\": true, \"minimum_patient_age\": \"0\", \"maximum_patient_age\": \"99\", \"specialties\": [{\"id\": 3, \"name\": \"Odontopediatria\"}, {\"id\": 12, \"name\": \"Sedacion\"}, {\"id\": 20, \"name\": \"Odontología General\"}, {\"id\": 242, \"name\": \"General\"}]}, {\"id\": 5, \"name\": \"Ricardo Mercado Berbeja\", \"status\": true, \"has_availability\": true, \"minimum_patient_age\": \"0\", \"maximum_patient_age\": \"99\", \"specialties\": [{\"id\": 2, \"name\": \"Endodoncia\"}, {\"id\": 20, \"name\": \"Odontología General\"}]}, {\"id\": 7, \"name\": \"Paola Carla Heresi Guzman\", \"status\": true, \"has_availability\": true, \"minimum_patient_age\": \"0\", \"maximum_patient_age\": \"99\", \"specialties\": [{\"id\": 6, \"name\": \"Ortodoncia\"}, {\"id\": 13, \"name\": \"Ortopedia\"}, {\"id\": 20, \"name\": \"Odontología General\"}, {\"id\": 242, \"name\": \"General\"}]}, {\"id\": 9, \"name\": \"Martha Galarza Cueto\", \"status\": true, \"has_availability\": true, \"minimum_patient_age\": \"0\", \"maximum_patient_age\": \"99\", \"specialties\": [{\"id\": 3, \"name\": \"Odontopediatria\"}, {\"id\": 12, \"name\": \"Sedacion\"}, {\"id\": 13, \"name\": \"Ortopedia\"}, {\"id\": 20, \"name\": \"Odontología General\"}, {\"id\": 242, \"name\": \"General\"}]}, {\"id\": 10, \"name\": \"Carolina Rojas Godoy\", \"status\": true, \"has_availability\": true, \"minimum_patient_age\": \"0\", \"maximum_patient_age\": \"99\", \"specialties\": [{\"id\": 3, \"name\": \"Odontopediatria\"}, {\"id\": 12, \"name\": \"Sedacion\"}, {\"id\": 20, \"name\": \"Odontología General\"}, {\"id\": 242, \"name\": \"General\"}]}, {\"id\": 11, \"name\": \"Sonia Roca Ichazo\", \"status\": true, \"has_availability\": true, \"minimum_patient_age\": \"12\", \"maximum_patient_age\": \"14\", \"specialties\": [{\"id\": 6, \"name\": \"Ortodoncia\"}, {\"id\": 13, \"name\": \"Ortopedia\"}, {\"id\": 20, \"name\": \"Odontología General\"}, {\"id\": 242, \"name\": \"General\"}]}, {\"id\": 12, \"name\": \"Adriana Soria\", \"status\": true, \"has_availability\": true, \"minimum_patient_age\": \"0\", \"maximum_patient_age\": \"99\", \"specialties\": [{\"id\": 6, \"name\": \"Ortodoncia\"}, {\"id\": 13, \"name\": \"Ortopedia\"}, {\"id\": 20, \"name\": \"Odontología General\"}, {\"id\": 242, \"name\": \"General\"}]}, {\"id\": 14, \"name\": \"Zdenka Aranibar\", \"status\": true, \"has_availability\": true, \"minimum_patient_age\": \"0\", \"maximum_patient_age\": \"99\", \"specialties\": [{\"id\": 2, \"name\": \"Endodoncia\"}, {\"id\": 20, \"name\": \"Odontología General\"}, {\"id\": 242, \"name\": \"General\"}]}, {\"id\": 15, \"name\": \"Slibka Mariela Camacho Cuellar\", \"status\": true, \"has_availability\": true, \"minimum_patient_age\": \"12\", \"maximum_patient_age\": \"99\", \"specialties\": [{\"id\": 2, \"name\": \"Endodoncia\"}, {\"id\": 15, \"name\": \"Rehabilitacion\"}, {\"id\": 20, \"name\": \"Odontología General\"}, {\"id\": 242, \"name\": \"General\"}]}, {\"id\": 16, \"name\": \"Gabriela Jarandilla\", \"status\": true, \"has_availability\": true, \"minimum_patient_age\": \"0\", \"maximum_patient_age\": \"99\", \"specialties\": [{\"id\": 2, \"name\": \"Endodoncia\"}, {\"id\": 20, \"name\": \"Odontología General\"}, {\"id\": 242, \"name\": \"General\"}]}, {\"id\": 18, \"name\": \"David Jose Sensano Suárez\", \"status\": true, \"has_availability\": true, \"minimum_patient_age\": \"12\", \"maximum_patient_age\": \"99\", \"specialties\": [{\"id\": 9, \"name\": \"Protesista\"}, {\"id\": 15, \"name\": \"Rehabilitacion\"}, {\"id\": 20, \"name\": \"Odontología General\"}]}, {\"id\": 19, \"name\": \"Liliana Sandoval\", \"status\": true, \"has_availability\": true, \"minimum_patient_age\": \"0\", \"maximum_patient_age\": \"99\", \"specialties\": [{\"id\": 10, \"name\": \"Periodontologia\"}, {\"id\": 20, \"name\": \"Odontología General\"}]}, {\"id\": 20, \"name\": \"Jacqueline Micaela Argote Cusi\", \"status\": true, \"has_availability\": true, \"minimum_patient_age\": \"0\", \"maximum_patient_age\": \"99\", \"specialties\": [{\"id\": 14, \"name\": \"Implantologia\"}, {\"id\": 20, \"name\": \"Odontología General\"}, {\"id\": 242, \"name\": \"General\"}]}, {\"id\": 21, \"name\": \"Eloy Arnildo Montero Castedo\", \"status\": true, \"has_availability\": true, \"minimum_patient_age\": \"12\", \"maximum_patient_age\": \"99\", \"specialties\": [{\"id\": 14, \"name\": \"Implantologia\"}, {\"id\": 20, \"name\": \"Odontología General\"}, {\"id\": 242, \"name\": \"General\"}]}, {\"id\": 22, \"name\": \"Susana Urrusti\", \"status\": true, \"has_availability\": true, \"minimum_patient_age\": \"12\", \"maximum_patient_age\": \"99\", \"specialties\": [{\"id\": 10, \"name\": \"Periodontologia\"}, {\"id\": 20, \"name\": \"Odontología General\"}]}]}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "get_doctors",
        "id": "05f2b78b-fe4d-4b92-aed6-9e1f3f8067c3",
        "tool_call_id": "call_7LDmfduo3YBkoM0TfJgUO7eU",
        "status": "success"
      },
      {
        "content": "",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 23,
            "prompt_tokens": 23181,
            "total_tokens": 23204,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 21632
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPWLlltA2LaK8S4TyszXufdnFzgt",
          "service_tier": "default",
          "finish_reason": "tool_calls",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--09ef0f09-ec97-4171-81e2-267164f6db47-0",
        "tool_calls": [
          {
            "name": "get_doctor_schedule",
            "args": {
              "id_doctor": 7,
              "duration_minutes": 60
            },
            "id": "call_Faf3YMldbpTPVxaha4YSEL9J",
            "type": "tool_call"
          }
        ],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 23181,
          "output_tokens": 23,
          "total_tokens": 23204,
          "input_token_details": {
            "audio": 0,
            "cache_read": 21632
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "{\"error\": \"\", \"slots\": []}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "get_doctor_schedule",
        "id": "6cfae02a-10ec-4960-9d02-1d00daa7d97b",
        "tool_call_id": "call_Faf3YMldbpTPVxaha4YSEL9J",
        "status": "success"
      },
      {
        "content": "",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 23,
            "prompt_tokens": 23223,
            "total_tokens": 23246,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 23168
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPWccrrR3bqTkZnhAqYvt4iXClyi",
          "service_tier": "default",
          "finish_reason": "tool_calls",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--b8241271-f9b2-44a3-a2a1-08bfef7d16ec-0",
        "tool_calls": [
          {
            "name": "get_doctor_schedule",
            "args": {
              "id_doctor": 12,
              "duration_minutes": 60
            },
            "id": "call_e2mLOuU0az9wDHbkr9FxiNwt",
            "type": "tool_call"
          }
        ],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 23223,
          "output_tokens": 23,
          "total_tokens": 23246,
          "input_token_details": {
            "audio": 0,
            "cache_read": 23168
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "{\"error\": \"\", \"slots\": []}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "get_doctor_schedule",
        "id": "32868cbe-8cce-4d97-b057-8dd8e89fa481",
        "tool_call_id": "call_e2mLOuU0az9wDHbkr9FxiNwt",
        "status": "success"
      },
      {
        "content": "",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 23,
            "prompt_tokens": 23265,
            "total_tokens": 23288,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 0
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPWtUpjMV5CTTbts6830GJJFcHrc",
          "service_tier": "default",
          "finish_reason": "tool_calls",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--afee82dd-a788-49e4-ab44-206cbb312b29-0",
        "tool_calls": [
          {
            "name": "get_doctor_schedule",
            "args": {
              "id_doctor": 11,
              "duration_minutes": 60
            },
            "id": "call_DTRMz2p6Ri0SfHnKT0WqZBnR",
            "type": "tool_call"
          }
        ],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 23265,
          "output_tokens": 23,
          "total_tokens": 23288,
          "input_token_details": {
            "audio": 0,
            "cache_read": 0
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "{\"error\": \"\", \"slots\": []}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "get_doctor_schedule",
        "id": "e9272508-45b0-4f6a-9a3d-edc3ec7cffe0",
        "tool_call_id": "call_DTRMz2p6Ri0SfHnKT0WqZBnR",
        "status": "success"
      },
      {
        "content": "",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 23,
            "prompt_tokens": 23307,
            "total_tokens": 23330,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 23168
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPXBAX7I3Rc3l20oraeUY9hk7CDG",
          "service_tier": "default",
          "finish_reason": "tool_calls",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--5bf4961a-c13d-4c0f-b7aa-886fecc4275a-0",
        "tool_calls": [
          {
            "name": "get_doctor_schedule",
            "args": {
              "id_doctor": 5,
              "duration_minutes": 60
            },
            "id": "call_4prYIiCWwiDixikrwaZOtaAL",
            "type": "tool_call"
          }
        ],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 23307,
          "output_tokens": 23,
          "total_tokens": 23330,
          "input_token_details": {
            "audio": 0,
            "cache_read": 23168
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "{\"doctor_id\": 5, \"schedule\": [], \"days_queried\": 30}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "get_doctor_schedule",
        "id": "7870a2ca-b0ef-4d69-8963-18fbadcf32ba",
        "tool_call_id": "call_4prYIiCWwiDixikrwaZOtaAL",
        "status": "success"
      },
      {
        "content": "",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 23,
            "prompt_tokens": 23360,
            "total_tokens": 23383,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 23296
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPXLxjQ1HUchJigbFplLcW6r0Mvx",
          "service_tier": "default",
          "finish_reason": "tool_calls",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--5b638bc0-6fbc-4947-ae58-b47772c5ce0e-0",
        "tool_calls": [
          {
            "name": "get_doctor_schedule",
            "args": {
              "id_doctor": 4,
              "duration_minutes": 60
            },
            "id": "call_NFfXnJvLncWEzk9nJl5qB4hJ",
            "type": "tool_call"
          }
        ],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 23360,
          "output_tokens": 23,
          "total_tokens": 23383,
          "input_token_details": {
            "audio": 0,
            "cache_read": 23296
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "{\"error\": \"\", \"slots\": []}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "get_doctor_schedule",
        "id": "5927a5f8-37f9-456b-bf53-5d3707cb8f7c",
        "tool_call_id": "call_NFfXnJvLncWEzk9nJl5qB4hJ",
        "status": "success"
      },
      {
        "content": "",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 23,
            "prompt_tokens": 23402,
            "total_tokens": 23425,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 23296
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPXbt7FyZolDZ7d32xPqAZWUNfo2",
          "service_tier": "default",
          "finish_reason": "tool_calls",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--2aeab1b1-fcc2-4230-a941-c582f38b79de-0",
        "tool_calls": [
          {
            "name": "get_doctor_schedule",
            "args": {
              "id_doctor": 9,
              "duration_minutes": 60
            },
            "id": "call_QsX1WCuFuWufZ9ZqBePKaQLK",
            "type": "tool_call"
          }
        ],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 23402,
          "output_tokens": 23,
          "total_tokens": 23425,
          "input_token_details": {
            "audio": 0,
            "cache_read": 23296
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "{\"error\": \"\", \"slots\": []}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "get_doctor_schedule",
        "id": "416babbc-bc94-4dc2-827b-31df672a433b",
        "tool_call_id": "call_QsX1WCuFuWufZ9ZqBePKaQLK",
        "status": "success"
      },
      {
        "content": "",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 23,
            "prompt_tokens": 23444,
            "total_tokens": 23467,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 0
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPXxBytgGDDV7Zp3G7RqnKokM3EE",
          "service_tier": "default",
          "finish_reason": "tool_calls",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--206eb2c3-d45d-494f-b630-7f09114faf3f-0",
        "tool_calls": [
          {
            "name": "get_doctor_schedule",
            "args": {
              "id_doctor": 10,
              "duration_minutes": 60
            },
            "id": "call_OHEHn1gE0mq8SWxnT5lhm10r",
            "type": "tool_call"
          }
        ],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 23444,
          "output_tokens": 23,
          "total_tokens": 23467,
          "input_token_details": {
            "audio": 0,
            "cache_read": 0
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "{\"doctor_id\": 10, \"schedule\": [{\"date\": \"2026-07-09\", \"day_label\": \"jueves 09/07\", \"slots\": [{\"start_time\": \"08:00\", \"end_time\": \"09:00\"}, {\"start_time\": \"14:00\", \"end_time\": \"15:00\"}, {\"start_time\": \"15:00\", \"end_time\": \"16:00\"}, {\"start_time\": \"16:00\", \"end_time\": \"17:00\"}, {\"start_time\": \"17:00\", \"end_time\": \"18:00\"}]}, {\"date\": \"2026-07-10\", \"day_label\": \"viernes 10/07\", \"slots\": [{\"start_time\": \"08:00\", \"end_time\": \"09:00\"}, {\"start_time\": \"10:00\", \"end_time\": \"11:00\"}, {\"start_time\": \"11:00\", \"end_time\": \"12:00\"}, {\"start_time\": \"14:00\", \"end_time\": \"15:00\"}, {\"start_time\": \"15:00\", \"end_time\": \"16:00\"}]}, {\"date\": \"2026-07-13\", \"day_label\": \"lunes 13/07\", \"slots\": [{\"start_time\": \"14:30\", \"end_time\": \"15:30\"}, {\"start_time\": \"15:30\", \"end_time\": \"16:30\"}]}, {\"date\": \"2026-07-14\", \"day_label\": \"martes 14/07\", \"slots\": [{\"start_time\": \"08:00\", \"end_time\": \"09:00\"}, {\"start_time\": \"09:00\", \"end_time\": \"10:00\"}, {\"start_time\": \"10:00\", \"end_time\": \"11:00\"}, {\"start_time\": \"14:00\", \"end_time\": \"15:00\"}, {\"start_time\": \"15:00\", \"end_time\": \"16:00\"}, {\"start_time\": \"16:00\", \"end_time\": \"17:00\"}]}, {\"date\": \"2026-07-15\", \"day_label\": \"miércoles 15/07\", \"slots\": [{\"start_time\": \"08:00\", \"end_time\": \"09:00\"}, {\"start_time\": \"09:00\", \"end_time\": \"10:00\"}, {\"start_time\": \"14:00\", \"end_time\": \"15:00\"}, {\"start_time\": \"15:00\", \"end_time\": \"16:00\"}, {\"start_time\": \"16:00\", \"end_time\": \"17:00\"}, {\"start_time\": \"17:00\", \"end_time\": \"18:00\"}]}, {\"date\": \"2026-07-16\", \"day_label\": \"jueves 16/07\", \"slots\": [{\"start_time\": \"08:00\", \"end_time\": \"09:00\"}, {\"start_time\": \"09:00\", \"end_time\": \"10:00\"}, {\"start_time\": \"10:00\", \"end_time\": \"11:00\"}, {\"start_time\": \"11:00\", \"end_time\": \"12:00\"}, {\"start_time\": \"14:00\", \"end_time\": \"15:00\"}, {\"start_time\": \"15:00\", \"end_time\": \"16:00\"}, {\"start_time\": \"16:00\", \"end_time\": \"17:00\"}, {\"start_time\": \"17:00\", \"end_time\": \"18:00\"}]}, {\"date\": \"2026-07-17\", \"day_label\": \"viernes 17/07\", \"slots\": [{\"start_time\": \"08:00\", \"end_time\": \"09:00\"}, {\"start_time\": \"09:00\", \"end_time\": \"10:00\"}, {\"start_time\": \"10:00\", \"end_time\": \"11:00\"}, {\"start_time\": \"11:00\", \"end_time\": \"12:00\"}, {\"start_time\": \"14:00\", \"end_time\": \"15:00\"}, {\"start_time\": \"15:00\", \"end_time\": \"16:00\"}, {\"start_time\": \"16:00\", \"end_time\": \"17:00\"}, {\"start_time\": \"17:00\", \"end_time\": \"18:00\"}]}, {\"date\": \"2026-07-20\", \"day_label\": \"lunes 20/07\", \"slots\": [{\"start_time\": \"14:30\", \"end_time\": \"15:30\"}, {\"start_time\": \"15:30\", \"end_time\": \"16:30\"}, {\"start_time\": \"16:30\", \"end_time\": \"17:30\"}, {\"start_time\": \"17:30\", \"end_time\": \"18:30\"}]}, {\"date\": \"2026-07-21\", \"day_label\": \"martes 21/07\", \"slots\": [{\"start_time\": \"08:00\", \"end_time\": \"09:00\"}, {\"start_time\": \"09:00\", \"end_time\": \"10:00\"}, {\"start_time\": \"10:00\", \"end_time\": \"11:00\"}, {\"start_time\": \"11:00\", \"end_time\": \"12:00\"}, {\"start_time\": \"14:00\", \"end_time\": \"15:00\"}, {\"start_time\": \"15:00\", \"end_time\": \"16:00\"}, {\"start_time\": \"16:00\", \"end_time\": \"17:00\"}, {\"start_time\": \"17:00\", \"end_time\": \"18:00\"}]}, {\"date\": \"2026-07-22\", \"day_label\": \"miércoles 22/07\", \"slots\": [{\"start_time\": \"08:00\", \"end_time\": \"09:00\"}, {\"start_time\": \"09:00\", \"end_time\": \"10:00\"}, {\"start_time\": \"10:00\", \"end_time\": \"11:00\"}, {\"start_time\": \"11:00\", \"end_time\": \"12:00\"}, {\"start_time\": \"14:00\", \"end_time\": \"15:00\"}, {\"start_time\": \"15:00\", \"end_time\": \"16:00\"}, {\"start_time\": \"16:00\", \"end_time\": \"17:00\"}, {\"start_time\": \"17:00\", \"end_time\": \"18:00\"}]}, {\"date\": \"2026-07-23\", \"day_label\": \"jueves 23/07\", \"slots\": [{\"start_time\": \"08:00\", \"end_time\": \"09:00\"}, {\"start_time\": \"09:00\", \"end_time\": \"10:00\"}, {\"start_time\": \"10:00\", \"end_time\": \"11:00\"}, {\"start_time\": \"11:00\", \"end_time\": \"12:00\"}, {\"start_time\": \"14:00\", \"end_time\": \"15:00\"}, {\"start_time\": \"15:00\", \"end_time\": \"16:00\"}, {\"start_time\": \"16:00\", \"end_time\": \"17:00\"}, {\"start_time\": \"17:00\", \"end_time\": \"18:00\"}]}, {\"date\": \"2026-07-24\", \"day_label\": \"viernes 24/07\", \"slots\": [{\"start_time\": \"08:00\", \"end_time\": \"09:00\"}, {\"start_time\": \"09:00\", \"end_time\": \"10:00\"}, {\"start_time\": \"10:00\", \"end_time\": \"11:00\"}, {\"start_time\": \"11:00\", \"end_time\": \"12:00\"}, {\"start_time\": \"14:00\", \"end_time\": \"15:00\"}, {\"start_time\": \"15:00\", \"end_time\": \"16:00\"}, {\"start_time\": \"16:00\", \"end_time\": \"17:00\"}, {\"start_time\": \"17:00\", \"end_time\": \"18:00\"}]}, {\"date\": \"2026-07-27\", \"day_label\": \"lunes 27/07\", \"slots\": [{\"start_time\": \"14:30\", \"end_time\": \"15:30\"}, {\"start_time\": \"15:30\", \"end_time\": \"16:30\"}, {\"start_time\": \"16:30\", \"end_time\": \"17:30\"}, {\"start_time\": \"17:30\", \"end_time\": \"18:30\"}]}, {\"date\": \"2026-07-28\", \"day_label\": \"martes 28/07\", \"slots\": [{\"start_time\": \"08:00\", \"end_time\": \"09:00\"}, {\"start_time\": \"09:00\", \"end_time\": \"10:00\"}, {\"start_time\": \"10:00\", \"end_time\": \"11:00\"}, {\"start_time\": \"11:00\", \"end_time\": \"12:00\"}, {\"start_time\": \"14:00\", \"end_time\": \"15:00\"}, {\"start_time\": \"15:00\", \"end_time\": \"16:00\"}, {\"start_time\": \"16:00\", \"end_time\": \"17:00\"}, {\"start_time\": \"17:00\", \"end_time\": \"18:00\"}]}, {\"date\": \"2026-07-29\", \"day_label\": \"miércoles 29/07\", \"slots\": [{\"start_time\": \"08:00\", \"end_time\": \"09:00\"}, {\"start_time\": \"09:00\", \"end_time\": \"10:00\"}, {\"start_time\": \"10:00\", \"end_time\": \"11:00\"}, {\"start_time\": \"11:00\", \"end_time\": \"12:00\"}, {\"start_time\": \"14:00\", \"end_time\": \"15:00\"}, {\"start_time\": \"15:00\", \"end_time\": \"16:00\"}, {\"start_time\": \"16:00\", \"end_time\": \"17:00\"}, {\"start_time\": \"17:00\", \"end_time\": \"18:00\"}]}, {\"date\": \"2026-07-30\", \"day_label\": \"jueves 30/07\", \"slots\": [{\"start_time\": \"08:00\", \"end_time\": \"09:00\"}, {\"start_time\": \"09:00\", \"end_time\": \"10:00\"}, {\"start_time\": \"10:00\", \"end_time\": \"11:00\"}, {\"start_time\": \"11:00\", \"end_time\": \"12:00\"}, {\"start_time\": \"14:00\", \"end_time\": \"15:00\"}, {\"start_time\": \"15:00\", \"end_time\": \"16:00\"}, {\"start_time\": \"16:00\", \"end_time\": \"17:00\"}, {\"start_time\": \"17:00\", \"end_time\": \"18:00\"}]}, {\"date\": \"2026-07-31\", \"day_label\": \"viernes 31/07\", \"slots\": [{\"start_time\": \"08:00\", \"end_time\": \"09:00\"}, {\"start_time\": \"09:00\", \"end_time\": \"10:00\"}, {\"start_time\": \"10:00\", \"end_time\": \"11:00\"}, {\"start_time\": \"11:00\", \"end_time\": \"12:00\"}, {\"start_time\": \"14:00\", \"end_time\": \"15:00\"}, {\"start_time\": \"15:00\", \"end_time\": \"16:00\"}, {\"start_time\": \"16:00\", \"end_time\": \"17:00\"}, {\"start_time\": \"17:00\", \"end_time\": \"18:00\"}]}], \"days_queried\": 30}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "get_doctor_schedule",
        "id": "5b08f379-1bfd-47db-b4a5-5966db17c554",
        "tool_call_id": "call_OHEHn1gE0mq8SWxnT5lhm10r",
        "status": "success"
      },
      {
        "content": "¿Para cuándo le gustaría agendar su cita? 📅\n\n1. Jueves 09/07\n2. Viernes 10/07\n3. Lunes 13/07\n4. Martes 14/07\n5. Miércoles 15/07\n6. Jueves 16/07\n7. Viernes 17/07",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 76,
            "prompt_tokens": 25955,
            "total_tokens": 26031,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 23424
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPYEUdGRNoKSfAtLMbYA8Tp4T275",
          "service_tier": "default",
          "finish_reason": "stop",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--2668ce26-64d5-4d28-be6c-153269d2e655-0",
        "tool_calls": [],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 25955,
          "output_tokens": 76,
          "total_tokens": 26031,
          "input_token_details": {
            "audio": 0,
            "cache_read": 23424
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "Quiero consulta para ortodoncia",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "human",
        "id": "c7cd4d0d-46a6-4c9a-a333-1a3ebf4cb8c5"
      },
      {
        "content": "Por favor, elija un día para su consulta de ortodoncia:\n\n1. Jueves 09/07\n2. Viernes 10/07\n3. Lunes 13/07\n4. Martes 14/07\n5. Miércoles 15/07\n6. Jueves 16/07\n7. Viernes 17/07",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 78,
            "prompt_tokens": 26045,
            "total_tokens": 26123,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 25984
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPYOH9Z8KMZnotw3MBe7xh3ZN8n2",
          "service_tier": "default",
          "finish_reason": "stop",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--ca279f18-72ea-4bbb-ad0c-01a49fbdb8f5-0",
        "tool_calls": [],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 26045,
          "output_tokens": 78,
          "total_tokens": 26123,
          "input_token_details": {
            "audio": 0,
            "cache_read": 25984
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "Jueves 09/07",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "human",
        "id": "38aff758-9f7e-473c-99b4-4af55543a7b1"
      },
      {
        "content": "Los horarios disponibles del Dr./Dra. Carolina Rojas Godoy para el jueves 09/07 son:\n\n1. 08:00 - 09:00\n2. 14:00 - 15:00\n3. 15:00 - 16:00\n4. 16:00 - 17:00\n5. 17:00 - 18:00\n\nPor favor, elija un horario.",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 92,
            "prompt_tokens": 26136,
            "total_tokens": 26228,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 0
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPYhokkEbMeLg5mLlUIKYaCWvsD8",
          "service_tier": "default",
          "finish_reason": "stop",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--0aae39bb-f0c1-4111-8b6c-a2b458d8aaf2-0",
        "tool_calls": [],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 26136,
          "output_tokens": 92,
          "total_tokens": 26228,
          "input_token_details": {
            "audio": 0,
            "cache_read": 0
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "08:00 - 09:00",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "human",
        "id": "2979d021-53fc-45ed-9028-1499e3e14b38"
      },
      {
        "content": "",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 111,
            "prompt_tokens": 26243,
            "total_tokens": 26354,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 26112
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPZFZMwZLXD6bNCulVEP1t1aaFEj",
          "service_tier": "default",
          "finish_reason": "tool_calls",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--5d0a14fc-9ba6-4e0a-ac01-7518593a5f33-0",
        "tool_calls": [
          {
            "name": "ask_human",
            "args": {
              "question": "Antes de continuar, por favor confirme si los siguientes datos son correctos ✅:\n\n👤 Paciente: ABRAM FRIESEN WALL (36)\n🦷 Especialidad: Ortodoncia\n🛠️ Servicio: Consultas Ortodoncia\n📅 Fecha: 09/07/2026\n⏰ Hora: 08:00\n\nResponda con \"SI\" para confirmar o indique qué dato desea corregir ✍️"
            },
            "id": "call_U7VvAAx7xB5sEelRqzptIX7Z",
            "type": "tool_call"
          }
        ],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 26243,
          "output_tokens": 111,
          "total_tokens": 26354,
          "input_token_details": {
            "audio": 0,
            "cache_read": 26112
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "si",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "ask_human",
        "id": "e6d0d684-855c-4320-bbaa-0cc6638d67d7",
        "tool_call_id": "call_U7VvAAx7xB5sEelRqzptIX7Z",
        "status": "success"
      },
      {
        "content": "",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 111,
            "prompt_tokens": 26364,
            "total_tokens": 26475,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 0
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPbLUpDb4eClb5Gq9rLeEYk2VCcH",
          "service_tier": "default",
          "finish_reason": "tool_calls",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--ddaf931c-1f43-4fd8-b1f6-a4283c7cb9cd-0",
        "tool_calls": [
          {
            "name": "create_appointment",
            "args": {
              "wa_id": "59170088388",
              "doctor_id": 12,
              "horario_cita": "09/07/2026 08:00",
              "nombre_doctor": "Carolina Rojas Godoy",
              "products_name": "Consultas Ortodoncia",
              "products_product_id": 182,
              "motivo_consulta": "Consulta con ortodoncia",
              "seguro_de_vida": "Membresía Odontoking",
              "estado_seguro": "VIGENTE",
              "is_for_self": true,
              "edad_paciente": 36
            },
            "id": "call_KzRm9pMh0vQ86UPtULgGH8oX",
            "type": "tool_call"
          }
        ],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 26364,
          "output_tokens": 111,
          "total_tokens": 26475,
          "input_token_details": {
            "audio": 0,
            "cache_read": 0
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "{\"success\": true, \"person_id\": 2824, \"lead_id\": 2716, \"appointment_registered\": true}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "create_appointment",
        "id": "f645210a-8221-470e-859a-1c433c778b5c",
        "tool_call_id": "call_KzRm9pMh0vQ86UPtULgGH8oX",
        "status": "success"
      },
      {
        "content": "",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 22,
            "prompt_tokens": 26511,
            "total_tokens": 26533,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 0
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPbXhr5tFV53ss2mtzAz4C2eiJmO",
          "service_tier": "default",
          "finish_reason": "tool_calls",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--f9782e46-351b-4e2a-9d9f-fbb1af5f82f8-0",
        "tool_calls": [
          {
            "name": "sync_transcript_to_crm",
            "args": {
              "wa_id": "59170088388"
            },
            "id": "call_1tVRWun1h1yjwWhPvWcEvd9N",
            "type": "tool_call"
          }
        ],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 26511,
          "output_tokens": 22,
          "total_tokens": 26533,
          "input_token_details": {
            "audio": 0,
            "cache_read": 0
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      },
      {
        "content": "{\"success\": true, \"lead_id\": 2716, \"activity_id\": 31672}",
        "additional_kwargs": {},
        "response_metadata": {},
        "type": "tool",
        "name": "sync_transcript_to_crm",
        "id": "eae293e2-090c-4941-8c60-c8fbd9aa546f",
        "tool_call_id": "call_1tVRWun1h1yjwWhPvWcEvd9N",
        "status": "success"
      },
      {
        "content": "Perfecto ✅ ABRAM, su cita ha sido agendada exitosamente con la Dra. Carolina Rojas Godoy:\n\n👤 Paciente: ABRAM FRIESEN WALL (36)  \n🦷 Especialidad: Ortodoncia  \n🛠️ Servicio: Consultas Ortodoncia  \n📅 Fecha: 09/07/2026  \n⏰ Hora: 08:00  \n\nLe recomendamos llegar con al menos 10 minutos de anticipación. \n\n¡Estamos aquí para ayudarle! 🦷✨ Si tiene alguna otra consulta, no dude en escribirnos.",
        "additional_kwargs": {
          "refusal": null
        },
        "response_metadata": {
          "token_usage": {
            "completion_tokens": 121,
            "prompt_tokens": 26566,
            "total_tokens": 26687,
            "completion_tokens_details": {
              "accepted_prediction_tokens": 0,
              "audio_tokens": 0,
              "reasoning_tokens": 0,
              "rejected_prediction_tokens": 0
            },
            "prompt_tokens_details": {
              "audio_tokens": 0,
              "cached_tokens": 26496
            }
          },
          "model_provider": "openai",
          "model_name": "gpt-4o-mini-2024-07-18",
          "system_fingerprint": "fp_8648f47b2a",
          "id": "chatcmpl-DzPbbOI4JIABKC60an9tFmZh01WNI",
          "service_tier": "default",
          "finish_reason": "stop",
          "logprobs": null
        },
        "type": "ai",
        "id": "lc_run--91c207b0-4f2c-4872-9421-db2906309127-0",
        "tool_calls": [],
        "invalid_tool_calls": [],
        "usage_metadata": {
          "input_tokens": 26566,
          "output_tokens": 121,
          "total_tokens": 26687,
          "input_token_details": {
            "audio": 0,
            "cache_read": 26496
          },
          "output_token_details": {
            "audio": 0,
            "reasoning": 0
          }
        }
      }
    ],
    "long_term_memory": "* Prefiere el horario de 17:30 - 18:30 para la cita\n* Prefiere el horario de 17:30 - 18:30 para la cita\n* User's appointment is on 07/07/2026 at 17:30\n* User's appointment is on 09/07/2026 for orthodontics\n* User wants to schedule for a cleaning\n* User has scheduled an appointment with Dr. Adriana Soria\n* Has been to the clinic before\n* Identity card number is 5833699\n* The consultation is for themselves\n* User wants a consultation with orthodontics\n* User is 36 years old\n* User's name is Sofopolis\n* Has Odontoking membership\n* User does not have dental insurance\n* User's name is Abram Friesen Wall"
  },
  "metadata": {
    "ci_paciente": "5833699",
    "intake_handoff": null,
    "is_new_patient": false,
    "ls_run_depth": 0,
    "nombre_registrado": "ABRAM FRIESEN WALL",
    "nombre_whatsapp": "Sofopolis",
    "seguro_paciente": "Membresia Odontoking",
    "thread_id": "59170088388",
    "wa_id": "59170088388"
  },
  "langsmith": {
    "organization": {
      "name": "Personal",
      "id": "19b5b4e8-aea5-43c3-a76d-e1f0ba61491f"
    },
    "workspace": {
      "name": "Workspace 1",
      "id": "4b3fb160-2f53-47e9-85ab-01621ef91153"
    },
    "tracing_project": {
      "id": "fee5ee00-7e67-470c-8125-9dc1c9a7e263",
      "name": "Odontoking"
    }
  }
}	