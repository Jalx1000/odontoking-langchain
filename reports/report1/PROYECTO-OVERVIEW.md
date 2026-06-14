# 01.odontoking — Reporte General del Proyecto

> Re-auditoría desde cero del agente de WhatsApp para clínica dental (FastAPI + LangGraph + PostgreSQL + Redis/Valkey + mem0).
> Alcance: **solo la app `01.odontoking`** (excluye n8n, kohlberg, planning).
> Fecha: 2026-06-14 · Tests al momento del reporte: **260 passed / 4 failed**.
>
> Diagramas en [`diagrams/`](diagrams/):
> 1. [Arquitectura de componentes](diagrams/01-arquitectura-componentes.mmd)
> 2. [Flujo de un mensaje (secuencia)](diagrams/02-flujo-mensaje-secuencia.mmd)
> 3. [Máquina de estados LangGraph](diagrams/03-langgraph-maquina-estado.mmd)
> 4. [Actividad: agendamiento de cita](diagrams/04-actividad-agendamiento.mmd)
> 5. [Topología multi-agente / despliegue](diagrams/05-multiagente-despliegue.mmd)

---

## 0. ¿Cuál es la meta? (visión)

**Meta del proyecto:** un asistente conversacional de WhatsApp que atiende pacientes de clínica dental de punta a punta — registra al paciente en el CRM, responde dudas, verifica seguros, consulta disponibilidad real de doctores y **agenda/cancela citas automáticamente**, sin intervención humana, con memoria de largo plazo y observabilidad completa.

**Meta de plataforma (a donde apunta el código):** convertir ese agente único en una **plataforma multi-tenant** ("Plan B") donde cada cliente (odontoking, kohlberg, …) es un tenant con su propia App de Meta, credenciales de CRM y, opcionalmente, su propio worker/servicio de agente. Ver [diagrama 5](diagrams/05-multiagente-despliegue.mmd).

**Mi meta como asistente en este repo:** llevar el proyecto de *"funciona en el happy path"* a *"production-grade y mantenible"*: cerrar los bugs que ya tienen test en rojo, blindar la seguridad del webhook, eliminar la duplicación de agentes, y dejar el roadmap multi-tenant ejecutable. El detalle de bugs/seguridad/QA está en los reportes hermanos ([platform-dev](platform-dev.md), [infra-dev](infra-dev.md), [security-dev](security-dev.md), [qa-dev](qa-dev.md), [devops-dev](devops-dev.md)); este documento es la **vista de producto y arquitectura**.

---

## 1. ✅ Qué funciona (hoy, en producción)

- **Ingreso de mensajes de WhatsApp** multi-tenant por ruta `/{tenant}/webhook` + alias legacy `/webhook` para odontoking ([whatsapp.py](../../app/api/v1/whatsapp.py)).
- **Soporte de texto, audio (transcripción Whisper) e interactivos** (botones/listas), con rechazo amable de imágenes/documentos/video.
- **Deduplicación de mensajes** (reintentos de Meta) y **rate-limit por `wa_id`** en memoria.
- **Debounce/batching por usuario** ([message_buffer.py](../../app/services/message_buffer.py)): acumula mensajes rápidos, 1 worker por `wa_id` con lock en Redis → procesamiento FIFO, sin condiciones de carrera sobre el checkpointer.
- **Agente LangGraph funcional** ([odontoking_graph.py](../../app/core/langgraph/odontoking_graph.py)): bucle `chat ↔ tool_call`, checkpointing en Postgres (`AsyncPostgresSaver`), 10 tools de negocio, contexto de fecha/hora Bolivia y contexto de paciente inyectado al prompt.
- **Integración CRM completa**: alta de persona, leads, atributos, actividades (citas) y sincronización de transcript ([crm.py](../../app/core/langgraph/tools/crm.py)).
- **Memoria de largo plazo opcional** (mem0 + pgvector) con capa de caché, activable por flag.
- **Respuesta enriquecida**: convierte opciones numeradas en botones/listas de WhatsApp automáticamente ([whatsapp_client.py](../../app/services/whatsapp_client.py)).
- **Dos rutas de entrega**: in-process (buffer) y broker (Redis Streams / RabbitMQ) con worker dedicado por tenant ([worker.py](../../app/worker.py), [broker.py](../../app/core/broker.py)).
- **Admin API** (tenants, stats, billing, conversations, users, DLQ) protegida por `X-Admin-Key`.
- **Observabilidad**: Langfuse (trazas), Prometheus/Grafana (métricas), structlog con correlation-id, `/health` con verificación real de DB/cache/buffer.
- **Resiliencia**: at-least-once en el broker con DLQ + alerta por email; recuperación de buffers huérfanos al arranque; reintentos con tenacity en las tools de clínica/seguro.
- **Suite de tests** razonable (264 tests) y **backlog auto-documentado** de 70 issues en [`todo/`](../../todo/).

