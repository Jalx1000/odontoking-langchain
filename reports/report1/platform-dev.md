# Platform-Dev — Reporte de Revisión del Proyecto

> Alcance: revisión backend/plataforma de `01.odontoking` — rutas FastAPI, grafo
> LangGraph, herramientas, servicios, configuración, modelos, broker/webhook,
> middleware, rate limiting, manejo de errores, corrección async, LLM
> retry/fallback y memoria. Contrastado contra `AGENTS.md` / `CLAUDE.md`.
>
> Nota de contexto: este repo es un agente de WhatsApp para una clínica dental
> (single/multi-tenant en transición — "Plan B" multi-tenant con fallback a
> env vars). Existe un flujo "genérico" de plantilla (`app/core/langgraph/graph.py`,
> `app/api/v1/chatbot.py`, `app/services/llm/*`) que **NO es el camino real de
> producción**: el flujo real de WhatsApp usa
> `app/core/langgraph/odontoking_graph.py` (`OdontokingAgent`), que tiene su
> propio `ChatOpenAI` sin pasar por `llm_service`. Esto produce una
> inconsistencia importante documentada abajo.

---

## Resumen ejecutivo (3-5 bullets)

- **El flujo de producción (`OdontokingAgent` en `odontoking_graph.py`) NO usa
  `llm_service`**: no tiene reintentos `tenacity`, ni fallback circular entre
  modelos, ni timeout global (`LLM_TOTAL_TIMEOUT`). Solo el endpoint genérico
  `/chatbot/chat` (no usado por WhatsApp) tiene esa protección. Esto es la
  brecha más grave de robustez del proyecto (`odontoking_graph.py:148-224`).
- **El historial de conversación de Odontoking crece sin límite** dentro del
  contexto del LLM: `_chat(...)` concatena `state.messages` completo sin pasar
  por `prepare_messages`/`trim_messages` (a diferencia de `graph.py`), lo que
  puede disparar `GraphRecursionError` y costos crecientes en conversaciones
  largas (`odontoking_graph.py:206`).
- **Configuración rota / inconsistente**: `DEFAULT_LLM_MODEL` por defecto es
  `"gpt-5-mini"` (`config.py:150`) pero `LLMRegistry.LLMS` solo contiene
  `gpt-4o-mini` y `gpt-4o` (`registry.py:30-51`) — el servicio cae siempre al
  warning `default_model_not_found_using_first` (issue ya documentado en
  `todo/02`).
- **Violaciones sistemáticas de la regla "todas las rutas con rate limiting"**:
  los endpoints GET de verificación de webhook, `DELETE /history/{wa_id}`,
  `/internal/usage` y **todos** los endpoints de `/admin/*` no tienen
  `@limiter.limit`.
- **Cabeceras HTTP con secretos "horneadas" en tiempo de import** en
  `crm.py`, `odontoking.py` e `insurance.py` (`_HEADERS = {... settings.ODONTOKING_API_TOKEN}`)
  — si el token rota en runtime (env var cambia sin redeploy, o multi-tenant
  con tokens distintos por clínica), las llamadas seguirán usando el token
  viejo. Además rompe el modelo multi-tenant (Plan B) porque ignora
  `tenant.crm_token`.
- Buen nivel general de manejo de errores y logging estructurado en las
  herramientas (`crm.py`, `odontoking.py`, `insurance.py`), uso correcto de
  `tenacity` en llamadas a la API pública de Odontoking, y diseño cuidadoso
  del buffer/dedupe de WhatsApp (`message_buffer.py`, `whatsapp.py`).

---

## 🐛 Bugs / Errores

### CRÍTICO

