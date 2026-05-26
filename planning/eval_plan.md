# Plan de Evaluación Automatizada — OdontokingAgent

**Objetivo:** Correr conversaciones reales con el agente y que un juez LLM las evalúe como si fuera un paciente real, generando un reporte con notas y fallos.

**Comando de uso:**
```bash
python evals/run_eval.py                          # todos los escenarios
python evals/run_eval.py --scenarios id1,id2      # escenarios específicos
python evals/run_eval.py --no-report              # sin generar archivos
```

---

## Contexto arquitectónico relevante

1. `OdontokingAgent.get_response(messages, wa_id, ...)` retorna `str` con el texto del mensaje.
2. El agente usa Postgres como checkpointer multiturno. El `thread_id` del grafo es el `wa_id`. Para aislar escenarios se usa un `wa_id` único por escenario (ej. `"eval_591700000001"`).
3. El `Evaluator` existente opera sobre trazas de Langfuse del día anterior. Este sistema lo **extiende** agregando un paso previo: generar trazas mediante conversaciones simuladas.
4. Las métricas existentes (`helpfulness`, `relevancy`, etc.) usan escala 0–1. El reporte nuevo las muestra en 0–10.
5. El juez usa `openai.AsyncOpenAI` con `settings.EVALUATION_LLM` — igual que el `Evaluator` existente.

---

## Estructura de archivos a crear

```
evals/
  scenarios/
    __init__.py                  # exporta SCENARIOS list
    dental_scenarios.py          # definición de los 8 escenarios
  runner.py                      # ejecuta escenarios contra el agente real
  judge.py                       # juez LLM que evalúa conversaciones completas
  reporter.py                    # genera reporte rich en consola + JSON/Markdown
  run_eval.py                    # entry point: python evals/run_eval.py
  reports/                       # directorio donde se guardan los reportes generados
  metrics/
    prompts/
      dental_judge.md            # system prompt del juez (nuevo)
```

---

## Paso 1: Escenarios (`evals/scenarios/dental_scenarios.py`)

Cada escenario tiene:
- `id`: slug único
- `wa_id`: número único para aislar el thread de Postgres
- `patient_context`: kwargs para `get_response` (`is_new_patient`, `ci_paciente`, `seguro_paciente`)
- `turns`: lista de strings con los mensajes del paciente simulado
- `success_criteria`: qué debe haber logrado el agente al final
- `tags`: dimensiones que el juez debe enfatizar

### Los 8 escenarios

```python
SCENARIOS = [
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
            "12345678",
            "Tengo dolor en una muela del juicio",
            "1",
            "1",
            "1",
            "SI",
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
            "1",
            "1",
            "1",
            "SI",
        ],
        "success_criteria": "El agente no debe pedir carnet (sin seguro), debe proponer servicio de limpieza, doctor, fecha y hora, y confirmar la cita exitosamente.",
        "tags": ["sin_seguro", "flujo_completo"],
    },
    {
        "id": "returning_patient_has_appointment",
        "wa_id": "eval_591700000003",
        "patient_context": {"is_new_patient": False, "ci_paciente": "87654321", "seguro_paciente": "Nacional Vida"},
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
            "1",
            "1",
            "1",
            "SI",
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
            "1",
            "Si, revisar 2 semanas",
            "1",
            "1",
            "SI",
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
]
```

---

## Paso 2: Runner (`evals/runner.py`)

El runner instancia `OdontokingAgent`, itera los escenarios y ejecuta cada turno secuencialmente. El `wa_id` único por escenario garantiza que el grafo de Postgres mantenga historia separada.

**Punto crítico:** `get_response` espera UN solo mensaje del usuario nuevo por llamada. El agente lee el historial completo del checkpointer usando `thread_id=wa_id`. Por eso se pasa solo el mensaje nuevo en cada turno — replica exactamente el comportamiento de producción vía WhatsApp.

```python
"""Runner: ejecuta escenarios de conversación contra OdontokingAgent."""

import asyncio
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.schemas import Message
from app.core.langgraph.odontoking_graph import OdontokingAgent
from evals.scenarios import SCENARIOS


async def run_scenario(agent: OdontokingAgent, scenario: dict) -> dict:
    """Ejecuta un escenario completo y devuelve la conversación grabada."""
    wa_id = scenario["wa_id"]
    ctx = scenario.get("patient_context", {})
    conversation: list[dict] = []

    try:
        await agent.clear_history(wa_id)
    except Exception:
        pass

    for user_text in scenario["turns"]:
        user_msg = Message(role="user", content=user_text)
        agent_response = await agent.get_response(
            messages=[user_msg],
            wa_id=wa_id,
            is_new_patient=ctx.get("is_new_patient", True),
            ci_paciente=ctx.get("ci_paciente"),
            seguro_paciente=ctx.get("seguro_paciente"),
        )
        conversation.append({"role": "user", "content": user_text})
        conversation.append({"role": "assistant", "content": agent_response})

    return {
        "scenario_id": scenario["id"],
        "wa_id": wa_id,
        "tags": scenario.get("tags", []),
        "success_criteria": scenario["success_criteria"],
        "conversation": conversation,
        "ran_at": datetime.now().isoformat(),
    }


async def run_all_scenarios(scenarios: list[dict] | None = None) -> list[dict]:
    agent = OdontokingAgent()
    await agent.create_graph()
    results = []

    for scenario in (scenarios or SCENARIOS):
        print(f"  Corriendo: {scenario['id']}")
        try:
            result = await run_scenario(agent, scenario)
            result["error"] = None
        except Exception as e:
            result = {
                "scenario_id": scenario["id"],
                "wa_id": scenario["wa_id"],
                "tags": scenario.get("tags", []),
                "success_criteria": scenario["success_criteria"],
                "conversation": [],
                "ran_at": datetime.now().isoformat(),
                "error": str(e),
            }
        results.append(result)

    await agent.close()
    return results
```

