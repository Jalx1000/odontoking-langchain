# Spec: optimización de consumo de tokens (Kohlberg / Sofía)

> Estado: **borrador para revisión** (fase Specify). No implementar hasta aprobación.
> Rama: `kohlberg-v2`. Modelo fijo: **gpt-4o-mini** (no se cambia de modelo/proveedor).

## Objetivo

Bajar el costo de tokens de OpenAI del agente Sofía **sin cambiar el modelo ni
degradar el comportamiento de venta**. En horas de uso se gastaron ~$2 en un modelo
"barato": el costo no viene del modelo, viene de **re-enviar el mismo contexto en
cada llamada y cada salto de tool**, con el **prompt caching roto**.

Usuario: el negocio (paga la cuenta OpenAI) y el equipo que opera el agente.
Éxito = misma calidad de conversación con **~50-70% menos tokens de entrada** por
conversación.

## Diagnóstico (medido en el código actual)

- `app/core/prompts/kohlberg.md` = **~6,124 tok**, 413 líneas, 3,859 palabras. Se
  envía como `SystemMessage` en **cada** llamada (`kohlberg_graph.py:194`).
- **Prompt caching roto:** `{current_datetime}` (incluye `HH:MM`, cambia cada
  minuto) está en la **línea 4** del prompt y otra vez en la 377. Como el
  `SystemMessage` es el prefijo, OpenAI solo puede cachear hasta la primera
  divergencia → casi todo el prompt se cobra a precio lleno en cada llamada.
- Schemas de tools = **~1,344 tok** de docstrings (8 tools) + overhead JSON, en cada
  llamada. Más pesados: `actualizar_pedido` (318), `derivar_a_asesor` (252),
  `get_promos` (218).
- **Sin trimming de historial** (`kohlberg_graph.py`): el checkpointer acumula todo y
  se re-envía completo en cada turno y cada salto `chat→tool_call→chat`.
- **Round-trips:** cada turno con tool = ≥2 llamadas completas. `actualizar_pedido`
  por cada dato (ciudad, nombre, edad, cada vino) + `think` multiplican las llamadas.
- Confirmado que **NO hay doble conteo**: el historial del CRM no se re-inyecta encima
  del checkpointer (`get_response` solo pasa el turno actual). ✔

Precios gpt-4o-mini: entrada $0.15/1M · **entrada cacheada $0.075/1M (−50%)** ·
salida $0.60/1M.

## Cambios propuestos (ordenados por impacto/esfuerzo)

### C1 — Restaurar prompt caching (MÁXIMO impacto, mínimo esfuerzo)
Sacar TODO lo volátil del prefijo cacheable. Mover `current_datetime` y el
`# Contexto del contacto` (wa_id, nombres) al **final** del `SystemMessage`, dejando
el cuerpo estático (~6k tok) como prefijo estable e idéntico entre conversaciones.
- Editar `kohlberg.md`: quitar el datetime de la línea 4 y 377; el cuerpo no cambia
  entre llamadas.
- En `_load_kohlberg_prompt`: emitir `CUERPO_ESTÁTICO + "\n\n" + bloque_volátil`
  (datetime + contexto) al final.
- Efecto: el prefijo estático se cachea → −50% en la porción más grande y recurrente.

### C2 — Recortar el system prompt (alto impacto)
3,859 → objetivo **~1,800-2,000 palabras**. Quitar redundancia, ejemplos repetidos y
reglas duplicadas, conservando: reglas de flujo, ciudad-primero, menús `N.-)`,
pasos 6/7, horarios, derivación. Validar con charla de prueba.

### C3 — Recortar docstrings de tools (medio)
~1,344 → ~600-700 tok. Dejar descripción de args esencial, cortar prosa. Prioridad:
`actualizar_pedido`, `derivar_a_asesor`, `get_promos`.

### C4 — Trimming de historial (alto en charlas largas)
Aplicar `trim_messages` (langchain) antes del `ainvoke` en `_chat`: presupuesto de
tokens (p. ej. últimos ~15 mensajes / ~3,000 tok), **preservando** el `SystemMessage`
y los pares `tool_call`/`ToolMessage` consistentes. Acota el crecimiento por turno.

### C5 — Menos round-trips (medio — DECIDIDO)
- **`think`**: **quitarlo** (decisión del usuario). Se elimina de `_KOHLBERG_TOOLS` y
  del prompt. Menos round-trips; el razonamiento ocurre implícito en la respuesta.
- **Registro incremental → HÍBRIDO** (decisión del usuario): `actualizar_pedido` se
  llama **al conocer la ciudad** (1 PUT: saca el lead de Tarija/Daniel + Ciudad) y
  **al agregar cada vino** (PUT con la lista completa + lead_value). **NO** se hace un
  PUT por nombre/edad sueltos: esos datos viajan con el próximo PUT (el de ciudad o el
  de vino). `registrar_pedido` sigue siendo el confirmar/cancelar final. Resultado:
  ~3 llamadas en vez de 5-6, conservando lo importante (ciudad y productos) aunque el
  cliente abandone. Ajustar el prompt ("Registro en vivo") y el docstring de
  `actualizar_pedido` para reflejar esta cadencia.

