# 11 — Agentes data-driven: prompt por cliente vía API (sin front)

> Estado: PLAN (no implementado). Fecha: 2026-10-09.
> Objetivo: que dar de alta un cliente nuevo del mismo rubro sea **solo subir un prompt por API**,
> sin tocar código ni hacer deploy. La gestión es 100% por Swagger (`/docs`), sin frontend.

---

## 0. Punto de partida (verificado en el código actual)

- Hay **3 agentes**: `LangGraphAgent` (`graph.py`, chatbot con streaming — fuera de alcance),
  `ImprimirAgent` (`imprimir_graph.py`) y `C21Agent` (`c21_graph.py`).
- Los dos de WhatsApp son **~90% idénticos** (diff: 211 líneas, casi todas nombres de log,
  imports de tools y el prompt). Lo único que difiere de verdad son 6 ejes:
  nombre, tool pack, prompt, placeholders del prompt, metadata inyectada, hooks (c21 abre lead
  en el 1er mensaje y deduplica tool-calls; imprimir no deduplica → bug latente).
- Ya existe multi-tenant serio:
  - `app/models/tenant.py` → tabla `tenants` (SQLModel), con `agent_type`, `llm_model`, CRM, billing.
    Secretos cifrados con Fernet.
  - `app/core/tenant.py` → `TenantConfig` runtime + lookup **Redis → DB → env fallback**
    (`get_tenant_async(slug)`), `invalidate_tenant_cache(slug)`.
  - `app/api/admin/tenants.py` → CRUD admin completo, `require_admin`, ya en Swagger
    (`tags=["admin-tenants"]`).
- Dispatch tenant→agente hoy triplicado: `whatsapp.py` (`_AGENT_REGISTRY`), `crm.py`, `worker.py`,
  `admin/conversations.py`.

## 1. Modelo mental

- **Tool pack = código, irreducible.** `crm.py` (leads/cotizaciones) e `inmobiliaria.py`
  (inmuebles/visitas) son integraciones HTTP; no se "promptean".
- **Prompt = dato por cliente.** Se sube por API, vive en DB, se asocia a un tenant, se carga por
  request.
- Consecuencia:
  - **Cliente nuevo, mismo rubro** → solo prompt (cero código).
  - **Rubro nuevo con acciones nuevas** → tool pack nuevo (código + deploy) + prompt.

## 2. Arquitectura objetivo

Un **único `WhatsAppAgent`** (chasis) reemplaza `ImprimirAgent` y `C21Agent`. El `agent_type` del
tenant elige el **tool pack**. El prompt se carga por request desde el store (no desde `.md` al
importar).

```
app/core/langgraph/
  base_agent.py     # WhatsAppAgent: pool PG, checkpointer, _chat, _tool_call (con dedup), get_response
  tool_packs.py     # registry: agent_type -> ToolPack(tools, render_context, build_metadata, hooks)
  prompt_loader.py  # get_active_prompt(slug): Redis -> DB -> .md fallback
  tools/crm.py, tools/inmobiliaria.py   # sin cambios de lógica
```

- Se **borran** `imprimir_graph.py` y `c21_graph.py`.
- `graph.py` (chatbot streaming) queda **fuera de alcance**: ciclo de vida distinto (sesiones,
  streaming, historial por usuario), no se clona por cliente.
- Hay **una instancia de chasis por `agent_type`** (no por cliente): el mismo tool pack sirve a N
  clientes, cada uno con su prompt.

### `ToolPack` (reemplaza a las clases de agente)

```python
@dataclass(frozen=True)
class ToolPack:
    agent_type: str                               # "odontoking" | "c21"
    tools: list[BaseTool]
    build_metadata: Callable[[Ctx], dict]         # qué ids inyectar en config.metadata
    render_context: Callable[[Ctx], str]          # bloque "# Contexto" + placeholders ({{OFICINA}}…)
    on_conversation_start: Callable | None = None  # hook c21: abrir_lead_inicial
    handoff_tool_name: str = "derivar_a_asesor"
    default_prompt_file: str                       # seed/fallback (imprimir.md / c21.md)
```

## 3. Almacenamiento del prompt — **tabla versionada** (decisión tomada)

Tabla nueva `tenant_prompts` (no una columna en `tenants`): permite historial, rollback y auditoría.

| columna | tipo | nota |
|---|---|---|
| `id` | uuid | PK |
| `tenant_slug` | str, FK→tenants.slug, index | |
| `version` | int | autoincremental **por tenant** |
| `content` | text | prompt completo |
| `is_active` | bool | exactamente una activa por tenant |
| `note` | str nullable | "ajuste de tono", etc. |
| `created_at` | ts | |
| `created_by` | str nullable | identidad admin |

- **Prompt activo** = fila con `is_active=True` del tenant. Activar una versión desactiva la anterior
  (transacción).
- **Migración Alembic**: crear tabla + **seed**: `imprimir.md` → versión 1 activa del tenant
  odontoking/imprimir; `c21.md` → versión 1 activa del tenant c21.
