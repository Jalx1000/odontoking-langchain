# Tareas: optimización de tokens (Kohlberg) — orden anti-regresiones

> Cada tarea = un commit. No avanzar sin pasar su verificación. Detalle en `tasks/plan.md`.

## Fase 0 — Baseline (oráculo)

- [x] Task: Script `scripts/token_report.py` (conteo determinista con tiktoken) ✅ baseline: prefijo estático 8,437 tok
  - Acceptance: imprime tokens del system prompt + cada tool schema + total del prefijo,
    tal como se ensamblan hoy; corre sin llamar a OpenAI.
  - Verify: `python scripts/token_report.py` imprime el desglose; guardar el total base.
  - Files: `scripts/token_report.py`

- [x] Task: Charla dorada de referencia ✅ guion+invariantes en tasks/golden-conversation.md (transcripción se captura al correr con crédito)
  - Acceptance: `tasks/golden-conversation.md` con el guion fijo y las respuestas de
    referencia (saludo→menú ciudad→nombre→edad→2 vinos→confirmar→paso6/7 + derivación +
    horarios).
  - Verify: transcripción completa guardada; invariantes marcados (menús `N.-)`, total en
    paso 6, sucursal en paso 7, mensaje de derivación).
  - Files: `tasks/golden-conversation.md`

## Fase 0.5 — Red de seguridad (antes de tocar el prompt)

- [x] Task: S1 — Log de tokens por turno ✅ `_log_llm_token_usage` → evento `kohlberg_llm_tokens`
  - Acceptance: evento `kohlberg_llm_tokens` en `_chat` con input/output/cache_read de
    `usage_metadata`.
  - Verify: el evento aparece en los logs tras una charla; `make check` verde.
  - Files: `app/core/langgraph/kohlberg_graph.py`

- [x] Task: S2 — Tests de invariantes de Kohlberg ✅ tests/unit/test_kohlberg_invariants.py (3 pasan; prefijo-estable va en C1)
  - Acceptance: pytest con contrato de prompt, estabilidad del prefijo, set de tools, y
    presupuesto de tokens (token_report exit≠0 sobre umbral).
  - Verify: `uv run pytest tests/.../test_kohlberg_invariants.py` verde; los tests
    fallan si se quita un marcador o se mete un valor dinámico arriba del prompt.
  - Files: `tests/` (nuevo), `scripts/token_report.py`

- [x] Task: S3 — Bajar recursion_limit + capturar GraphRecursionError ✅ 50→15 (el catch ya existía)
  - Acceptance: `recursion_limit` ~10-15 en `kohlberg_graph.py`; `GraphRecursionError`
    capturado con respuesta amable + log `kohlberg_recursion_limit_hit`.
  - Verify: `make check` verde; charla dorada normal no lo dispara.
  - Files: `app/core/langgraph/kohlberg_graph.py`

## Fase 1 — C1 Restaurar caching (riesgo nulo)

- [x] Task: Mover datetime + contexto al final del SystemMessage ✅ prefijo estático ahora cacheable + test_prefix_is_stable
  - Acceptance: `kohlberg.md` sin `{current_datetime}` en el cuerpo; `_load_kohlberg_prompt`
    concatena cuerpo estático + bloque volátil (datetime + contacto) al final.
  - Verify: `token_report` total ≈ igual; charla dorada equivalente; horarios respetados.
  - Files: `app/core/prompts/kohlberg.md`, `app/core/langgraph/kohlberg_graph.py`

## Fase 2 — C6 Cap de salida (riesgo bajo)

- [x] Task: Bajar `max_tokens` a techo seguro ✅ 4096→1536
  - Acceptance: `max_tokens` = 1024 (o 1536 si la respuesta más larga no entra en 1024).
  - Verify: la respuesta más larga de la charla dorada (promos/catálogo) no se trunca.
  - Files: `app/core/langgraph/kohlberg_graph.py`

## Fase 3 — C3 Recortar docstrings de tools (riesgo bajo-medio)

- [x] Task: Docstrings concisos conservando semántica de args ✅ tools 2359→1949 tok (get_promos/get_sucursales/derivar/actualizar)
  - Acceptance: docstrings de `actualizar_pedido`, `derivar_a_asesor`, `get_promos` (y
    resto) recortados; total tools ~700 tok; args y "cuándo llamar" intactos.
  - Verify: cada tool dispara en el momento correcto en la charla dorada; `token_report`
    baja; `make check` verde.
  - Files: `app/core/langgraph/tools/kohlberg.py`

## Fase 4 — C4 Trimming de historial (riesgo medio)

- [x] Task: `trim_messages` en `_chat` con presupuesto generoso ✅ 4000 tok, start_on=human, +2 tests
  - Acceptance: trim con ~20 msgs / ~4k tok, `start_on="human"`, preserva SystemMessage y
    pares tool/ToolMessage; charlas típicas NO se recortan.
  - Verify: charla dorada intacta; charla larga sintética no rompe la API ni re-pregunta
    datos recientes; `make check` verde.
  - Files: `app/core/langgraph/kohlberg_graph.py`

## Fase 5 — C5 Híbrido + quitar think (riesgo medio, conductual)

- [x] Task: Quitar `think` de las tools y del prompt ✅ 7 tools; test asserta think ausente
  - Acceptance: `think` fuera de `_KOHLBERG_TOOLS`; sin menciones en `kohlberg.md`.
  - Verify: agente inicializa con 7 tools; charla dorada equivalente; `make check` verde.
  - Files: `app/core/langgraph/kohlberg_graph.py`, `app/core/prompts/kohlberg.md`

- [x] Task: Cadencia híbrida de `actualizar_pedido` ✅ prompt + docstring: PUT en ciudad y cada vino; nombre/edad de paso
  - Acceptance: prompt ("Registro en vivo") + docstring instruyen PUT al conocer ciudad y
    al agregar cada vino; NO por nombre/edad sueltos.
  - Verify: en la charla dorada, PUT de ciudad saca el lead de Tarija/Daniel; cada vino
    re-PUT con lista completa; nombre/edad quedan en el PUT siguiente; confirmar → No
    atendido; baja el nº de llamadas LLM.
  - Files: `app/core/prompts/kohlberg.md`, `app/core/langgraph/tools/kohlberg.py`

## Fase 6 — C2 Recortar prompt conservador (riesgo alto → último)

- [x] Task: Recorte conservador del system prompt ✅ 3859→3663 palabras (solo duplicación); budget test 24k
  - Acceptance: solo se quita redundancia/duplicación evidente; TODA regla de conducta
    conservada; ~2,800 palabras.
  - Verify: charla dorada equivalente en TODOS los invariantes; `token_report` baja;
    `make check` + `pytest` verdes.
  - Files: `app/core/prompts/kohlberg.md`

## Fase 7 — Docs y cierre

- [x] Task: Actualizar docs de cadencia (~3 PUT, no 5-6) ✅ 429 doc + lead-incremental + reporte en spec-optimizacion
  - Acceptance: `para-crm-throttle-429.md` y `spec-kohlberg-lead-incremental.md` reflejan
    el híbrido; reporte antes/después de `token_report` en el spec.
  - Verify: docs coherentes; usuario aprueba antes de evaluar despliegue.
  - Files: `docs/para-crm-throttle-429.md`, `docs/spec-kohlberg-lead-incremental.md`,
    `docs/spec-kohlberg-optimizacion-tokens.md`
