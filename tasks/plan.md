# Plan de implementación: optimización de tokens (Kohlberg) — anti-regresiones

> Spec: `docs/spec-kohlberg-optimizacion-tokens.md`. Rama `kohlberg-v2`. Modelo fijo gpt-4o-mini.
> Principio rector: **seguridad ante regresiones sobre velocidad**.

## Principios anti-regresión (aplican a TODO el plan)

1. **Baseline primero.** Antes de tocar nada, capturar el oráculo: (a) conteo de tokens
   determinista del prompt+tools ensamblados (tiktoken) y (b) transcripción de una
   **charla dorada** end-to-end. Todo cambio se compara contra esto.
2. **Un cambio = un commit.** Cada C# va en su propio commit atómico y reversible, con
   su checkpoint de verificación. Si algo regresiona, `git revert` de UN commit lo saca.
3. **Del más seguro al más riesgoso.** Primero cambios mecánicos sin pérdida de
   contenido; el recorte de prompt (mayor riesgo) va **último**.
4. **Gate por checkpoint.** No avanzo al siguiente C# hasta que el actual pase su
   verificación (charla dorada equivalente + `make check` + tests).
5. **Equivalencia semántica, no textual.** El LLM no es determinista; se verifican
   **invariantes estructurales** (menús `N.-)`, ciudad-primero, paso 6 con total, paso 7
   con sucursal, mensaje de derivación, PUT de ciudad y por vino), no texto exacto.
6. **Nada se despliega** hasta que la charla dorada post-cambios sea equivalente a la
   baseline y el usuario lo apruebe. El despliegue del híbrido sigue supeditado a que el
   fix de `LeadRepository::update` del CRM esté vivo (ver spec del lead incremental).

## Orden de implementación y checkpoints

### Fase 0 — Baseline (oráculo de regresión) · sin cambios de comportamiento
- Script `scripts/token_report.py`: ensambla el system prompt + schemas de tools tal
  como se envían y cuenta tokens con `tiktoken` (modelo gpt-4o-mini). Imprime desglose
  (prompt / cada tool / total prefijo). Es la métrica determinista antes/después.
- Charla dorada: guion fijo (saludo → menú ciudad → nombre → edad → 2 vinos → confirmar
  → paso 6/7; + una rama de derivación; + una pregunta de horarios). Guardar la
  transcripción de referencia en `tasks/golden-conversation.md`.
- **Checkpoint 0:** tenemos número de tokens base + transcripción de referencia.

### Fase 0.5 — Red de seguridad (va ANTES de tocar el prompt) · riesgo BAJO
Convierte el oráculo manual en garantías automáticas y tapa el runaway de costo.
- **S1 — Log de tokens por turno:** evento structlog `kohlberg_llm_tokens` leyendo
  `response_message.usage_metadata` (input / output / **cache_read**) en `_chat`. Hace
  el costo visible en Railway (sin Langfuse) y confirma que el caching de C1 pega.
- **S2 — Tests de invariantes (pytest, primeros tests de Kohlberg):**
  - *Contrato de prompt:* marcadores de conducta obligatorios presentes (menús `N.-)`,
    ciudad-primero, total en paso 6, sucursal en paso 7, derivación, horarios) → falla
    si C2 borra alguno.
  - *Estabilidad del prefijo:* `_load_kohlberg_prompt` byte-idéntico hasta el bloque
    volátil en dos timestamps → blinda el caching de C1 contra regresiones futuras.
  - *Set de tools:* nombres esperados (verifica que `think` quedó fuera; 7 tools).
  - *Presupuesto:* `token_report` sale con código ≠0 si el prefijo supera un umbral.
- **S3 — `recursion_limit` a ~10-15 + capturar `GraphRecursionError`** (respuesta
  amable, no error crudo). Tapa el peor caso de costo por loop del ReAct (hoy 50).
- **Checkpoint 0.5:** tests verdes; `kohlberg_llm_tokens` aparece en logs; loguea/limita
  el recursion; `make check` verde. Esta red queda activa para TODAS las fases siguientes.

### Fase 1 — C1 Restaurar caching · riesgo NULO (solo reordena)
- Mover `{current_datetime}` (y la 2ª aparición) fuera del cuerpo estático; el
  `_load_kohlberg_prompt` emite `CUERPO_ESTÁTICO + "\n\n" + bloque_volátil` (datetime +
  contexto de contacto) al final del SystemMessage.
