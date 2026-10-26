# Mejoras Odontoking — Validación reactiva de seguro + Pre‑llenado dinámico del formulario

- **Fecha:** 2026-10-23
- **Autor:** Revisión de ingeniería (diagnóstico a partir de logs de producción)
- **Rama de producción:** `master` (flujo basado en formularios, `WHATSAPP_FLOWS_ENABLED=true`)
- **Estado:** Propuesta — **no aplicado** (pendiente de aprobación)
- **Alcance:** `app/core/prompts/odontoking.md`, `app/core/langgraph/odontoking_graph.py`, `app/core/langgraph/tools/whatsapp_flows.py` (+ coordinación con CRM para el pre‑llenado)

---

## 1. Resumen ejecutivo

Se detectó en producción que el agente de Odontoking **responde mal cuando el paciente menciona que tiene seguro o membresía**: lo confunde con una pregunta de precio y, además, improvisa respuestas que no están en ningún prompt. Este documento recoge:

1. **Mejora 1 — Validación reactiva de seguro/membresía** (corrige el bug observado).
2. **Mejora 2 — Pre‑llenado dinámico del formulario** (si en el saludo el paciente ya dijo *para qué* y *para quién*, auto‑rellenarlo en el form).

---

## 2. El incidente (evidencia de producción)

Conversación real, `wa_id 59175640698` ("Danitza"), 2026-10-23:

| # | Hora | Paciente | Respuesta del bot | ¿OK? |
|---|------|----------|-------------------|------|
| 1 | 15:28 | "Buenas, quiero agendar cita" | Envía formulario (`paciente_tercero`) | ✅ |
| 2 | 19:27 | *form completado* (`status: pending`, `patient_status: self`) | "Estoy registrando su solicitud…" | ✅ |
| 3 | 19:36 | "Quiero agendar" | Envía formulario otra vez | ⚠️ (ver §2.1) |
| 4 | 19:44 | *form completado* (`status: pending`, `patient_status: created`, name "Mar…") | "Estoy registrando su solicitud…" | ✅ |
| 5 | 19:44:52 | **"Yo tengo seguro membresia"** | "…no brindamos información sobre **precios** por este chat…" | ❌ |
| 6 | 19:44:57 | **"Me cubre"** | "…consultar directamente con su **proveedor**…" | ❌ |
| 7 | 19:44:58 | **"???"** (confusión) | "¿Le gustaría agendar una cita…?" | — |

### 2.1 Observación secundaria (turnos 1–4): probablemente NO es bug

El doble envío de formulario encaja con **dos agendas legítimas**: turno 2 para ella misma (`self`), turno 4 para un tercero recién creado (`created`, "Mar…"). Lo único a verificar: el prompt promete que *"el CRM envía la confirmación ✅ directamente al paciente"*, pero esa confirmación **no aparece en estos logs** (es otro servicio). **Acción:** confirmar en el CRM que las citas se crearon y se confirmaron.

---

## 3. Causa raíz (turnos 5–7)

**El prompt que corre en producción (`master`) no tiene ningún manejo de seguro/membresía, y el tool de verificación está desconectado.**

1. **Prompt:** la migración a formularios reemplazó la versión conversacional por una basada en `enviar_formulario`. En esa versión se eliminó **toda** la lógica de seguros. Verificado: en `master/app/core/prompts/odontoking.md` no aparecen las palabras `seguro`, `membresía`, `verify_insurance` ni `aseguradora`.
2. **Tool desconectado:** en `odontoking_graph.py` el binding depende de un feature flag:
   ```python
   _FORMS_TOOLS = [enviar_formulario, get_citas, get_services, get_specialties]   # verify_insurance NO está
   _CLASSIC_TOOLS = [..., verify_insurance, ...]
   _ODONTOKING_TOOLS = _FORMS_TOOLS if settings.WHATSAPP_FLOWS_ENABLED else _CLASSIC_TOOLS
   ```
   Con flows activo (producción), el LLM **no puede llamar** `verify_insurance`.
