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

## Update — Error 02: el seguro no se verifica en pacientes recurrentes (2026-06-14, noche)
**Síntoma:** paciente recurrente CON nombre (`nombre_registrado=Javier Mogro`) pero sin
`ci`/`seguro` en contexto → el agente agendó **sin pasar por verificación de seguro**.
**Causa:** la regla 2 (`paciente_nuevo: false`) enrutaba directo al paso 6 (Motivo), saltando
los pasos 4-5 (seguro). El fix anterior solo forzaba onboarding cuando faltaba el NOMBRE.
**Regla de negocio (confirmada):** verificación de seguro **obligatoria antes de agendar**,
también recurrentes. Aseguradora + `verify_insurance` NO vigente → **no se agenda** (regularizar).
"No tengo seguro" → **sí agenda** (particular).
**Cambios:**
- `odontoking_graph.py` `_load_odontoking_prompt`: inyecta gate dinámico `# ⚠️ SEGURO NO
  VERIFICADO ...` cuando falta `ci` o `seguro` (cualquier paciente).
- `odontoking.md`: regla 2 refuerza seguro obligatorio para recurrentes; paso 4 marcado
  obligatorio + rama "No tengo seguro"=particular; paso 11 añade condición (c) seguro resuelto;
  REGLA FINAL DE ORO con "SEGURO = BARRERA OBLIGATORIA".
- `tests/unit/test_odontoking_prompt.py`: +3 tests del gate (`TestInsuranceGate`).
**Verificación:** `uv run pytest` → **275 passed**.

## Update — Error 03: no respeta el flujo ordenado + inventa la edad (2026-06-14, noche)
**Síntoma:** el agente improvisa el orden del flujo (arranca por seguro o motivo, omite "para
quién" y "paciente antiguo") y en una validación **inventó la edad ("38")**.
**Causa:** la bifurcación "nuevo vs recurrente" (regla 2) enrutaba al recurrente directo al
paso 6, y los pasos 1/2/3 estaban marcados "SOLO para paciente_nuevo: true".
**Decisiones (confirmadas):** flujo **único 1→12 en orden** para nuevo Y recurrente; se piden
SOLO los datos que faltan (nombre conocido no se re-pregunta; edad se pregunta si falta);
pasos 2 y 3 SIEMPRE en cada agendamiento; NUNCA inventar nombre ni edad.
**Cambios (solo prompt `odontoking.md`):**
- Regla de contexto: "FLUJO DE AGENDAMIENTO ÚNICO" 1→12; eliminado el atajo de recurrente.
- Pasos 1/2/3: quitado "SOLO para paciente_nuevo: true" → aplican a todo agendamiento.
- Paso 10/11 + REGLA DE ORO: exigen nombre y **edad reales** (guard contra "[edad]"/inventar);
  "FLUJO EN ORDEN" como regla de oro.
**Verificación:** `uv run pytest` → **275 passed** (cambio solo de prompt, sin código).

## Update — Error 04: WhatsApp 400 al enviar botones/listas (2026-06-15)
**Síntoma (logs prod):** `httpx.HTTPStatusError: 400` en `send_interactive_message`. Detalle de
Meta: `(#131009) Parameter value is not valid — "Markdown is not allowed for button title"`.
El mensaje del agente NO se entregaba al paciente.
**Causa:** el LLM usa Markdown (`**negrita**`) y `build_interactive_payload` ponía ese texto
con markdown en los títulos de botones/filas. WhatsApp prohíbe markdown en títulos → 400.
(Además el límite del body estaba en 4096; el real de interactive es 1024.)
**Cambios:**
- `whatsapp_client.py`: nuevo `_clean_title()` que quita `* _ ~ \`` y colapsa espacios en los
  títulos de botones/listas; títulos nunca vacíos; body de interactive capado a 1024; si un
  título queda vacío tras limpiar → fallback a texto plano.
- `odontoking.md`: regla de estilo "NO uses Markdown" (texto plano).
- `tests/unit/test_whatsapp_client.py`: +2 tests (markdown stripped de botones y listas).
**Verificación:** `uv run pytest` → **277 passed**.

## Deploy
- **Proyecto Railway:** `Odontoking` (ID `df51b2f5-e9d7-4a31-aad1-0291a076d303`), env `production`.
  Servicio backend: **`odontoking-langchain`** (agente in-process). Otros: pgvector, Redis,
  RabbitMQ(+UI/prod), DbGate, `04.agent-production-front`, `odontoking-evals`.
- **Fix #1 (Error 01) DESPLEGADO:** commit `ae35f4d` → push a `origin/master` → deployment
  `96a4d608` SUCCESS (2026-06-14 21:23), instance arrancó `2026-06-15T01:25:38Z`. En vivo.
- **Fix #2 (Error 02, gate de seguro): PENDIENTE de deploy** — en working tree, sin commitear.
- **Deploy:** commit + push a `origin/master` → Railway auto-reconstruye. **Sin migraciones de DB.**
  Es deploy a producción → requiere OK explícito del usuario.
- Nota: `.github/workflows/deploy.yaml` está roto (`make docker-build-env` no existe); Railway
  construye directo del repo, así que no bloquea, pero conviene arreglarlo aparte.