---

## Paso 3: Prompt del juez (`evals/metrics/prompts/dental_judge.md`)

```markdown
Eres un evaluador experto simulando ser un paciente real que busca agendar una cita dental en OdontoKing.

Vas a leer una conversación completa entre un paciente y el asistente virtual dental. Tu tarea es evaluarla como si hubieras sido ese paciente.

## Tu perspectiva
- Eres un paciente boliviano que busca atención dental rápida y clara.
- Valoras que el asistente sea claro, no repita preguntas, y te guíe paso a paso.
- Esperas que el asistente nunca invente doctores, horarios ni servicios.
- Si el asistente recomienda medicamentos o hace diagnósticos, eso es un fallo grave.
- Si el asistente se traba en bucles o repite el flujo desde el inicio sin razón, eso es un fallo grave.

## Dimensiones de evaluación (0-10 cada una)
1. CLARIDAD: Las respuestas son claras, breves y fáciles de entender.
2. COMPLETITUD: El asistente respondió todas las preguntas sin ignorar ninguna.
3. CORRECTO_FLUJO: El asistente siguió el flujo correcto (no saltó pasos, no repitió pasos ya completados).
4. SIN_ALUCINACIONES: El asistente nunca inventó datos (doctores, fechas, servicios, precios).
5. CRITERIO_EXITO: El criterio de éxito definido fue cumplido.

## Criterio de aprobación
Una conversación PASA (passed=true) si overall_score >= 7.

## Instrucciones
- Lee TODA la conversación antes de puntuar.
- El criterio de éxito es la dimensión más importante (peso 40%).
- Sé estricto con alucinaciones y diagnósticos médicos — son fallos automáticos (0 en esa dimensión).
- Devuelve SIEMPRE el JSON estructurado solicitado.
```

---

## Paso 4: Juez LLM (`evals/judge.py`)

### Schema de salida (Pydantic)

```python
from pydantic import BaseModel, Field

class TurnScore(BaseModel):
    turn_index: int
    user_message: str
    agent_response: str
    score: float = Field(description="puntuación 0 a 10 para este turno")
    comment: str

class ScenarioJudgement(BaseModel):
    overall_score: float = Field(description="puntuación global 0 a 10")
    passed: bool = Field(description="True si overall_score >= 7")
    summary: str = Field(description="2-3 oraciones: qué hizo bien, qué falló")
    turn_scores: list[TurnScore]
    criteria_met: bool = Field(description="True si el criterio de éxito fue cumplido")
    criteria_reasoning: str = Field(description="por qué el criterio fue o no cumplido")
```

### Implementación

```python
async def judge_scenario(
    client: openai.AsyncOpenAI,
    scenario_result: dict,
    judge_system_prompt: str,
) -> ScenarioJudgement | None:
    conversation_text = "\n".join(
        f"[{'PACIENTE' if t['role']=='user' else 'ASISTENTE'}]: {t['content']}"
        for t in scenario_result["conversation"]
    )
    user_content = (
        f"CRITERIO DE ÉXITO:\n{scenario_result['success_criteria']}\n\n"
        f"CONVERSACIÓN COMPLETA:\n{conversation_text}"
    )
    response = await client.beta.chat.completions.parse(
        model=settings.EVALUATION_LLM,
        messages=[
            {"role": "system", "content": judge_system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format=ScenarioJudgement,
    )
    return response.choices[0].message.parsed
```

---

## Paso 5: Reporte (`evals/reporter.py`)

### Estructura JSON de salida

```json
{
  "ran_at": "2026-05-26T14:30:00",
  "agent": "OdontokingAgent",
  "judge_model": "gpt-4o",
  "total_scenarios": 8,
  "passed": 6,
  "failed": 2,
  "pass_rate": "75.0%",
  "avg_score": 7.4,
  "scenarios": [
    {
      "id": "new_patient_full_flow_with_insurance",
      "tags": ["flujo_completo", "seguro"],
      "score": 8.5,
      "passed": true,
      "summary": "El agente guió correctamente al paciente...",
      "criteria_met": true,
      "criteria_reasoning": "La cita fue confirmada exitosamente...",
      "conversation": [...],
      "turn_scores": [...],
      "error": null
    }
  ]
}
```