1. **`odontoking_graph.py:148-153,209-212` — Sin retries/fallback/timeout en el LLM de producción.**
   `OdontokingAgent.__init__` crea su propio `ChatOpenAI(model=settings.ODONTOKING_LLM_MODEL, ...).bind_tools(...)`
   y `_chat()` llama `self._llm.ainvoke(...)` directamente. No pasa por
   `llm_service.call()`, por lo que:
   - No hay `@retry` con backoff exponencial (regla 3 del proyecto).
   - No hay fallback circular a otro modelo si `gpt-4o-mini` falla o da rate-limit.
   - No hay `LLM_TOTAL_TIMEOUT` (60s) — un `ainvoke` colgado solo se corta por
     el `asyncio.wait_for(..., timeout=settings.LLM_TOTAL_TIMEOUT + 30)` en
     `whatsapp.py:131-140`, que es un timeout de *todo el grafo* (incluye
     herramientas), no del LLM en sí.
   - **Fix sugerido**: usar `llm_service.call(langchain_messages, model_name=settings.ODONTOKING_LLM_MODEL, ...)`
     o, si se necesita un LLM dedicado por tenant, registrar el modelo de
     Odontoking en `LLMRegistry` y envolver la llamada con la misma lógica de
     `_invoke_with_retry` / `_call_with_fallback`.

2. **`odontoking_graph.py:206` — Historial sin recorte (`prepare_messages`/`trim_messages` no se usa).**
   `langchain_messages = [SystemMessage(content=system_prompt)] + list(state.messages)`
   envía **todo** el historial acumulado al LLM en cada turno. `app/utils/graph.py`
   ya tiene `prepare_messages()` con `trim_messages` + `tiktoken`, usado solo
   por `graph.py` (no usado en producción). Para conversaciones largas de
   WhatsApp esto:
   - Aumenta linealmente costo y latencia por turno.
   - Puede superar el límite de contexto del modelo.
   - Contribuye a `GraphRecursionError` (mitigado de forma reactiva en
     `except GraphRecursionError` borrando todo el checkpoint —
     `odontoking_graph.py:375-382` — lo cual **pierde memoria conversacional**
     del paciente).
   - **Fix sugerido**: aplicar `prepare_messages`/`trim_messages` (o un
     recorte por nº de mensajes) antes de construir `langchain_messages`.

3. **`config.py:150` vs `registry.py:30-51` — `DEFAULT_LLM_MODEL` no existe en el registro (ya en `todo/02`).**
   `DEFAULT_LLM_MODEL = os.getenv("DEFAULT_LLM_MODEL", "gpt-5-mini")` pero
   `LLMRegistry.LLMS` no incluye `"gpt-5-mini"`. En `LLMService.__init__`
   (`service.py:60-79`) esto cae al `except` y usa `LLMRegistry.LLMS[0]`
   (`gpt-4o-mini`) silenciosamente con un `logger.warning`. Funciona "por
   accidente" pero:
   - Es confuso para cualquier override `model_name="gpt-5-mini"` en
     `llm_service.call()` — lanzaría `ValueError: model 'gpt-5-mini' not found
     in registry` (`service.py:269-273`).
   - **Fix sugerido**: o bien añadir `gpt-5-mini` al registro, o cambiar el
     default de `DEFAULT_LLM_MODEL` a `"gpt-4o-mini"` para que coincida con la
     realidad.

### ALTO

4. **`app/core/langgraph/tools/crm.py:18-23`, `odontoking.py:24-28`, `insurance.py:12-16` — `_HEADERS` construido a nivel de módulo con `settings.ODONTOKING_API_TOKEN`.**
   ```python
   _HEADERS = {
       "accept": "application/json",
       "Content-Type": "application/json",
       "Authorization": f"Bearer {settings.ODONTOKING_API_TOKEN}",
   }
   ```
   Esto se evalúa **una sola vez al importar el módulo** (ya documentado en
   `todo/21`). Problemas:
   - Si `ODONTOKING_API_TOKEN` se actualiza vía variable de entorno en
     producción sin reinicio (p.ej. rotación de credenciales gestionada por un
     orquestador), el token viejo sigue usándose hasta el próximo deploy.
   - En el modelo multi-tenant (Plan B, `app/core/tenant.py`), cada
     `TenantConfig` tiene su propio `crm_token` desencriptado — pero estas
     herramientas **ignoran completamente `tenant.crm_token`/`tenant.crm_url`**
     y siempre usan el token/URL globales de Odontoking. Si se onboardea un
     segundo tenant con agente `"odontoking"` y otro CRM, las llamadas
     seguirían yendo al CRM de Odontoking.
   - **Fix sugerido**: convertir `_HEADERS`/`_BASE` en funciones (`_headers()`,
     `_base_url()`) que lean `settings` (o mejor, recibir `tenant: TenantConfig`
     como parámetro de la tool) en cada llamada.

