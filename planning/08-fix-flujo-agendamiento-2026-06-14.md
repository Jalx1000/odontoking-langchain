# Fix — Flujo de agendamiento del agente Odontoking (2026-06-14)

**Branch:** `master` · **Estado:** corregido en working tree, **pendiente de deploy a Railway (prod)**
**Tests:** 272 passed / 0 failed · **Migraciones DB:** ninguna

## Problema reportado (producción)
Paciente con historial de chat pero sin datos en el CRM: el agente lo trató como recurrente,
NO pidió nombre/edad, NO verificó seguro, mostró el literal `[Nombre]` en la confirmación y
agendó una cita que no quedó registrada correctamente en el CRM.

## Causas raíz
> ⚠️ Corrección tras revisar Railway: el bug `get_doctors` (#1) **NO está en producción**.
> Producción corre `origin/master` = `23eb7da` (deploy 2026-06-10), cuyo `get_doctors` funciona
> (logs: `odontoking_doctors_fetched count=12` el 2026-06-14). El bug `true` se introdujo en el
> commit local **`b253092` (2026-06-14, SIN pushear)** — es una regresión que rompería prod si
> se despliega. La causa REAL del error reportado en prod son #2/#3/#4 (onboarding/nombre).

1. **`get_doctors` roto** ([odontoking.py](../app/core/langgraph/tools/odontoking.py)): filtro
   `d.get("has_availability" != true)` → `NameError` (`true` no existe). Solo en el commit local
   `b253092` (no desplegado). Mi fix lo neutraliza antes de cualquier deploy.
2. **Lógica nuevo vs recurrente** (ESTA es la causa del error en prod): "existe en CRM" se trataba como "datos completos". El
   placeholder `"Paciente WhatsApp"` (auto-creado en el primer "hola") envenenaba el flag.
3. **El nombre real nunca se inyectaba** al contexto del agente → fuga de `[Nombre]`.
4. **Seguro** ([insurance.py](../app/core/langgraph/tools/insurance.py)): pisaba `VENCIDO`→`VIGENTE`.
5. **Horarios** ([odontoking.py](../app/core/langgraph/tools/odontoking.py)):
   `get_doctor_schedule` devolvía el string `"Sin disponibilidad"` en vez de `[]`.

## Decisiones de negocio aplicadas
- Paciente sin nombre real (aunque exista en CRM) → **se trata como nuevo** (onboarding completo).
- **Resolución del nombre por prioridad:** `verify_insurance.patient_name` → nombre del evento
  de WhatsApp (profile name) → preguntar. El `advisor` no aplica aquí.

## Cambios por archivo
| Archivo | Cambio |
|---|---|
| `app/core/langgraph/tools/odontoking.py` | `get_doctors` filtra solo por `is_active`; `get_doctor_schedule` devuelve `schedule: []` |
| `app/core/langgraph/tools/insurance.py` | preserva el `status` real del seguro (no pisa `VENCIDO`) |
| `app/core/langgraph/tools/crm.py` | `_real_name_or_none()` + `ensure_person_registered` devuelve `nombre_registrado` (placeholder→None) |
| `app/api/v1/whatsapp.py` | propaga `nombre_registrado` + nombre del evento WhatsApp al agente |
| `app/core/langgraph/odontoking_graph.py` | fuerza `paciente_nuevo=true` sin nombre real; inyecta nombre al contexto |
| `app/core/prompts/odontoking.md` | resolución de nombre; onboarding si faltan datos; pasos 10-11 endurecidos (sin `[Nombre]`, requiere "SI" explícito) |
| `tests/unit/test_odontoking_prompt.py` (NUEVO) | 4 tests del builder de contexto |
| `tests/unit/tools/test_crm_tool.py` | 4 tests de `_real_name_or_none` |

## Verificación
- `uv run pytest` → **272 passed**. Los 3 tests antes en rojo (get_doctors, seguro vencido,
  horarios vacíos) ahora en verde.
- ruff: archivos modificados limpios. (Deuda preexistente de `D102` en otros tests, fuera de alcance.)

## Supuesto a validar
La prioridad "nombre desde el seguro" asume que `verify_insurance` retorna `patient_name`.
`insurance.py` devuelve el payload crudo, así que si la API lo incluye, fluye; si no, el
fallback al nombre del evento WhatsApp cubre el caso. **Confirmar con la API real.**

## Deploy
- **Proyecto Railway:** `Odontoking` (ID `df51b2f5-e9d7-4a31-aad1-0291a076d303`), env `production`.
  Servicio backend: **`odontoking-langchain`** (agente in-process). Otros: pgvector, Redis,
  RabbitMQ(+UI/prod), DbGate, `04.agent-production-front`, `odontoking-evals`.
- **Estado actual de prod:** corre `origin/master` = `23eb7da` (deploy 2026-06-10). `get_doctors`
  funciona (logs `count=12` el 2026-06-14). El error reportado se debe a #2/#3/#4 (onboarding/nombre).
- **Estado de git:** `origin/master` = `23eb7da`. Local `master` = `b253092` (regresión `true`,
  sin pushear). Mis fixes están en el working tree **sin commitear**.
- **Para desplegar el fix:** commit de los fixes (deja `get_doctors` correcto, anula `b253092`) →
  push a `origin/master` → Railway auto-reconstruye (`railway.toml` builder=dockerfile). **Sin
  migraciones de DB.** Es deploy a producción → requiere OK explícito del usuario.
- Nota: `.github/workflows/deploy.yaml` está roto (`make docker-build-env` no existe); Railway
  construye directo del repo, así que no bloquea, pero conviene arreglarlo aparte.