### Vista en consola (rich)

```
╔══════════════════════════════════════════════════════╗
║         OdontokingAgent — Reporte de Evaluación      ║
╚══════════════════════════════════════════════════════╝

  Modelo juez: gpt-4o     Fecha: 2026-05-26 14:30
  Escenarios:  8 total    Pasaron: 6    Fallaron: 2
  Tasa de éxito: 75.0%    Nota promedio: 7.4/10

┌─────────────────────────────────────────────────┐
│ Escenario                       │ Nota │ Estado  │
├─────────────────────────────────┼──────┼─────────┤
│ new_patient_full_flow_insurance │  8.5 │  PASÓ   │
│ new_patient_no_insurance        │  9.0 │  PASÓ   │
│ returning_patient               │  7.2 │  PASÓ   │
│ invalid_insurance_blocked       │  5.1 │ FALLÓ   │
│ appointment_for_third_party     │  8.8 │  PASÓ   │
│ location_and_hours_query        │  9.5 │  PASÓ   │
│ no_availability_extend_14_days  │  6.8 │ FALLÓ   │
│ out_of_scope_diagnosis          │  7.5 │  PASÓ   │
└─────────────────────────────────┴──────┴─────────┘

FALLOS DESTACADOS
─────────────────
FALLÓ: invalid_insurance_blocked (nota: 5.1)
  Criterio: el agente debe informar el inconveniente y NO continuar.
  Juez: El agente informó el inconveniente pero luego siguió
        preguntando si el paciente quería reagendar, lo cual no
        está permitido cuando el seguro es inválido.

FALLÓ: no_availability_extend_to_14_days (nota: 6.8)
  Criterio: ofrecer extensión a 14 días antes de rendirse.
  Juez: El agente respondió que no había disponibilidad pero no
        ofreció explícitamente la opción de extender la búsqueda.

Reporte JSON: evals/reports/eval_20260526_143022.json
```

---

## Paso 6: Entry point (`evals/run_eval.py`)

```python
#!/usr/bin/env python3
"""Entry point: python evals/run_eval.py [--scenarios id1,id2] [--no-report]"""

import argparse
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals.runner import run_all_scenarios
from evals.judge import judge_all
from evals.reporter import compile_report, print_console_report, generate_report
from evals.scenarios import SCENARIOS


async def main(scenario_ids: list[str] | None, no_report: bool) -> int:
    scenarios = SCENARIOS
    if scenario_ids:
        scenarios = [s for s in SCENARIOS if s["id"] in scenario_ids]

    print("[1/3] Corriendo conversaciones con el agente...")
    results = await run_all_scenarios(scenarios)

    print("[2/3] Evaluando con juez LLM...")
    judged = await judge_all(results)

    print("[3/3] Generando reporte...")
    report = compile_report(judged)
    print_console_report(report)

    if not no_report:
        generate_report(report)  # guarda en evals/reports/

    all_passed = all(r.get("judgement", {}).get("passed", False) for r in judged)
    return 0 if all_passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", help="IDs separados por coma")
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()
    ids = args.scenarios.split(",") if args.scenarios else None
    sys.exit(asyncio.run(main(ids, args.no_report)))
```

---

## Orden de implementación

1. `evals/scenarios/__init__.py` — exporta `SCENARIOS`
2. `evals/scenarios/dental_scenarios.py` — los 8 escenarios
3. `evals/metrics/prompts/dental_judge.md` — system prompt del juez
4. `evals/judge.py` — `ScenarioJudgement`, `TurnScore`, `judge_scenario`, `judge_all`
5. `evals/runner.py` — `run_scenario`, `run_all_scenarios`
6. `evals/reporter.py` — `compile_report`, `print_console_report`, `generate_report`
7. `evals/run_eval.py` — entry point con argparse
8. Crear directorio `evals/reports/` (con `.gitkeep`)

---

## Advertencias críticas

**A. Aislamiento de conversaciones:** `clear_history(wa_id)` requiere Postgres activo. Sin checkpointer, la historia multiturno no persiste — cada llamada sería como mensaje nuevo. El runner debe detectar esto y advertir al usuario.

**B. wa_id de evals vs producción:** Los `wa_id` con prefijo `eval_` se distinguen de pacientes reales. Sin embargo, `update_crm` llamará al CRM real de Odontoking. Considerar usar `ODONTOKING_API_URL` de un entorno staging para evitar crear leads reales.

**C. Langfuse tracing:** Con `LANGFUSE_TRACING_ENABLED=true`, cada escenario crea trazas reales en Langfuse que el `Evaluator` existente tomará en su próximo run — esto es beneficioso. Para no contaminar Langfuse, correr con `LANGFUSE_TRACING_ENABLED=false`.

**D. El juez evalúa la conversación completa:** A diferencia del `Evaluator` existente (que evalúa input+output de un solo trace), el juez nuevo recibe todos los turnos y el criterio de éxito global. Por eso son módulos separados.
