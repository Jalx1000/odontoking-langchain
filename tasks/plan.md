# Plan técnico — Agentes data-driven (prompt por cliente vía API)

> Fase 2 del flujo spec-driven. Spec: `planning/11-agentes-data-driven-SPEC.md`.
> Tareas ejecutables: `tasks/todo.md`.
> Decisiones: tabla versionada; Swagger-only (sin front); `/validate` bloquea solo ante error de
> render real; **aislamiento de historial (thread_id) APARCADO**.

## Componentes y dependencias

```
                 ┌─────────────────┐
   (Fase 1)      │  tool_packs.py  │  ToolPack: tools + render_context + build_metadata
                 └────────┬────────┘
                          │
                 ┌────────▼────────┐
                 │  base_agent.py  │  WhatsAppAgent (chasis único) ← reemplaza *_graph.py
                 └────────┬────────┘
                          │ (call sites: whatsapp/crm/worker/admin.conversations)
   ──────────────────────┼────────────────────────────────────────────────────────
   (Fase 2)      ┌────────▼────────┐      ┌──────────────────┐
                 │ tenant_prompt   │─────▶│  migración+seed  │ (imprimir.md/c21.md → v1 activa)
                 │ (modelo SQL)    │      └──────────────────┘
                 └────────┬────────┘
                 ┌────────▼────────┐
                 │ prompt_loader   │  get_active_prompt(slug): Redis → DB → .md
                 └────────┬────────┘
   ──────────────────────┼────────────────────────────────────────────────────────
   (Fase 3)      ┌────────▼────────┐  _chat lee prompt dinámico; slug viaja por get_response
                 │ wiring chasis   │
                 └────────┬────────┘
   ──────────────────────┼────────────────────────────────────────────────────────
   (Fase 4)      ┌────────▼────────┐  /admin/tenants/{slug}/prompts (Swagger, require_admin)
                 │  prompts API    │
                 └─────────────────┘
```

## Orden de implementación (por dependencia)

1. **Fase 1 — Chasis (equivalencia funcional, sin cambio de comportamiento).**
   `tool_packs.py` → `base_agent.py` → actualizar call sites → borrar `imprimir_graph.py`/`c21_graph.py`.
   El prompt sigue viniendo del `.md` vía `pack.default_prompt_file`. De paso se unifica el **dedup de
   tool-calls** (hoy solo en c21). Checkpoint de verificación: toda la suite verde + app arranca +
   respuestas equivalentes.

2. **Fase 2 — Almacenamiento del prompt.**
   Modelo `tenant_prompts` + migración + **seed** desde los `.md` actuales → `prompt_loader.py`.
   `tenant_slug` es string indexado **sin FK dura** a `tenants` (odontoking vive en env-fallback, no en
   la tabla). Checkpoint: migración up/down limpia; loader cubre sus 3 ramas.

3. **Fase 3 — Wiring del prompt dinámico.**
   `_chat` usa `get_active_prompt(slug, pack.default_prompt_file)`; `tenant_slug` viaja por
   `get_response(...)` desde los call sites. Checkpoint: sin filas → comportamiento idéntico (fallback
   `.md`); con fila activa → usa ese prompt sin reiniciar.

4. **Fase 4 — API de gestión (Swagger).**
   `prompts.py`: POST subir / GET listar / GET {version} / GET active / POST activate / POST validate.
   `require_admin`, documentado (summary/description/responses/examples). Invalida caché en escrituras.

## Paralelo vs secuencial

- **Secuencial**: Fase 1 → 3 → 4 (cada una depende de la anterior).
- **Paralelizable**: Fase 2 (modelo + migración + loader) puede construirse en paralelo a la Fase 1,
  porque no depende del chasis; solo se juntan en la Fase 3.
- Dentro de Fase 1: `tool_packs.py` y `base_agent.py` son secuenciales; la actualización de los 4 call
  sites puede hacerse en paralelo una vez exista `base_agent.py`.

## Riesgos y mitigación

| Riesgo | Mitigación |
|---|---|
| Regresión al fusionar los dos grafos | Fase 1 es *solo* refactor: diff de comportamiento con tests de no-regresión antes de borrar los `.py` viejos. |
| `str.format` revienta con llaves literales (`{precio}`) | Prohibido por boundary; solo `.replace`; test que renderiza un prompt con `{…}` literal. |
| Seed con tenant no presente en tabla (odontoking es env) | `tenant_slug` sin FK dura; seed idempotente que no asume fila en `tenants`. |
| Caché sirve prompt viejo tras subir | Invalidar `tenant:prompt:{slug}` en toda escritura; test end-to-end upload→mensaje. |
| Prompt inyectado por usuario | `require_admin` en todos los endpoints; documentado. |

## Aparcado (no en este alcance)

- **Aislamiento de historial** vía `thread_id = {slug}:{wa_id}` y la migración de checkpoints. Decidido
  posponer. Consecuencia: el aislamiento de *prompt* por slug SÍ se cumple; el de *historial* entre dos
  tenants con el mismo `wa_id` queda pendiente (criterio de éxito #3 parcialmente cubierto).
- `graph.py` (chatbot streaming) — fuera de alcance.
- `agent_type` configurable por API (rubro nuevo sigue siendo código + deploy).

## Checkpoints de verificación entre fases

- **Tras Fase 1**: `make check` + `uv run pytest` verdes; app arranca; imprimir y c21 responden igual.
- **Tras Fase 2**: `make migrate` / `make migrate-downgrade` limpios; tests unit del loader verdes.
- **Tras Fase 3**: test de no-regresión (sin filas) + test de override (con fila) verdes.
- **Tras Fase 4**: tests de API verdes; endpoints visibles y documentados en `/docs`.