3. **Context‑builder:** el bloque de seguro (`seguro_registrado`, `ci_paciente_registrada`) solo se inyecta cuando `not WHATSAPP_FLOWS_ENABLED`. En modo formularios el agente no recibe nada de seguro.

**Consecuencia:** sin ninguna política para "seguro/membresía", el LLM:
- (turno 5) cae en la **única regla cercana que tiene — la deflección de precios** (`LÍMITES ESTRICTOS → "Ante precio"`).
- (turno 6) **improvisa** "consulte con su proveedor". Ese texto **no existe en ningún prompt** del repo (0 coincidencias en todas las ramas) → es una alucinación, y es absurda: para "Membresía Odontoking", **Odontoking ES el proveedor**.

---

## 4. Mejora 1 — Validación reactiva de seguro / membresía

**Requisito de negocio (definido por el equipo):** el bot **solo** valida el seguro de forma **reactiva** — cuando en el chat el paciente *lo pida* o *diga que tiene seguro/membresía de una aseguradora*. **No** es un paso obligatorio de cada agenda (eso lo gestiona el formulario / recepción).

### 4.1 Herramienta disponible (ya existe, hay que reconectarla)

`verify_insurance` (`app/core/langgraph/tools/insurance.py`) sigue funcional:
- **Endpoint:** `POST /api/insurance/verify` (única fuente para las 3 opciones).
- **Params:** `wa_id`, `ci_paciente` (solo dígitos), `seguro_paciente` ∈ { `"Nacional Vida"`, `"Alianza"`, `"Membresía Odontoking"` }.
- **Cobertura activa SOLO si** `status == "VIGENTE"`. Cualquier otro (`EN_MORA`, `VENCIDO`, `NO_REGISTRADO`, `SIN_SEGURO`, `INDETERMINADO`) → sin cobertura confirmada.
- Devuelve también `patient_name` cuando la aseguradora lo trae.
- **Vitalia NO está soportada** por este endpoint.

### 4.2 Parche A — Código: reconectar el tool

`app/core/langgraph/odontoking_graph.py`

```diff
 _FORMS_TOOLS = [
     enviar_formulario,
     get_citas,
     get_services,
     get_specialties,
+    verify_insurance,
 ]
```

> Sin esto, el modelo no puede llamar `verify_insurance` en producción.

### 4.3 Parche B — Prompt: declarar el tool

`app/core/prompts/odontoking.md` → sección `HERRAMIENTAS DISPONIBLES`

```diff
 🔧 get_specialties → Especialidades reales (para responder dudas de qué especialidades hay).
+🔧 verify_insurance → Valida la cobertura de un seguro/membresía. Úsala SOLO de forma REACTIVA:
+   cuando el paciente PIDA validar su seguro o DIGA que tiene seguro/membresía de una aseguradora.
+   NO es un paso obligatorio para agendar. Params: wa_id, ci_paciente (solo dígitos),
+   seguro_paciente (exactamente "Nacional Vida", "Alianza" o "Membresía Odontoking").
```

### 4.4 Parche C — Prompt: bloque nuevo (antes de `LÍMITES ESTRICTOS`)