- **Checkpoint 1:** `token_report` sin cambio de total; charla dorada equivalente;
  horarios siguen respetándose (el datetime al final sigue siendo leído). El beneficio
  (cache) se confirma en Langfuse post-deploy (`cached_tokens > 0`).

### Fase 2 — C6 Cap de salida · riesgo BAJO
- Bajar `max_tokens` (kohlberg_graph.py:140) de 4096 a un techo seguro. **Verificar
  primero** que la respuesta más larga esperada (lista de promos/catálogo) entra en el
  techo; elegir 1024 solo si entra, si no 1536.
- **Checkpoint 2:** la respuesta más larga de la charla dorada no se trunca.

### Fase 3 — C3 Recortar docstrings de tools · riesgo BAJO-MEDIO
- Recortar prosa de docstrings conservando semántica de args y el "cuándo llamar".
  Prioridad: `actualizar_pedido`, `derivar_a_asesor`, `get_promos`.
- **Checkpoint 3:** cada tool sigue disparándose en el momento correcto en la charla
  dorada; `token_report` baja; `make check` verde.

### Fase 4 — C4 Trimming de historial · riesgo MEDIO
- Añadir `trim_messages` en `_chat` antes del `ainvoke`, con presupuesto **generoso**
  (p. ej. ~20 mensajes / ~4k tok) para que charlas típicas **no se recorten**; solo
  actúa en charlas largas. Preservar `SystemMessage`, empezar en `human`, mantener
  pares `tool_call`/`ToolMessage` consistentes.
- **Checkpoint 4:** charla dorada intacta (no se recorta); charla larga sintética no
  rompe la API (pares consistentes) y no re-pregunta datos recientes.

### Fase 5 — C5 Híbrido + quitar `think` · riesgo MEDIO (conductual)
- Quitar `think` de `_KOHLBERG_TOOLS` y sus menciones en el prompt.
- Ajustar prompt ("Registro en vivo") + docstring de `actualizar_pedido`: PUT **al
  conocer ciudad** y **al agregar cada vino**; **no** por nombre/edad sueltos (viajan
  con el próximo PUT). `registrar_pedido` = confirmar/cancelar final.
- **Checkpoint 5:** en la charla dorada, el lead sale de Tarija/Daniel al decir ciudad
  (PUT ciudad), cada vino re-PUT con lista completa, nombre/edad quedan en el PUT
  siguiente; confirmar deja el pedido en No atendido. Total de llamadas LLM baja.

### Fase 6 — C2 Recortar prompt (conservador ~2,800 palabras) · riesgo ALTO → ÚLTIMO
- Solo eliminar redundancia/duplicación evidente; conservar TODA regla de conducta.
  Diff-review bloque por bloque. Objetivo ~2,800 palabras (no ~2,000).
- **Checkpoint 6:** charla dorada equivalente en TODOS los invariantes; `token_report`
  refleja la baja; `make check` + tests verdes.

### Fase 7 — Docs y cierre
- Actualizar `docs/para-crm-throttle-429.md` y `docs/spec-kohlberg-lead-incremental.md`:
  cadencia ~3 PUT (no 5-6) por el híbrido.
- Reporte antes/después de `token_report` en el spec.
- **Checkpoint 7:** docs coherentes; usuario aprueba; recién ahí se evalúa desplegar.

## Riesgos y mitigación

| Riesgo | Fase | Mitigación |
|---|---|---|
| Recorte de prompt cambia conducta | 6 | Último, conservador, diff-review, charla dorada |
| `trim_messages` rompe pares tool/ToolMessage | 4 | `start_on="human"`, preservar system, presupuesto generoso |
| Cap de salida trunca respuesta larga | 2 | Verificar la respuesta más larga antes de fijar el techo |
| Híbrido pierde un dato al abandonar | 5 | Ciudad y vinos sí se PUT; nombre/edad viajan con el próximo PUT |
| Mover datetime afecta lógica de horarios | 1 | Bloque datetime claro y etiquetado al final; test de horarios |
| No hay Langfuse para medir tokens | todas | `token_report` (tiktoken) es determinista y local |

## Rollback
Cada fase es un commit. Regresión detectada → `git revert <commit>` de esa fase; las
demás quedan. La Fase 0 (baseline) no cambia comportamiento y es permanente (herramienta).
