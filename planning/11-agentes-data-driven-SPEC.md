# Spec: Agentes data-driven — prompt por cliente vía API (sin front)

> Estado: SPEC — Fase 1 del flujo spec-driven. Pendiente de revisión humana antes de PLAN.
> Fecha: 2026-10-09. Plan técnico asociado: `planning/11-agentes-data-driven-prompt-por-cliente.md`.

## Objetivo

Dar de alta un **cliente nuevo del mismo rubro** debe ser **solo subir un prompt por API**, sin tocar
código ni hacer deploy. Un rubro nuevo con acciones nuevas sigue necesitando su *tool pack* en código
(frontera irreducible: una tool es una integración HTTP, no se promptea).

- **Usuario**: operador admin de la plataforma (no el cliente final de WhatsApp).
- **Hoy**: un "agente" es una clase Python con el prompt horneado al importar
  (`imprimir_graph.py` lee `imprimir.md`); `ImprimirAgent` y `C21Agent` están ~90% duplicados.
- **Después**: un único chasis `WhatsAppAgent`; el `agent_type` del tenant elige el tool pack; el
  prompt se carga por request desde DB versionada, gestionable por Swagger.

### Criterios de éxito (reformulados, testeables)

1. Un admin crea un tenant del mismo rubro y lo pone a responder en WhatsApp **sin cambios de código
   ni deploy**, usando solo `/docs`.
2. Subir y activar una versión de prompt cambia el comportamiento del agente para ese tenant
   **sin reiniciar el servicio** (inmediato tras invalidación de caché; a lo sumo el TTL).
3. Dos tenants con el **mismo `wa_id` no comparten** ni historial ni prompt (aislamiento estanco).
4. Gestión completa de prompts (crear / listar / ver / activar / rollback / validar) disponible y
   **documentada en Swagger**; **no hay frontend**.
5. Tras el refactor, `imprimir` y `c21` responden **equivalente** a hoy (sin regresión).
6. Un prompt con placeholder roto se detecta en `/validate` **antes** de activarse (no rompe en vivo).

## Tech Stack

FastAPI · SQLModel · Alembic · PostgreSQL · Redis · LangGraph + langchain-openai · `uv` ·
ruff · pyright · pytest (asyncio_mode=auto). Secretos cifrados con Fernet (`app/services/encryption.py`).

## Commands

```
Instalar:        make install
Dev (infra+app): make dev
Lint:            uv run ruff check .
Format:          uv run ruff format .
Typecheck:       uv run pyright
Check (lint+tc): make check
Tests:           uv run pytest                  # todo
Tests rápidos:   uv run pytest -m "not slow"
Migración nueva: make migration MSG="add tenant_prompts table"
Aplicar migrac.: make migrate                   # uv run alembic upgrade head
Revertir 1:      make migrate-downgrade
```

## Project Structure

```
app/core/langgraph/
  base_agent.py     # NUEVO — WhatsAppAgent (chasis único): pool PG, checkpointer,
                    #         _chat, _tool_call (con dedup), get_response, close, clear_history
  tool_packs.py     # NUEVO — registry agent_type -> ToolPack(tools, render_context,
                    #         build_metadata, on_conversation_start, default_prompt_file)
  prompt_loader.py  # NUEVO — get_active_prompt(slug): Redis -> DB -> .md fallback
  tools/crm.py            # sin cambios de lógica
  tools/inmobiliaria.py   # sin cambios de lógica
  imprimir_graph.py # SE BORRA (migrado al chasis)
  c21_graph.py      # SE BORRA (migrado al chasis)
  graph.py          # FUERA DE ALCANCE (chatbot streaming)
app/models/
  tenant_prompt.py  # NUEVO — SQLModel tabla tenant_prompts
app/api/admin/
  prompts.py        # NUEVO — router /admin/tenants/{slug}/prompts (Swagger, require_admin)
app/core/tenant.py  # EDIT — exponer prompt activo vía cache chain existente
alembic/versions/   # NUEVO — migración: crear tabla + seed desde imprimir.md / c21.md
tests/unit/ , tests/api/ , tests/unit/tools/   # tests nuevos aquí
```