```
═══════════════════════════════════════════
SEGURO Y MEMBRESÍA (validación REACTIVA — no es pregunta de precio)
═══════════════════════════════════════════
Mencionar un seguro o membresía NO es una pregunta de precio: NUNCA dispares la frase de precios
ni le digas que consulte "con su proveedor" (Odontoking gestiona directamente la "Membresía Odontoking").

Valida con verify_insurance SOLO en estos casos del chat:
  (a) el paciente PIDE que valides su seguro / pregunta "¿me cubre?", "¿está vigente?"; o
  (b) el paciente DICE que tiene seguro o membresía de una aseguradora.
Fuera de esos casos NO valides nada: sigue el flujo normal de formularios.

Cómo validar:
1. Mapea la aseguradora EXACTAMENTE a una de:
   - "Nacional" / "Nacional Seguros" / "Nacional Vida" → "Nacional Vida"
   - "Alianza" (particular o empresa)                 → "Alianza"
   - "soy socio" / "membresía" / "Membresía Odontoking" → "Membresía Odontoking"
   Si menciona otra aseguradora fuera de esta lista (p. ej. Vitalia), no se puede validar aquí → deriva a recepción.
2. Pide el carnet si no lo tienes:
   {"mensaje": "Con gusto valido su cobertura. ¿Me comparte su número de carnet de identidad, por favor? 🪪"}
3. Llama verify_insurance (wa_id + ci_paciente + seguro_paciente).
4. Responde SOLO según el resultado:
   - has_insurance:true y status "VIGENTE" → cobertura activa; confírmalo y ofrece agendar:
     {"mensaje": "¡Listo! Su cobertura figura como vigente ✅. ¿Le ayudo a agendar su cita?"}
   - cualquier otro status (EN_MORA, VENCIDO, NO_REGISTRADO, …) → NO vigente:
     {"mensaje": "Verifiqué su cobertura y por ahora no figura como vigente en nuestra clínica. Le recomendamos regularizarla con su bróker o aseguradora. Si desea, igual puedo ayudarle a agendar como paciente particular. 🙌"}
   - retry:true o error → frase de inconveniente técnico; no afirmes si está vigente o no.
NUNCA inventes la cobertura ni el estado.
```

### 4.5 Parche D — Prompt: acotar la regla de precios

`app/core/prompts/odontoking.md` → sección `LÍMITES ESTRICTOS`

```diff
-- No damos precios (si los ves como 0, ignóralos). Ante precio:
+- No damos precios (si los ves como 0, ignóralos). OJO: "tengo seguro/membresía" o "¿me cubre?"
+  NO es pregunta de precio (ver SEGURO Y MEMBRESÍA). Solo ante precio/costo/tarifa REAL:
   {"mensaje": "Con el objetivo de ofrecer una atención personalizada, no brindamos información sobre precios por este chat. Con gusto puedo ayudarle a programar una cita para una valoración."}
```

### 4.6 Parche E — Opcional: pre‑llenar el carnet ya registrado

En modo formularios el context‑builder no inyecta el seguro. Para no re‑preguntar el carnet a quien ya lo tiene registrado, se puede inyectar `ci_paciente_registrada` / `seguro_registrado` **solo como pre‑llenado** (sin el "hard‑gate" de la versión classic). Opcional; no bloquea la Mejora 1.

### 4.7 Notas importantes

- **La validación es solo informativa.** En modo formularios no hay `save_insurance` y el form `paciente_tercero` no captura aseguradora: el bot valida y le informa al paciente, pero **ese dato no se asocia a la cita** (eso lo hace el formulario/recepción). Coincide con el requisito "solo validar".
- **Vitalia** → derivar a recepción (no soportada por el endpoint).
- Hay TODOs relacionados en el repo: `todo/37-insurance-keys-drift-between-tools-[bug].md` y `todo/63-prompt-missing-alianza-bug.md` — revisar al tocar seguros.

---

## 5. Mejora 2 — Pre‑llenado dinámico del formulario

**Idea:** si en el saludo el paciente ya dijo **para qué** (motivo / servicio / especialidad) y **para quién** (para él mismo o para otra persona, e incluso el nombre), esos campos deberían venir **auto‑rellenados** en el formulario que se le envía, para que no los tenga que volver a escribir.

### 5.1 ¿Se puede? — Sí, con dependencia del CRM

**Factible, pero NO es solo cambio del agente.** Hoy:

- El agente envía únicamente `{"flow": "<flow>"}` a `POST /api/v1/whatsapp/conversations/{conversation_id}/flows` (ver `whatsapp_flows.py`).
- El `flow_reply` entrante trae las claves: `flow, status, patient_name, relation, specialty, doctor_name, date, time` → esos son los campos del formulario.

Para pre‑llenar hacen falta **tres piezas coordinadas**:

| Pieza | Cambio | Responsable |
|-------|--------|-------------|
| 1. **Definición del Flow (WhatsApp/Meta)** | El Flow debe aceptar `data` inicial para pre‑poblar pantallas (navigate con payload, o `data_exchange`). | CRM / Meta |
| 2. **Endpoint del CRM** `.../flows` | Aceptar un `prefill` adicional y pasarlo como `data` inicial del Flow. | CRM |
| 3. **Agente** `enviar_formulario` | Detectar *para qué*/*para quién* en el saludo y enviar los campos como `prefill`. | Este repo |

> ⚠️ **Gating:** el pre‑llenado solo funciona si las piezas 1 y 2 existen. El cambio del agente (pieza 3) es inútil por sí solo. **Confirmar con el equipo de CRM** si los Flows publicados admiten datos iniciales.

### 5.2 Campos pre‑rellenables (realistas)

| Campo del form | ¿Pre‑rellenable desde el saludo? | Fuente |
|----------------|----------------------------------|--------|
| `relation` (para mí / otra persona) | ✅ Sí | "para mi hijo", "es para mí" |
| `patient_name` | ✅ Sí (si lo dio o viene en contexto) | texto libre / `nombre_registrado` / `nombre_whatsapp` |
| `specialty` / motivo | ✅ Parcial (mapear molestia→especialidad con `get_services`+`get_specialties`) | "me duele una muela", "limpieza" |
| `doctor_name` | ❌ No (requiere elegir de la agenda real) | — |
| `date` / `time` | ❌ No (depende de slots reales) | — |
| `ci` (cédula) | ❌ No (dato privado; lo tipea el paciente) | — |

### 5.3 Cambio propuesto en el agente (pieza 3)

`app/core/langgraph/tools/whatsapp_flows.py` — extender el tool con un `prefill` opcional:

```diff
 @tool
 async def enviar_formulario(
     flow: str,
+    prefill: Optional[dict] = None,   # {relation, patient_name, specialty} — opcional
     conversation_id: Annotated[Optional[int], InjectedToolArg] = None,
 ) -> str:
     ...
-        resp = await _post_flow(conversation_id, flow)
+        resp = await _post_flow(conversation_id, flow, prefill)
```

Y en `_post_flow`, incluir `prefill` en el body solo si viene:

```diff
-        resp = await client.post(url, json={"flow": flow}, headers=headers)
+        body = {"flow": flow}
+        if prefill:
+            body["prefill"] = prefill
+        resp = await client.post(url, json=body, headers=headers)
```

En el prompt (`master/odontoking.md`), extender el enrutamiento de agendar:

```
- Al enviar enviar_formulario para agendar, si en el saludo el paciente YA indicó para quién
  (para él o para otra persona, con nombre si lo dio) y/o qué molestia/servicio necesita, pásalo
  en `prefill` { "relation": "...", "patient_name": "...", "specialty": "..." }.
  Solo incluye lo que el paciente ya dijo con claridad; NO inventes ni rellenes campos dudosos.
  El carnet, el doctor y el horario SIEMPRE los elige el paciente en el formulario.
```

### 5.4 Degradación segura

Si el CRM aún no soporta `prefill`, el agente puede enviarlo igual: el endpoint debe **ignorar** las claves desconocidas y enviar el formulario vacío como hoy (backward‑compatible). Confirmar ese contrato con el CRM.

---

## 6. Mejora 3 — Desambiguación "cancelar" vs "reprogramar"

**Problema detectado en conversación real:** el paciente escribe *"Quiero cancelar la cita y moverla
para mañana por favor"*. Contiene la palabra "cancelar", pero la intención real es **reprogramar**
(mantiene la cita, solo cambia la fecha/hora). El ruteo por keyword lo mandaba a `flow="cancelar_cita"`
(por el match de "cancelar") → la cita se eliminaría en vez de moverse.

**Regla (aplicada en el prompt):** guiarse por el RESULTADO que pide el paciente, no por la palabra.

- MOVER/PASAR/CAMBIAR la MISMA cita a otra fecha/hora —aunque diga "cancelar"— → `reprogramar_cita`.
  Ej.: "cancele la cita y muévala para mañana", "cancélela y pásela al viernes", "cámbiela a otra hora".
- Solo `cancelar_cita` cuando quiere ELIMINARLA SIN nueva fecha ("ya no quiero", "anúlela", "ya no puedo ir").
- Cancelar para agendar con OTRO doctor/servicio → `cancelar_cita` y luego `paciente_tercero`.

**Dónde:** `app/core/prompts/odontoking.md` — línea de enrutamiento reprogramar/cancelar + bloque
"AGENDAR, CANCELAR Y REPROGRAMAR CITAS" (regla DESAMBIGUACIÓN). Solo prompt, sin cambio de código.

---

## 7. Riesgos y decisiones abiertas

1. **Destino del cambio:** producción es `master`; el trabajo actual está en `sensia`. Definir si se aplica sobre `master` o un branch desde `master`.
2. **Confirmación de citas (turnos 1–4):** verificar en el CRM que la confirmación "✅" realmente se envía tras `status: pending`.
3. **Pre‑llenado:** confirmar con CRM que (a) los Flows aceptan datos iniciales y (b) el endpoint `.../flows` acepta `prefill`. Sin esto, la Mejora 2 no avanza.
4. **Persistencia del seguro:** decidir si, a futuro, una validación `VIGENTE` debe asociarse a la cita (hoy no; requeriría `save_insurance` o que el form capture la aseguradora).

---

## 8. Checklist de implementación

**Mejora 1 (bug de seguro) — solo este repo:** ✅ APLICADO en branch `fix/odontoking-seguro-reactivo`
- [x] Parche A: agregar `verify_insurance` a `_FORMS_TOOLS`.
- [x] Parche B: declarar el tool en el prompt.
- [x] Parche C: bloque "SEGURO Y MEMBRESÍA" reactivo.
- [x] Parche D: acotar la regla de precios.
- [ ] (Opcional) Parche E: pre‑llenar carnet registrado en modo forms. (no aplicado)
- [x] `make check`: ruff ✓ + pyright ✓ en el archivo tocado; 20/20 tests (odontoking/insurance/flows).
- [ ] Tests nuevos: caso "tengo membresía" → verify → VIGENTE / no‑vigente / Vitalia→handoff. (pendiente)

**Mejora 3 (desambiguación cancelar vs reprogramar) — solo prompt:** ✅ APLICADO
- [x] Regla DESAMBIGUACIÓN en enrutamiento + bloque AGENDAR/CANCELAR/REPROGRAMAR.
- [x] `.format()` del prompt validado.
- [ ] Test nuevo: "cancelar y moverla para mañana" → `reprogramar_cita` (no `cancelar_cita`). (pendiente)

**Mejora 2 (pre‑llenado) — lado agente APLICADO; R2 (publicar Flow) PENDIENTE del CRM:**
- [x] CRM confirmó: endpoint acepta `prefill` y las claves desconocidas no dan 4xx (R1/R5).
- [x] CRM confirmó valores: `relation` self|tercero (+alias), `patient_name` texto, `relationship`
  hijo|padre_madre|pareja|hermano|familiar|otro, `specialty` id/nombre del CRM o general/No sé (R3,R4,R6).
- [ ] CRM: **publicar** la nueva versión de `paciente_tercero` con campos ligados (R2) — PENDIENTE.
- [x] Agente: `prefill` opcional (relation/patient_name/relationship/specialty) en `enviar_formulario`
  + regla PRE‑LLENADO en el prompt (solo paciente_tercero; nunca inventa; omite lo dudoso).
- [x] Tests: `prefill` se envía solo en paciente_tercero (canonicalizado, vacíos fuera); ignorado en
  cancelar/reprogramar; schema LLM expone flow + params de prefill, no conversation_id.
- [ ] Afinar mapeo servicio→especialidad (hoy: runtime con get_services/get_specialties + fallback
  "general"); si el CRM nos pasa el catálogo de `specialty`, armamos tabla fija.

> Nota: por R5, el lado del agente es inocuo hasta que el CRM publique el Flow (R2); al publicarlo,
> el pre‑llenado empieza a verse sin más cambios de nuestro lado.