- **Fallback**: si un tenant no tiene fila activa, el loader usa `tool_pack.default_prompt_file`.

## 4. API de gestión (Swagger, sin front, todo documentado)

Extiende el router admin existente (`require_admin`, ya en `/docs`). Prefijo
`/admin/tenants/{slug}/prompts`:

| método | ruta | descripción |
|---|---|---|
| `POST` | `/{slug}/prompts` | **Subir** versión nueva. Body: `content`, `note?`, `activate: bool=false`. Devuelve `version`. |
| `GET` | `/{slug}/prompts` | Listar versiones (metadata; sin `content`). |
| `GET` | `/{slug}/prompts/{version}` | Contenido de una versión. |
| `GET` | `/{slug}/prompts/active` | Prompt activo actual. |
| `POST` | `/{slug}/prompts/{version}/activate` | Activar/rollback a esa versión. |
| `POST` | `/{slug}/prompts/validate` | **Dry-run**: renderiza con contexto de ejemplo y reporta placeholders rotos, SIN guardar. |

Requisitos de documentación ("todo documentado"):
- Cada endpoint con `summary`, `description`, `response_model` y `responses={...}` (404 tenant/versión,
  409 slug, 422 validación).
- Ejemplos de request/response en los schemas Pydantic (`model_config = {"json_schema_extra": {...}}`).
- Agrupados bajo `tags=["admin-tenants"]` (o `["admin-prompts"]`).
- **Sin frontend**: la UI es Swagger (`/docs`).

Toda escritura → `invalidate_tenant_cache(slug)` + invalidar `tenant:prompt:{slug}`.

## 5. Routing / aislamiento ("cada input vuelve al cliente que lo mandó")

Ya existe casi todo: el webhook identifica el tenant por `phone_number_id`, `get_tenant_async(slug)`
trae su config, el gateway responde por el canal de ese tenant. Dos correcciones **obligatorias**
para que sea estanco con prompts dinámicos:

1. **Pasar `tenant_slug` a `get_response(...)`** → `_chat` carga el prompt de ese tenant (hoy el
   prompt es global al agente).
2. **Namespacing del checkpoint**: hoy `thread_id = wa_id`. Cambiar a `thread_id = f"{slug}:{wa_id}"`
   para que el mismo número hablando con dos tenants no colisione historiales. (Bug de aislamiento
   real; migrar/clear de checkpoints existentes al desplegar.)

## 6. Caché y rendimiento

- Prompt activo cacheado en Redis `tenant:prompt:{slug}` (mismo patrón/TTL que `TenantConfig`),
  invalidado al subir/activar. El webhook no toca DB por mensaje.
- `prompt_loader.get_active_prompt(slug)`: Redis → DB (`tenant_prompts` activo) → `.md` fallback.

## 7. "Crear un agente nuevo" después (el payoff)

- **Cliente nuevo, mismo rubro**: `POST /admin/tenants` (slug + credenciales WA + `agent_type`) +
  `POST /admin/tenants/{slug}/prompts` (su prompt, `activate=true`). **Cero código, cero deploy.**
- **Rubro nuevo con acciones nuevas**: agregar un `ToolPack` en `tool_packs.py` (código + deploy) +
  los dos POST. El único código es el de las tools.

## 8. Orden de ejecución (fases)

1. **Chasis** — `base_agent.py` + `tool_packs.py`; migrar imprimir/c21; actualizar call sites
   (`whatsapp.py`, `crm.py`, `worker.py`, `admin/conversations.py`); borrar los 2 `_graph.py`;
   unificar el dedup de tool-calls. ⇒ equivalencia funcional, prompt aún desde `.md`.
2. **Prompt dinámico** — modelo `TenantPrompt` + migración Alembic + seed; `prompt_loader.py`;
   `_chat` lee del loader. ⇒ comportamiento idéntico, prompt ya en DB.
3. **API Swagger** — endpoints `/prompts` documentados + `/validate`.
4. **Aislamiento** — `thread_id` namespaced + `tenant_slug` en `get_response`.
5. **Tests** — render con placeholders/missing context; activate/rollback; invalidación de caché;
   aislamiento dos-tenants-mismo-wa_id; upload → siguiente mensaje usa el prompt nuevo.

## 9. Riesgos / decisiones

- **Placeholders**: los prompts traen `{precio}`, `{{OFICINA}}` literales → **nunca** `str.format`;
  seguir con `.replace`. El endpoint `/validate` lo cubre antes de activar.
- **Inyección de prompt**: el prompt es texto que entra al system prompt; mitigado porque **solo
  admin** sube (`require_admin`). Documentar.
- **`llm_model` por tenant**: el campo ya existe; el chasis debe respetarlo (hoy usa setting global).
- **Rollback de checkpoints** al cambiar el esquema de `thread_id` (fase 4).
- **Fuera de alcance**: `graph.py` (chatbot). Las tools siguen siendo código (frontera honesta).