---

## 2. 🐛 Bugs / Fixes (confirmados leyendo código y/o con test en rojo)

| # | Severidad | Archivo:línea | Problema | Estado test |
|---|-----------|---------------|----------|-------------|
| B1 | **CRÍTICO** | [odontoking.py:148](../../app/core/langgraph/tools/odontoking.py#L148) | `d.get("has_availability" != true)` — `true` no existe en Python → `NameError`; `get_doctors` **siempre falla** para doctores activos y devuelve `{"error": ...}` al LLM. El agente no puede listar doctores. | 🔴 2 tests fallando |
| B2 | **CRÍTICO/negocio** | [insurance.py:53-56](../../app/core/langgraph/tools/insurance.py#L53) | `verify_insurance` fuerza `status="VIGENTE"` cuando `has_insurance` es truthy, **pisando un `"VENCIDO"`** del upstream. | 🔴 1 test fallando |
| B3 | **ALTO** | [odontoking.py:226](../../app/core/langgraph/tools/odontoking.py#L226) | `get_doctor_schedule` devuelve el string `"Sin disponibilidad"` en el campo `schedule` (debería ser `[]`), rompe el contrato `schedule: list`. | 🔴 1 test fallando |
| B4 | **CRÍTICO/seg** | [whatsapp.py:343-389](../../app/api/v1/whatsapp.py#L343) | Webhook **sin verificación de firma `X-Hub-Signature-256`** → inyección de payloads falsos. (todo/64) | — |
| B5 | **CRÍTICO/seg** | [whatsapp.py:395-404](../../app/api/v1/whatsapp.py#L395) | `DELETE /{tenant}/history/{wa_id}` **sin autenticación** → cualquiera borra el historial de un paciente. (todo/54) | — |
| B6 | **ALTO** | [odontoking_graph.py:375-382](../../app/core/langgraph/odontoking_graph.py#L375) | Ante `GraphRecursionError` se **borra el checkpoint completo** del paciente (pérdida de contexto). (todo/30) | — |
| B7 | **ALTO** | [odontoking_graph.py:148-153](../../app/core/langgraph/odontoking_graph.py#L148) | El agente real usa `ChatOpenAI` directo y **bypassa `llm_service`** → sin fallback de modelo ni reintentos del LLM (solo timeout en worker/webhook). (todo/66) | — |
| B8 | **ALTO** | [crm.py:18-23](../../app/core/langgraph/tools/crm.py#L18), [odontoking.py:24-28](../../app/core/langgraph/tools/odontoking.py#L24), [insurance.py:12-16](../../app/core/langgraph/tools/insurance.py#L12) | `_HEADERS` con token **en tiempo de import** → ignora `TenantConfig.crm_token`/`crm_url` por tenant; rompe rotación y multi-tenant. (todo/21, 22) | — |
| B9 | MEDIO | [whatsapp.py:50-57](../../app/api/v1/whatsapp.py#L50) | Dedup y rate-limit **en memoria por proceso** → con 2+ réplicas se duplican mensajes. (todo/18) | — |
| B10 | MEDIO | [odontoking_graph.py:206](../../app/core/langgraph/odontoking_graph.py#L206) | Historial **sin recorte** (`trim_messages`) → crece sin límite, dispara B6. | — |
| B11 | MEDIO | [config.py:150](../../app/core/config.py#L150) | `DEFAULT_LLM_MODEL="gpt-5-mini"` no está en el registry (afecta solo a `LLMService` legacy, no a la ruta de prod). (todo/02, 11) | — |

> El [`todo/`](../../todo/) lista **70 issues** ya tipados (`[bug]/[debt]/[perf]/[risk]/[config]`). Son un excelente backlog; el problema no es falta de detección sino de **cierre** (varios críticos siguen abiertos: 30, 54, 64, 66).

---

## 3. 🔧 Qué mejorar (deuda técnica de fondo)

- **Dos agentes en paralelo:** `LangGraphAgent` ([graph.py](../../app/core/langgraph/graph.py), legacy/template) sigue pre-calentándose en [main.py:65](../../app/main.py#L65) y consume un pool de conexiones, pero el que atiende es `OdontokingAgent`. Consolidar en uno. (todo/29, 13)
- **Llamadas DB síncronas dentro de handlers async** (`get_tenant_async` → `_db_get` con `Session` síncrono, [tenant.py:149](../../app/core/tenant.py#L149)) → bloquean el event loop en cada webhook con cache-miss. (todo/17)
- **Singletons construidos en import-time** (broker, llm registry, agentes, prompts) → side effects al importar (p.ej. alembic), prompts no recargables. (todo/12, 20, 24, 31)
- **DLQ in-memory en RabbitMQ** ([broker.py:316](../../app/core/broker.py#L316)) → se pierde al reiniciar; el email es el único registro durable. (todo/23)
- **Prompt y headers cacheados al importar** → cambios requieren redeploy.
- **README/docs sin personalizar:** siguen siendo del template genérico (ver §7).
- **`logger.error` en bloques except** en varios módulos (debería ser `logger.exception`). (todo/16, 47)
- **Tests que mockean la DB** en flujos sensibles (billing/usage) — anti-patrón. (ver [qa-dev.md](qa-dev.md))

---

## 4. 🕳️ Qué falta / Qué estamos pasando por alto

- **Seguridad del borde:** firma de webhook (B4), auth en endpoints de borrado (B5), `hmac.compare_digest` para `X-Admin-Key`, rate-limit en varios `/admin/*` e `/internal/*`.
- **Red de seguridad de CI:** el workflow [ci.yaml](../../.github/workflows/ci.yaml) corre ruff + pyright pero **no ejecuta `pytest`** → por eso 4 tests en rojo y bugs llegaron sin detección. El [deploy.yaml](../../.github/workflows/deploy.yaml) llama a `make docker-build-env` que **no existe** en el Makefile.
- **Retención de datos:** no hay política para `checkpoints*` ni `chat_histories_odonto` → crecimiento ilimitado. Tampoco backup del volumen Postgres (contiene PII médica).
- **Gestión de secretos:** `.env.development` con credenciales reales en disco (no trackeado, correcto) — **rotarlas**. (todo/05)
- **PII en logs:** `whatsapp_raw_payload` loguea el payload (nombre, teléfono, motivo) a nivel INFO. ([whatsapp.py:353](../../app/api/v1/whatsapp.py#L353))
- **Idempotencia de `update_crm`:** sin clave de idempotencia → reintentos pueden duplicar leads/citas. (todo/68)
- **Prompt injection:** entrada del paciente llega al prompt sin mitigación explícita.
- **Tool muerta:** `duckduckgo_search` existe pero **no está cableada** al agente odontoking.
- **Config dudosa:** `SHAREMEDATA` mal formada (todo/04); propósito sin documentar.

---

## 5. ✨ Features a agregar (propuestas)

**Producto / negocio**
1. **Recordatorios de cita** (24 h / 2 h antes) vía WhatsApp template messages — alto valor, hoy inexistente.
2. **Reprogramación/cancelación self-service** guiada (ya hay `get_citas` + cancelación en CRM; falta el flujo conversacional).
3. **Encuesta de satisfacción post-cita** y captura de NPS.
4. **Handoff a humano** (escalamiento a recepción) cuando el agente no resuelve o el paciente lo pide.
5. **Mensajes proactivos / campañas** (recall de limpieza semestral) — requiere plantillas aprobadas por Meta.

**Plataforma**
6. **Onboarding de tenant self-service** desde el Admin API/portal (crear tenant, credenciales cifradas, registrar webhook).
7. **`BaseAgent` + factory** para que kohlberg y otros clientes hereden del template (ya anticipado en [worker.py:32](../../app/worker.py#L32)).
8. **Dashboard de conversaciones** (frontend) — hoy solo Admin API.

**Robustez**
9. **`trim_messages` + resumen de contexto** en vez de borrar historial (resuelve B6/B10).
10. **DLQ persistente en DB** y panel de reproceso.

---

## 6. 🤖 Multi-agente / Multi-tenant (estado real)

Ver [diagrama 5](diagrams/05-multiagente-despliegue.mmd).

- **Modelo de tenant** completo: [`TenantConfig`](../../app/core/tenant.py) con lookup **cache (Redis) → DB (Postgres) → fallback env**, credenciales WA/CRM **cifradas con Fernet** en DB, campos `agent_type`, `agent_endpoint_url`, `llm_model`, `billing_*`.
- **Dos modos de despliegue de agente ya soportados:**
  - *In-process*: agente embebido en la API (ruta A).
  - *Externo/distribuido*: si `agent_endpoint_url` está set, la API publica al **broker** y un **worker por tenant** (`WORKER_TENANT=...`) consume de Redis Streams/RabbitMQ.
- **Limitaciones actuales:**
  - `_AGENT_REGISTRY = {"odontoking": ...}` — **solo un agente** registrado (todo/15, 19).
  - Las **tools ignoran el tenant** (token global en import, B8) → multi-tenant real aún no funciona end-to-end.
  - `_build_agent_registry()` instancia agentes en el worker pero no hay aún un `BaseAgent` reutilizable (anotado como "Fase 3").

**Veredicto:** el andamiaje multi-tenant está ~70% construido pero **no operativo** para un segundo cliente hasta cerrar B8 y la factory de agentes.

---

## 7. 📚 ¿Está documentado todo el proyecto?

**Parcialmente. Falta documentación específica del producto real.**

| Existe | Estado |
|--------|--------|
| `AGENTS.md` / `CLAUDE.md` | ✅ Buenas reglas de ingeniería… pero `CLAUDE.md` describe el flujo del **template** (`LangGraphAgent.get_response`), no el real (`OdontokingAgent` + worker + broker). **Desactualizado.** |
| `docs/` (10 archivos) | ⚠️ Genéricos del template (architecture, auth, db, llm, memory…). **No documentan** WhatsApp, CRM, broker/worker, buffer, ni multi-tenant. |
| `README.md` | ❌ Es el README del template ("FastAPI LangGraph Agent Template", incluso con typo "supwirport"). 0 menciones a odontoking/whatsapp/dental. |
| `planning/` | ✅ Aquí sí vive la visión real (multi-tenant, sprints, bugfixes, e2e). |
| `todo/` (70) | ✅ Backlog técnico excelente y tipado. |
| `REQUERIMIENTOS-AGENTE-WHATSAPP.md` | ✅ Requerimientos de negocio. |
| Diagramas | ❌ No existían → **creados en este reporte** ([diagrams/](diagrams/)). |
| Tests como doc viva | ⚠️ Buenos, pero 4 en rojo y CI no los corre. |

**Brechas de documentación a cerrar:** README específico del producto, doc de integraciones externas (§ siguiente), runbook de despliegue (Railway/easypanel + worker por tenant), guía de onboarding de tenant, y actualizar `CLAUDE.md` al flujo real.

---

## 8. 🔌 Integraciones externas (mapa)

| Integración | Dónde | Uso | Notas |
|-------------|-------|-----|-------|
| **WhatsApp Cloud API (Meta v25.0)** | [whatsapp_client.py](../../app/services/whatsapp_client.py), webhook | Recibir/enviar, media, typing, read receipts | Falta verificación de firma (B4) |
| **OpenAI — Chat** | [odontoking_graph.py:148](../../app/core/langgraph/odontoking_graph.py#L148) | LLM del agente (`gpt-4o-mini`) | Bypassa `llm_service` (B7) |
| **OpenAI — Whisper** | [whatsapp_client.py:222](../../app/services/whatsapp_client.py#L222) | Transcripción de notas de voz (`whisper-1`) | — |
| **Sofopolis / OdontoCRM** | [crm.py](../../app/core/langgraph/tools/crm.py) | personas, leads, atributos, actividades (citas), notas | Token global, no per-tenant (B8) |
| **Odontoking Clinic API** | [odontoking.py](../../app/core/langgraph/tools/odontoking.py) | products/services, specialties, doctors, slots, horarios, disponibilidad | Mismo `_BASE` que CRM |
| **Insurance Verify API** | [insurance.py](../../app/core/langgraph/tools/insurance.py) | Verificación de seguro por CI | Override de status (B2) |
| **Langfuse** | [observability.py](../../app/core/observability.py) | Trazas de LLM | Activable por flag |
| **mem0 + pgvector** | [memory.py](../../app/services/memory.py) | Memoria semántica largo plazo | Opcional (`ODONTOKING_MEMORY_ENABLED`) |
| **PostgreSQL** | checkpoints, `chat_histories_odonto`, tenants, users, sessions, usage_logs | Estado + CRUD | Dos estrategias de conexión |
| **Redis / Valkey** | buffer, locks, cache, streams | Coordinación distribuida | Fallback in-memory |
| **RabbitMQ** | [broker.py](../../app/core/broker.py) | Broker alternativo (Fase 5) | DLQ in-memory (B/todo-23) |
| **Email (notify)** | [notifications.py](../../app/core/notifications.py) | Alertas de DLQ | — |
| **DuckDuckGo** | tools/duckduckgo_search.py | Búsqueda web | **No cableada** al agente |
| **Sharemedata** | config | ¿? | Key malformada (todo/04), sin documentar |

---

## 9. 🗺️ Roadmap

### 🔴 Fase 0 — Estabilización (1 semana) · *antes de cualquier deploy*
- Fix B1 (`true` → filtro correcto), B2 (no pisar VENCIDO), B3 (`[]` en schedule) — ya tienen test en rojo.
- B4 firma de webhook + B5 auth en `DELETE history`.
- Rotar secretos de `.env.development`.
- Añadir `pytest` a CI y arreglar el job de deploy.
- **Salida:** 264/264 verde, CI bloquea merges, borde seguro.

### 🟠 Fase 1 — Robustez del agente (1–2 sprints)
- B7: portar `OdontokingAgent` a `llm_service` (fallback + reintentos).
- B6/B10: `trim_messages` + resumen, eliminar el borrado de checkpoint.
- Eliminar `LangGraphAgent` legacy y su pre-warm.
- Quitar DB síncrona del path async (B/todo-17); consolidar pools.
- Retención de checkpoints/historial + backup Postgres.

### 🟡 Fase 2 — Multi-tenant real (2–3 sprints)
- B8: tools leen credenciales del `TenantConfig` (per-tenant token/url).
- `BaseAgent` + factory; registry dinámico; segundo tenant (kohlberg) de prueba.
- Onboarding de tenant self-service vía Admin API + DLQ persistente.

### 🟢 Fase 3 — Producto (continuo)
- Recordatorios de cita, reprogramación self-service, handoff a humano, encuestas, campañas proactivas, dashboard de conversaciones.

### 📘 Transversal — Documentación
- README real, docs de integraciones, runbook de despliegue, actualizar `CLAUDE.md`, mantener diagramas.

---

## 10. Resumen de prioridades

| Prioridad | Acción | Severidad | Esfuerzo |
|-----------|--------|-----------|----------|
| P0 | B1/B2/B3 (bugs con test rojo) | Crítico | Bajo |
| P0 | B4/B5 (seguridad webhook) | Crítico | Medio |
| P0 | Rotar secretos + pytest en CI + fix deploy | Alto | Bajo |
| P1 | B7 llm_service + B6/B10 contexto | Alto | Medio |
| P1 | Eliminar agente legacy + DB async + pools | Alto | Medio |
| P1 | Retención datos + backup | Alto | Medio |
| P2 | B8 multi-tenant tools + BaseAgent | Alto | Alto |
| P2 | Features de producto (recordatorios, handoff) | Medio | Alto |
| P3 | Documentación (README, docs, runbook) | Medio | Medio |
