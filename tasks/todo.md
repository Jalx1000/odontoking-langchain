# TODO — Agentes data-driven (prompt por cliente vía API)

> Plan: `tasks/plan.md`. Spec: `planning/11-agentes-data-driven-SPEC.md`.
> Ejecutar una tarea a la vez, en orden. Verde (`make check` + tests) antes de pasar a la siguiente.

## Fase 1 — Chasis (equivalencia funcional, sin cambio de comportamiento)

- [ ] **T1 — `tool_packs.py` con `ToolPack` + IMPRIMIR_PACK y C21_PACK**
  - Acceptance: dataclass `ToolPack(agent_type, tools, render_context, build_metadata,
    on_conversation_start, handoff_tool_name, default_prompt_file)`; IMPRIMIR_PACK y C21_PACK exponen
    exactamente las tools de `_IMPRIMIR_TOOLS` / `_C21_TOOLS` actuales; `render_context` porta la lógica
    de `_load_imprimir_prompt` / `_load_c21_prompt` (incluye placeholders `{{OFICINA}}` etc. de c21 y el
    bloque de teléfono de imprimir); solo `.replace`, nunca `.format`.
  - Verify: `uv run python -c "from app.core.langgraph.tool_packs import IMPRIMIR_PACK, C21_PACK; print(len(IMPRIMIR_PACK.tools), len(C21_PACK.tools))"` → `7 8`; `uv run pyright`.
  - Files: `app/core/langgraph/tool_packs.py` (nuevo).

- [ ] **T2 — `base_agent.py` con `WhatsAppAgent(pack)`**
  - Acceptance: porta el chasis compartido (pool PG, checkpointer, `_chat`, `_tool_call` **con dedup**,
    `create_graph`, `get_response`, `close`, `clear_history`); instancia `imprimir_agent` y `c21_agent`;
    el `_tool_call` deduplica tool-calls idénticos (name+args) en un mismo mensaje.
  - Verify: unit test `test_tool_call_dedups_identical_calls`; `uv run python -c "from app.core.langgraph.base_agent import imprimir_agent, c21_agent"`.
  - Files: `app/core/langgraph/base_agent.py` (nuevo), `tests/unit/test_base_agent.py` (nuevo).

- [ ] **T3 — Actualizar call sites a `base_agent`**
  - Acceptance: `whatsapp.py`, `api/v1/crm.py`, `worker.py`, `admin/conversations.py` importan
    `imprimir_agent`/`c21_agent` desde `base_agent`; sin referencias a `imprimir_graph`/`c21_graph`.
  - Verify: `grep -rn "imprimir_graph\|c21_graph" app` vacío salvo los propios archivos; app arranca
    (`make dev` o import de `app.main`); `make check`.
  - Files: `app/api/v1/whatsapp.py`, `app/api/v1/crm.py`, `app/worker.py`, `app/api/admin/conversations.py`.

- [ ] **T4 — Borrar los grafos viejos (tras confirmar equivalencia)**
  - Acceptance: `imprimir_graph.py` y `c21_graph.py` eliminados; nada los importa.
  - Verify: `uv run pytest` verde; test de no-regresión de estructura de respuesta para imprimir y c21.
  - Files: borrar `app/core/langgraph/imprimir_graph.py`, `app/core/langgraph/c21_graph.py`.

## Fase 2 — Almacenamiento del prompt (paralelizable con Fase 1)

- [ ] **T5 — Modelo `tenant_prompts` + migración de esquema**
  - Acceptance: SQLModel `TenantPrompt(id, tenant_slug[index], version, content[text], is_active,
    note?, created_at, created_by?)`; `tenant_slug` string indexado **sin FK dura**; constraint lógica
    "una activa por tenant".
  - Verify: `make migration MSG="add tenant_prompts table"` + `make migrate` aplican limpio;
    `make migrate-downgrade` revierte sin error.
  - Files: `app/models/tenant_prompt.py` (nuevo), `alembic/versions/<hash>_add_tenant_prompts.py` (nuevo).

- [ ] **T6 — Seed de prompts v1 desde los `.md` actuales**
  - Acceptance: migración de datos idempotente que inserta `imprimir.md` → v1 activa (slug imprimir/
    odontoking) y `c21.md` → v1 activa (slug c21); no falla si el tenant no existe en la tabla `tenants`.
  - Verify: tras `make migrate`, consulta muestra 1 fila `is_active=True` por slug sembrado; re-ejecutar
    no duplica.
  - Files: `alembic/versions/<hash>_seed_tenant_prompts.py` (nuevo).

- [ ] **T7 — `prompt_loader.py` (Redis → DB → `.md`)**
  - Acceptance: `get_active_prompt(slug, *, default_file)` resuelve cache → DB activo → fallback `.md`;
    helpers `_prompt_cache_set/get` + invalidación bajo `tenant:prompt:{slug}` (mismo patrón que
    `TenantConfig`).
  - Verify: unit tests de las 3 ramas + invalidación (`test_prompt_loader.py`).
  - Files: `app/core/langgraph/prompt_loader.py` (nuevo), `tests/unit/test_prompt_loader.py` (nuevo),
    `app/core/tenant.py` (edit: helper de invalidación de prompt).

## Fase 3 — Wiring del prompt dinámico

- [ ] **T8 — `_chat` usa prompt dinámico; `tenant_slug` viaja por `get_response`**
  - Acceptance: `_chat` llama `get_active_prompt(slug, pack.default_prompt_file)` en vez del template
    estático; `get_response(..., tenant_slug=...)` y los call sites lo pasan; sin filas activas →
    comportamiento idéntico (fallback `.md`); con fila activa → usa ese prompt sin reiniciar servicio.
  - Verify: `test_no_regresion_sin_filas` (usa `.md`) + `test_override_con_fila` (usa DB); manual:
    subir prompt y ver que el siguiente mensaje cambia.
  - Files: `app/core/langgraph/base_agent.py`, `app/api/v1/whatsapp.py`, `app/api/v1/crm.py`,
    `app/worker.py`.

## Fase 4 — API de gestión (Swagger, sin front)

- [ ] **T9 — Router `/admin/tenants/{slug}/prompts`**
  - Acceptance: endpoints `POST /` (subir: content, note?, activate=false → version), `GET /` (listar
    metadata), `GET /{version}`, `GET /active`, `POST /{version}/activate`, `POST /validate` (dry-run;
    bloquea **solo** ante error de render real); todos con `require_admin`, `response_model`,
    `responses={404,409,422}` y ejemplos; cada escritura invalida caché.
  - Verify: tests de API por endpoint incl. 404/409/422; endpoints visibles en `/docs`.
  - Files: `app/api/admin/prompts.py` (nuevo), `app/api/admin/router.py` (registro),
    `tests/api/test_prompts.py` (nuevo).

- [ ] **T10 — Barrido de tests de criterios de éxito**
  - Acceptance: cubre criterio #2 (upload→cambia sin reiniciar), #4 (endpoints documentados), #6
    (`/validate` bloquea render roto); criterio #3 cubierto solo para *prompt* (historial aparcado).
  - Verify: `uv run pytest` verde completo; `make check` limpio.
  - Files: `tests/api/test_prompts.py`, `tests/unit/test_prompt_loader.py`, `tests/unit/test_base_agent.py`.

## Aparcado (no ejecutar en este alcance)

- Aislamiento de historial `thread_id = {slug}:{wa_id}` + migración de checkpoints.
- `graph.py` (chatbot streaming).
- `agent_type` configurable por API.