## Code Style

Se respeta el estilo vigente del repo (docstrings densos con el *porqué*, no el *qué*; type hints;
`logger.bind(...)` estructurado; funciones `_privadas` para helpers). Ejemplo del patrón a seguir:

```python
async def get_active_prompt(slug: str, *, default_file: str) -> str:
    """Prompt activo del tenant: Redis -> DB (tenant_prompts activo) -> .md fallback.

    Cacheado bajo tenant:prompt:{slug} (mismo patrón/TTL que TenantConfig). El fallback al .md
    garantiza que un tenant sin fila activa nunca quede sin prompt (compat con el arranque actual).
    """
    cached = await _prompt_cache_get(slug)
    if cached is not None:
        return cached
    content = await _prompt_db_get_active(slug)
    if content is None:
        content = _read_default_prompt(default_file)  # imprimir.md / c21.md
    await _prompt_cache_set(slug, content)
    return content
```

Regla crítica: los prompts contienen llaves literales (`{precio}`, `{{OFICINA}}`) → **nunca
`str.format`**; sustitución solo con `.replace` de marcadores conocidos.

## Testing Strategy

- Framework: `pytest` + `pytest-asyncio` (asyncio_mode=auto). Tests en `tests/unit`, `tests/api`,
  `tests/unit/tools`.
- **Unit**: render del prompt con placeholders y con contexto faltante; `get_active_prompt` en sus 3
  ramas (cache / DB / fallback); activar/rollback (solo una versión activa por tenant); invalidación
  de caché tras escritura.
- **API**: endpoints de prompt (crear/listar/activar/validar) con `require_admin`; 404 tenant/versión,
  409 duplicado, 422 render inválido.
- **Integración/aislamiento**: dos tenants con el mismo `wa_id` no comparten historial ni prompt;
  subir prompt → el siguiente mensaje usa el nuevo.
- **No regresión**: fixtures que comparan que `imprimir`/`c21` producen la misma estructura de
  respuesta que antes del refactor.

## Boundaries

- **Always**: correr `make check` + `uv run pytest -m "not slow"` antes de commit; mantener docstrings
  con el *porqué*; invalidar caché del tenant en toda escritura; usar `.replace` (nunca `.format`) para
  renderizar prompts; mantener los secretos cifrados.
- **Ask first**: cambiar el esquema de `thread_id` (`{slug}:{wa_id}`) porque reinicia historiales;
  cualquier cambio a la tabla `tenants` existente; agregar dependencias nuevas; borrar
  `imprimir_graph.py`/`c21_graph.py` (confirmar equivalencia antes).
- **Never**: commitear secretos; usar `str.format` en prompts; exponer el prompt-management sin
  `require_admin`; tocar `graph.py` (chatbot, fuera de alcance); quitar tests que fallan sin aprobación.

## Open Questions

1. **Migración de checkpoints**: al pasar `thread_id` a `{slug}:{wa_id}`, ¿aceptamos que los
   historiales activos se reinicien, o hace falta un script de migración de claves? (Afecta deploy.)
2. **Identidad `created_by`**: ¿`require_admin` expone una identidad utilizable para auditar quién
   subió cada versión, o guardamos un placeholder por ahora?
3. **`/validate`**: ¿bloquea la activación ante placeholder desconocido, o solo advierte y deja
   activar igual? (Propuesta: bloquea solo ante error de render real.)
4. **Límite de tamaño** del prompt (`content` TEXT) — ¿imponemos un máximo (p. ej. 64 KB)?
5. **Orden de PRs**: ¿confirmas chasis (Fase 1) y feature (Fases 2-4) como PRs separados?
```
