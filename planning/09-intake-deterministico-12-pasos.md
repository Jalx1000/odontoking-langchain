# 09 — Intake determinista (garantizar los 12 pasos)

> Estado: implementación. Objetivo: que SIEMPRE se cumplan los 12 pasos (nuevo o recurrente),
> moviendo el control del flujo del prompt al CÓDIGO. Enfoque elegido: **Intake determinista
> 1–6 por código + LLM 7–12**. Gate detrás de `INTAKE_ENABLED` (default true).

## Por qué
Los pasos 1-5 los controlaba el prompt y el LLM los saltaba de forma intermitente (Errores
01/02/03), aun con el prompt de 12 pasos desplegado. Un LLM no garantiza un flujo. La
recolección inicial pasa a código determinista; el LLM solo se usa para los pasos dinámicos
con herramientas (7-12) y, dentro del intake, para nada (parsing determinista).

## Alcance
- **Código (determinista):** pasos 1→6 — nombre, edad, ¿para quién?, ¿paciente antiguo?,
  seguro (+CI+verify_insurance), motivo. Pregunta canónica por paso, en orden, sin saltar.
- **LLM (Phase 2):** pasos 7→12 — proponer doctores, días, horas, validación, confirmación,
  agradecimiento. Recibe TODOS los datos del intake inyectados; arranca en el paso 7.

## Estado del intake (persistido en cache_service / Redis, key `intake:{wa_id}`, TTL 1h)
`nombre, edad, is_for_self, tercero_nombre, tercero_edad, es_antiguo, seguro, ci,
seguro_estado, motivo, pending (slot en curso), completo`.
`nombre` se siembra de `nombre_registrado`/`nombre_whatsapp` si existe (no se re-pregunta).

## Orden y preguntas (canónicas; las opciones numeradas salen como botones/listas)
1. nombre (si no se tiene) → edad. (edad: parsear entero 1–120; nunca inventar)
2. ¿Para quién es la cita? [Para mí / Para otra persona]; si otra → nombre + edad de esa persona.
3. ¿Primera vez o ya vino antes? [Primera vez / Ya he ido antes]
4. ¿Seguro? [Alianza / Nacional Vida / Membresía Odontoking / No tengo seguro]
5. Si eligió aseguradora → pedir CI → `verify_insurance(ci, seguro)`:
   - VIGENTE → `seguro_estado=VIGENTE`, continuar.
   - NO vigente → mensaje de regularización, **no agenda**; permite reenviar CI.
   - "No tengo seguro" → `seguro_estado=PARTICULAR`, continuar.
6. Motivo [Dolor dental / Diente quebrado / Encía inflamada / Limpieza / Otro] → intake completo.

## Disparo del intake
- Se activa con intención de agendar (keywords: agendar, cita, reservar, turno, consulta, sacar
  hora, + servicios). Si ya hay intake en curso (estado existe y no completo) → continúa.
- Saludos / preguntas sueltas sin intención → Phase 2 (LLM) responde normal (no fuerza intake).

## Handoff a Phase 2
Al completar el motivo: persistir datos al CRM (`update_crm` con nombre, edad, is_for_self,
paciente_antiguo, seguro, ci, estado_seguro, motivo) e invocar el grafo LLM inyectando en el
contexto: "INTAKE COMPLETO: <datos> → NO repreguntes 1-6; continúa desde el paso 7".

## Parsing determinista (sin LLM por turno)
edad/CI → dígitos; opciones → match por título de botón/keywords; nombre → texto (≥3 chars).
Respuesta no parseable → re-preguntar el mismo paso (no avanza).

## Seguridad / reglas de negocio
- No se avanza de paso sin completar el actual (garantía de orden).
- Seguro: barrera dura (no vigente con aseguradora → no agenda).
- Nunca se inventan datos: cada slot viene del paciente o del CRM.

## Rollout
- `INTAKE_ENABLED` (default true) para activar/desactivar sin redeploy de código.
- Tests unitarios de la lógica pura (orden, parsing, gating, verify). Prueba real en WhatsApp
  tras deploy.

## Archivos
- `app/core/langgraph/intake.py` (NUEVO) — controlador + parsing + store.
- `app/core/config.py` — flag `INTAKE_ENABLED`.
- `app/core/langgraph/odontoking_graph.py` — wiring en `get_response` + inyección de datos a Phase 2.
- `tests/unit/test_intake.py` (NUEVO) — lógica pura.