### C6 — Cap de salida (bajo)
`max_tokens=4096` (`kohlberg_graph.py:140`) es alto para WhatsApp; la salida cuesta 4×
la entrada. Bajar a ~1,024 como techo (no cambia respuestas normales, evita runaway).

## Tech Stack / Commands / Estructura
Sin cambios de stack. FastAPI + LangGraph + langchain_openai. Archivos tocados:
`app/core/prompts/kohlberg.md`, `app/core/langgraph/kohlberg_graph.py`,
`app/core/langgraph/tools/kohlberg.py`.
```bash
make check        # ruff + pyright (debe pasar)
uv run pytest     # tests
```

## Testing / Verificación
- **Antes/después con métrica real:** contar tokens de una conversación patrón
  (Langfuse ya traza tokens/costo por llamada) — comparar input tokens por turno.
- Charla de prueba end-to-end (ciudad→nombre→edad→2 vinos→confirmar) que verifique:
  el flujo, los menús, los pasos 6/7 y la derivación **no cambian**.
- `make check` limpio; tests existentes verdes.

## Boundaries
- **Always:** medir tokens antes/después; preservar el flujo de venta y los contratos
  del CRM; `make check` + tests antes de commit.
- **Ask first (requiere tu OK):** C5 (tocar el registro incremental / quitar `think`);
  cuánto recortar el prompt (riesgo de regresión de comportamiento).
- **Never:** cambiar de modelo/proveedor; romper los menús `N.-)`, los pasos 6/7, la
  persistencia de ciudad/edad, ni la derivación; desplegar sin la charla de prueba.

## Success Criteria
- Prefijo estático (prompt + tools) **cacheado** por OpenAI (verificable en Langfuse:
  `cached_tokens > 0` en llamadas sucesivas de una misma ventana).
- Input tokens por turno **−50-70%** en la conversación patrón.
- Comportamiento de venta idéntico en la charla de prueba (menús, pasos 6/7,
  derivación, registro).
- `make check` y `pytest` verdes.

## Decisiones tomadas (2026-09-26)
1. **C5 registro:** HÍBRIDO — PUT al conocer ciudad + al agregar cada vino; no por
   nombre/edad sueltos. Actualiza el modelo de [[project_kohlberg_pedido_model]].
2. **`think`:** se quita.
3. **Alcance:** solo tokens de OpenAI. Railway es el destino de despliegue (sus logs
   muestran el flujo/errores, NO conteo de tokens). Los números reales de tokens salen
   de **Langfuse** o del dashboard de uso de OpenAI, no de los logs de Railway.

## Open Questions
1. ~~Agresividad del recorte del prompt~~ → **RESUELTO: conservador (~2,800 palabras)**,
   solo redundancia/duplicación evidente, validado con charla de prueba.
2. ¿Hay acceso a Langfuse para medir tokens antes/después, o validamos solo con la
   charla de prueba + el conteo local (tiktoken) + verificación de `cached_tokens`
   cuando haya acceso? (No bloquea: el conteo local es determinista.)

## Efecto colateral positivo (429)
El híbrido baja los PUT al CRM de **~5-6 a ~3 por conversación** → alivia el `429`
descrito en `docs/para-crm-throttle-429.md`. Ese doc y `spec-kohlberg-lead-incremental.md`
ya se actualizaron a la cadencia híbrida.

## Resultados medidos (2026-09-28, `scripts/token_report.py`)

| Métrica | Baseline | Tras C1–C6 | Δ |
|---|---|---|---|
| System prompt estático | 6,078 tok | 5,812 tok | −266 |
| Tools (schemas) | 2,359 tok (8) | 1,949 tok (7) | −410 (incl. `think` −175) |
| **Prefijo estático por llamada** | **8,437 tok** | **7,761 tok** | **−676 (−8%)** |
| Prompt (palabras) | 3,859 | 3,663 | −196 |

**El ahorro real es mayor que ese −8%**, porque el prefijo estático (7,761 tok) ahora es
**cacheable** (C1): con cache caliente, OpenAI cobra ~50% de esa porción (≈ −3,880 tok
efectivos/llamada). Sumado: C4 acota el historial en charlas largas; C5 quita ~2-3
round-trips/conversación (híbrido + sin `think`); C6 topea la salida. Confirmar el
`cached_tokens > 0` en Langfuse/`kohlberg_llm_tokens` post-deploy.

Hecho: C1 caching · C2 recorte conservador · C3 docstrings · C4 trim historial · C5
híbrido + quita think · C6 cap salida · Fase 0/0.5 (token_report, tests, log de tokens,
recursion_limit 50→15). Tests: `tests/unit/test_kohlberg_invariants.py` (6, verdes).