5. **`app/api/admin/deps.py:20` — comparación de API key con `!=` en vez de `hmac.compare_digest`.**
   ```python
   if x_admin_key != settings.ADMIN_API_KEY:
   ```
   `app/api/v1/internal.py:38` sí usa `hmac.compare_digest`. La comparación
   directa de strings es vulnerable a timing attacks (aunque de explotación
   difícil sobre HTTP, es una inconsistencia de seguridad fácil de corregir).
   - **Fix sugerido**: `if not hmac.compare_digest(x_admin_key, settings.ADMIN_API_KEY):`

6. **`app/core/langgraph/odontoking_graph.py:375-382` — `GraphRecursionError` borra TODO el checkpoint del paciente.**
   ```python
   except GraphRecursionError:
       logger.warning("odontoking_recursion_limit_hit", wa_id=wa_id)
       try:
           await self.clear_history(wa_id)
   ```
   Esto borra `checkpoints`, `checkpoint_writes` y `checkpoint_blobs` para ese
   `thread_id` — el paciente pierde todo el contexto de la conversación
   (nombre, seguro, cita en progreso) cada vez que el grafo entra en bucle.
   Dado el bug #2 (sin recorte de historial), esta condición es más probable
   de lo deseable.
   - **Fix sugerido**: arreglar primero el recorte de historial (#2); como
     mitigación adicional, en vez de borrar todo el checkpoint, podría
     recortarse el `state.messages` a los últimos N mensajes y reinyectar el
     estado, preservando el contexto del paciente (nombre, seguro, etc. ya
     persistido en CRM).

7. **`app/api/v1/whatsapp.py:322-340,359-372` — Endpoints GET de verificación del webhook sin rate limiting.**
   `verify_webhook_tenant` y `verify_webhook_legacy` no tienen
   `@limiter.limit`. Son endpoints públicos (Meta los llama, pero también
   cualquiera puede golpearlos) que hacen `await get_tenant_async(tenant_slug)`
   → potencialmente una consulta a Redis + Postgres por request sin límite.
   - **Fix sugerido**: añadir `@limiter.limit("60 per minute")` o similar.

8. **Endpoints `/admin/*` y `/internal/usage` sin rate limiting.**
   Ningún endpoint en `app/api/admin/*.py` (`tenants.py`, `stats.py`,
   `conversations.py`, `billing.py`, `users.py`) ni
   `app/api/v1/internal.py:46-47` tiene `@limiter.limit`. Estos endpoints
   están protegidos por API key (`require_admin` / `_require_internal_key`),
   pero la regla del proyecto ("All routes must have rate limiting
   decorators") es explícita y sin excepciones, y un atacante con la key
   filtrada (o un loop de un agente externo mal configurado contra
   `/internal/usage`) podría generar carga ilimitada en la base de datos.
   - **Fix sugerido**: aplicar un límite generoso pero presente, p.ej.
     `@limiter.limit("120 per minute")` en cada router admin/internal.

### MEDIO

9. **`app/api/v1/whatsapp.py:395-404` — `DELETE /{tenant_slug}/history/{wa_id}` sin autenticación ni rate limit (ya en `todo/54`).**
   ```python
   @router.delete("/{tenant_slug}/history/{wa_id}")
   async def clear_history(tenant_slug: str, wa_id: str, request: Request) -> dict:
   ```
   Cualquiera que conozca un `wa_id` (número de teléfono, fácil de adivinar/enumerar
   en formato `591XXXXXXXX`) puede borrar el historial de checkpoint de ese
   paciente sin autenticación. El comentario dice "Dev/admin use only" pero no
   hay guard. Tampoco tiene `@limiter.limit`.
   - **Fix sugerido**: mover bajo `/admin` con `Depends(require_admin)`, o
     exigir `X-Admin-Key`, y añadir rate limit.

10. **`app/services/whatsapp_client.py` — Ninguna llamada HTTP a Graph API/Whisper usa `tenacity`.**
    `send_text_message`, `send_interactive_message`, `mark_as_read`,
    `send_typing_indicator`, `download_media`, `transcribe_audio` hacen un
    único intento `httpx` sin `@retry`. Para `send_text_message`/`send_response`
    (la respuesta final al paciente) un fallo transitorio de red hacia Graph
    API significa que el paciente **nunca recibe respuesta** aunque el LLM ya
    haya generado una. Viola la regla 3 (todos los retries vía tenacity).
    - **Fix sugerido**: envolver al menos `send_text_message` /
      `send_interactive_message` con
      `@retry(stop=stop_after_attempt(3), wait=wait_exponential(...), retry=retry_if_exception_type(httpx.TransportError))`.

11. **`app/core/langgraph/graph.py:122,178,255,479` y otros — `logger.error()` en bloques `except` sin `exc_info`/`logger.exception`.**
    Ejemplos:
    - `graph.py:122`: `logger.error("connection_pool_creation_failed", error=str(e), ...)`
    - `graph.py:178-184`: `logger.error("llm_call_failed_all_models", ...)` dentro de un `except Exception as e`.
    - `graph.py:255`: `logger.error("graph_creation_failed", ...)`.
    - `graph.py:479`: `logger.error("clear_chat_history_operation_failed", ...)`.
    - `services/database.py:65,258`, `services/llm/service.py:204,236,270,322,330,336`.

    Esto pierde el traceback (regla explícita: "Use `logger.exception()` not
    `logger.error()` for exceptions"). Nota: este código pertenece al flujo
    genérico (`graph.py`) que no está en el camino caliente de producción,
    pero igual es código mantenido y testeado, y viola la regla del proyecto.
    - **Fix sugerido**: cambiar a `logger.exception(...)` (sin pasar
      `error=str(e)` redundante, structlog ya captura el traceback).

12. **`app/api/v1/whatsapp.py:343-354` — el log `whatsapp_raw_payload` vuelca hasta 500 chars del body crudo del webhook, incluyendo posibles datos personales (nombre, número de teléfono, mensaje del paciente).**
    En producción (`LOG_FORMAT=json`, persistido en `logs/*.jsonl` y además
    reenviado por `alert_processor` si hay `logger.error`), esto puede
    almacenar PII de pacientes en logs sin necesidad, contradiciendo buenas
    prácticas de protección de datos para un cliente de salud dental
    (considerar normativa local de datos de salud).
    - **Fix sugerido**: loguear solo metadatos (tipo de mensaje, `msg.id`,
      longitud) o redactar el contenido en producción (`if settings.DEBUG`).

### BAJO

13. **`app/models/usage_log.py:17` y `app/models/tenant.py` — `id: int = Field(default=None, primary_key=True)`.**
    El tipo declarado es `int` pero el valor por defecto es `None`. SQLModel
    lo tolera en runtime, pero bajo `pyright` estricto esto es un error de
    tipo (`Optional[int]` esperado). Mismo patrón en otros modelos
    (`app/models/user.py:33` también usa `id: int = Field(default=None, ...)`).
    Si `make typecheck` corre en modo `standard` puede que no lo marque
    (depende de los stubs de SQLModel), pero es inconsistente con el resto del
    código que sí usa `Optional[int] = Field(default=None, ...)` (p.ej.
    `chat_history_odonto.py:14`).
    - **Fix sugerido**: usar `id: Optional[int] = Field(default=None, primary_key=True)`
      de forma consistente.

14. **`app/api/v1/whatsapp.py:343-354,374-389` — Logging de IP/headers ausente para detectar webhooks falsificados.**
    Meta firma los webhooks con `X-Hub-Signature-256` (HMAC sobre el body con
    el App Secret), pero no se ve ninguna verificación de firma en
    `_handle_webhook_payload` ni en `receive_message_tenant`/`receive_message_legacy`.
    Cualquiera que conozca la URL `/api/v1/whatsapp/{tenant_slug}/webhook`
    puede enviar payloads JSON arbitrarios que serán procesados como mensajes
    de WhatsApp reales (incluyendo llamadas a `update_crm`, `verify_insurance`,
    etc. con datos falsos). Esto es más un hallazgo de seguridad que de
    plataforma, pero impacta directamente el manejo de errores/validación de
    entrada de FastAPI.
    - **Fix sugerido**: validar `X-Hub-Signature-256` contra un `APP_SECRET`
      por tenant antes de procesar el payload.

---

## ⚠️ Violaciones de reglas del proyecto (AGENTS.md rules)

| Regla | Archivo:línea | Violación |
|---|---|---|
| Regla 3 (tenacity en todos los retries) | `app/core/langgraph/odontoking_graph.py:209-212` | Llamada al LLM principal sin `@retry`/fallback (ver Bug #1). |
| Regla 3 (tenacity en todos los retries) | `app/services/whatsapp_client.py` (todo el archivo) | Ninguna llamada HTTP usa `tenacity` (ver Bug #10). |
| Regla 5 (rate limiting en todas las rutas) | `app/api/v1/whatsapp.py:322-340` (`verify_webhook_tenant`), `:359-372` (`verify_webhook_legacy`), `:395-404` (`clear_history`) | Sin `@limiter.limit` (Bugs #7, #9). |
| Regla 5 (rate limiting en todas las rutas) | `app/api/v1/internal.py:46-47`, todos los endpoints en `app/api/admin/*.py` | Sin `@limiter.limit` (Bug #8). |
| "Use `logger.exception()` not `logger.error()`" | `app/core/langgraph/graph.py:122,178,255,479`, `app/services/database.py:65,258`, `app/services/llm/service.py:204,236,270,322,330,336`, `app/services/memory.py:92` | `logger.error()` dentro de bloques `except` sin traceback (Bug #11). |
| "Never hardcode secrets (use config.py)" — *espíritu de la regla* | `app/core/langgraph/tools/crm.py:18-23`, `odontoking.py:24-28`, `insurance.py:12-16` | No es un secreto hardcodeado literalmente, pero el token se "congela" en import-time desde `settings`, ignorando rotación y multi-tenancy (Bug #4). |
| Regla 6 (LLM ops con tracing Langfuse) | `app/core/langgraph/odontoking_graph.py:209-212` | Sí pasa `callbacks=config.get("callbacks", [])` → **esto SÍ cumple** (Langfuse llega), pero al no pasar por `llm_service` pierde las métricas `llm_inference_duration_seconds` que sí tiene `graph.py:157-158`. Mencionado aquí porque es una asimetría de observabilidad, no de tracing per se. |
| "All imports at top of file" | — | No se encontraron violaciones — imports diferidos existen (`tenant.py:102,113,122,131,152-154`, `whatsapp.py` ninguno) pero son imports diferidos **intencionales** para evitar import circular, documentados con comentarios. Técnicamente sigue siendo una violación literal de la regla; ver sección de mejoras. |
| "All DB ops async (asyncpg para LangGraph; sync SQLModel engine para CRUD)" | — | Respetado correctamente. `database_service` usa `Session`/`create_engine` síncrono envuelto en `async def` (documentado en `CLAUDE.md`), y `odontoking_graph.py`/`graph.py` usan `AsyncConnectionPool` + `AsyncPostgresSaver`. ✅ |

---

## 🔧 Mejoras / Deuda técnica

1. **Unificar el camino del LLM**: decidir si `odontoking_graph.py` debe migrar
   a usar `llm_service` (con su registro, fallback y métricas) o si
   `app/services/llm/*` y `app/core/langgraph/graph.py` (plantilla genérica)
   deben eliminarse del repo si ya no se usan en producción. Mantener ambos
   caminos vivos duplica la superficie de mantenimiento y ya generó el bug #1.

2. **Imports diferidos por import circular** (`app/core/tenant.py:102,113,122,131,152-154`,
   `app/core/broker.py:257,481`, `app/core/langgraph/odontoking_graph.py:308`
   `from app.services.memory import memory_service`). Aunque están bien
   documentados como necesarios para evitar ciclos, valdría la pena
   refactorizar la dependencia (p.ej. mover `memory_service`/`cache_service`
   a un módulo "leaf" sin dependencias hacia `tenant`/`broker`) para poder
   cumplir la regla 1 sin excepciones y simplificar el grafo de imports.

3. **`_pick_agent_user()` en `crm.py:33-39`** — asignación aleatoria de
   `user_id` del agente CRM (`random.choice(_AGENT_USERS_WEEKDAY/_AGENT_USERS_SUNDAY)`).
   Ya documentado en `todo/38`. Esto hace que cada vez que se actualiza un lead
   (`update_crm`), el "propietario" pueda cambiar aleatoriamente, lo que
   dificulta el seguimiento por el equipo de ventas/recepción.

4. **`update_crm` no es idempotente para citas confirmadas** (`crm.py:393-456`,
   ya en `todo/68`): si el LLM llama dos veces a `update_crm` con
   `es_cita_confirmada=True` (p.ej. tras un reintento del usuario o un bug de
   duplicidad de tool calls), se crearían dos `activities` tipo `meeting`
   duplicadas en el CRM. No hay verificación de "¿ya existe una cita en este
   `schedule_from` para este `lead_id`?" antes de `POST /activities`.

5. **`get_citas`/`update_crm`/`sync_transcript_to_crm` repiten el mismo
   patrón** de "buscar persona → buscar leads → filtrar por email" (~15 líneas
   duplicadas en `crm.py:280-298`, `:494-507`, `:615-628`). Extraer un helper
   `_find_lead_for_person(client, person_id, person_email)` reduciría
   duplicación y el riesgo de que una corrección se aplique en un solo lugar.

6. **`InMemoryBroker` (`broker.py:520-543`) y `InMemoryCacheService`/
   `_InMemoryMessageBuffer`**: válidos para desarrollo local, pero si Railway
   (mencionado en memoria del agente / `planning/06-e2e-tests-conversacion-railway.md`)
   corre con una sola instancia y sin `VALKEY_HOST`, el broker, el caché y el
   buffer son todos in-memory — esto es coherente para single-tenant, pero
   conviene documentar explícitamente en `docs/configuration.md` qué variables
   son **obligatorias** en producción para evitar pérdida de mensajes
   (`VALKEY_HOST` para buffer distribuido + recuperación tras crash).

7. **`app/core/config.py` no usa Pydantic Settings** pese a que `AGENTS.md`
   dice explícitamente "Use Pydantic Settings for type-safe configuration (see
   `app/core/config.py`)" y `CLAUDE.md`/docstring del módulo lo confirman como
   objetivo. La clase `Settings` actual es un `__init__` manual con `os.getenv`
   — funciona, pero no da validación de tipos/valores en startup (p.ej. un
   `POSTGRES_PORT="abc"` lanza `ValueError` críptico en vez de un error de
   Pydantic claro). No es bloqueante pero es deuda frente a la guía declarada.

8. **`app/core/langgraph/odontoking_graph.py:87` (`with open(_PROMPT_FILE, "r") as _f`)**
   — lectura de archivo síncrona a nivel de módulo (import time). Es aceptable
   para un archivo pequeño leído una vez, pero rompe ligeramente "Minimize
   blocking I/O operations" si se reimporta el módulo dinámicamente (no es el
   caso aquí, por lo que es BAJO impacto, solo lo señalo por completitud).

---

## 🕳️ Cosas que estamos pasando por alto

- **No hay verificación de firma de webhook de Meta** (`X-Hub-Signature-256`)
  — cualquiera con la URL puede inyectar mensajes falsos (ver Bug #14). Esto
  es crítico desde la óptica de seguridad pero también de **plataforma**:
  mensajes falsos consumirían cuota de OpenAI, escribirían en el CRM real
  (`update_crm`, `sync_transcript_to_crm`) y dispararían
  `send_text_message` hacia números arbitrarios usando el token del tenant.
- **El timeout de `asyncio.wait_for(..., timeout=settings.LLM_TOTAL_TIMEOUT + 30)`
  en `whatsapp.py:131-140` envuelve TODO `agent.get_response(...)`**, incluido
  `ensure_person_registered` (ya resuelto antes, fuera del timeout) pero
  también las llamadas a herramientas (CRM, Odontoking API, etc.) dentro del
  grafo. Si una tool tarda 50s (p.ej. `get_doctor_schedule` con sus 3 reintentos
  de hasta 10s c/u = ~30s) y el LLM tarda otros 30s, se puede superar el
  timeout total de 90s y el usuario recibe `_TIMEOUT_MSG` aunque el CRM ya haya
  sido modificado (operación no transaccional, efectos secundarios persisten).
- **No hay un "circuit breaker" o health-check hacia `ODONTOKING_API_URL`**:
  si la API de Sofopolis/Odontoking cae, cada turno de cada paciente intentará
  3 reintentos con backoff (en `get_doctor_schedule`, `verify_insurance`) antes
  de fallar — multiplicando la latencia para todos los usuarios
  simultáneamente sin ningún mecanismo de "fail fast" agregado.
- **El worker (`app/worker.py`) no fue revisado en profundidad** en este pase
  (fuera del alcance explícito), pero importa `OdontokingAgent` directamente
  (`app/worker.py:34`) — si el worker corre en un proceso separado del API,
  cada proceso mantiene su propio `_connection_pool` y su propio
  `_persist_tasks` set; conviene confirmar que `agent.close()` se llama
  también en el shutdown del worker (no solo en `main.py:99`).
- **`_seen_message_ids` (dedupe) y `_wa_message_times` (rate limit) en
  `whatsapp.py:50-58,91-98` son dicts en memoria de proceso** — en un
  despliegue con múltiples réplicas (horizontal scaling), cada réplica tiene
  su propio estado de dedupe/rate-limit, por lo que un mensaje duplicado
  enviado por Meta a dos réplicas distintas **no sería detectado como
  duplicado**. Esto contradice el diseño "Plan B multi-tenant" si se escala
  horizontalmente. Documentado parcialmente en comentarios pero no resuelto.
- **El campo `Tenant.crm_token`/`crm_url` existe en el modelo y en
  `TenantConfig`, pero ninguna tool de `app/core/langgraph/tools/` lo recibe
  como parámetro** — confirma que la arquitectura multi-tenant "Plan B" está a
  medio camino: el routing del webhook es multi-tenant, pero el agente y sus
  tools siguen 100% hardcodeados a la configuración global de Odontoking.

---

## ✨ Nuevas funcionalidades propuestas

1. **Endpoint de salud específico del agente** (`/health/agent` o ampliar
   `/health`): actualmente `health_check` en `main.py:203-244` verifica DB,
   cache y buffer, pero no verifica si `odontoking_agent._connection_pool` está
   vivo ni si la API de Odontoking responde (`GET /api/v1/products` con
   timeout corto). Útil para alertas tempranas antes de que un paciente real
   reciba un error.

2. **Idempotencia en `update_crm`** mediante una tabla local
   (`appointments` o reutilizar `usage_logs`-like) que registre
   `(lead_id, schedule_from)` ya sincronizados, para evitar duplicados (ver
   mejora #4) y permitir reconciliación si el CRM externo fallara
   parcialmente.

3. **Job periódico de "purga de contexto"**: en vez de borrar todo el
   checkpoint en `GraphRecursionError` (Bug #6), un cron/worker que recorte
   periódicamente `state.messages` a los últimos N turnos por `thread_id`,
   preservando un resumen (`long_term_memory` vía mem0, ya parcialmente
   implementado) — esto ataca directamente los Bugs #2 y #6 de forma proactiva.

4. **Verificación de firma de webhook (`X-Hub-Signature-256`) por tenant**,
   usando `tenant.agent_api_key` o un nuevo campo `app_secret` en `Tenant`,
   con `hmac.compare_digest` (mismo patrón ya usado en `internal.py`).

5. **Dashboard/endpoint admin de "modelo activo"**: dado que `llm_service`
   tiene fallback circular (`_switch_to_next_model`), exponer en
   `/admin/stats` o similar el `_current_model_index` actual y el historial de
   *switches* recientes (ya se loguea con `model_switched`, solo falta
   agregarlo) — ayudaría a detectar degradaciones de OpenAI en tiempo real.
   (Aplica una vez resuelto el Bug #1, cuando `odontoking_graph` también use
   `llm_service`.)

---

## 📋 Prioridades

| Hallazgo | Severidad | Esfuerzo | Impacto |
|---|---|---|---|
| #1 — `OdontokingAgent` sin retry/fallback/timeout en LLM | CRÍTICO | Medio (refactor para usar `llm_service` o añadir `@retry` local) | Alto — afecta disponibilidad de cada conversación real |
| #2 — Sin recorte de historial en `odontoking_graph._chat` | CRÍTICO | Bajo (reusar `prepare_messages`/`trim_messages`) | Alto — costo, latencia, recursión |
| #3 — `DEFAULT_LLM_MODEL=gpt-5-mini` no está en `LLMRegistry` | CRÍTICO | Trivial (alinear default o agregar entrada al registry) | Medio — confuso pero hoy "funciona por accidente" |
| #14 — Sin verificación de firma de webhook Meta | ALTO (seguridad) | Medio | Alto — integridad de datos del CRM y costos LLM |
| #4 — `_HEADERS`/`_BASE` congelados en import, ignoran `TenantConfig` | ALTO | Medio | Alto para roadmap multi-tenant; bajo para single-tenant actual |
| #6 — `GraphRecursionError` borra todo el checkpoint | ALTO | Bajo-Medio | Alto — pérdida de contexto del paciente |
| #7/#8 — Rate limiting faltante en webhooks GET, `/internal`, `/admin/*` | ALTO | Bajo (decoradores) | Medio-Alto — protección DoS/DB |
| #9 — `DELETE /history/{wa_id}` sin auth | MEDIO | Bajo | Medio — borrado no autorizado de historial |
| #10 — Sin `tenacity` en `whatsapp_client.py` | MEDIO | Bajo | Medio — mensajes "perdidos" por fallos transitorios de Graph API |
| #5 — `require_admin` usa `!=` en vez de `hmac.compare_digest` | ALTO (seguridad, fix trivial) | Trivial | Bajo-Medio |
| #11 — `logger.error` en vez de `logger.exception` | MEDIO | Bajo | Bajo-Medio — dificulta debugging |
| #12 — PII en logs de `whatsapp_raw_payload` | MEDIO | Bajo | Medio — cumplimiento de datos |
| #13 — Tipos `id: int = Field(default=None, ...)` | BAJO | Trivial | Bajo |
| Mejora #3 — `_pick_agent_user()` aleatorio | BAJO/MEDIO (debt) | Bajo | Medio para operación de ventas |
| Mejora #4 — `update_crm` no idempotente | MEDIO | Medio | Medio-Alto (citas duplicadas) |
| Nueva func. #1 — health-check del agente/Odontoking API | MEJORA | Bajo | Medio — observabilidad proactiva |
| Nueva func. #3 — purga periódica de contexto | MEJORA | Medio | Alto (mitiga #2 y #6 de forma sistémica) |
