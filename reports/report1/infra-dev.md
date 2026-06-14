# Infra-Dev — Reporte de Revisión del Proyecto

**Proyecto**: 01.odontoking — Agente WhatsApp dental (FastAPI + LangGraph + mem0/pgvector + Postgres + Redis/Valkey)
**Alcance**: capa de datos/infraestructura — modelos, migraciones, conexiones DB, memoria pgvector, caché, broker, docker-compose.

---

## Resumen ejecutivo (3-5 bullets)

- **El proyecto abrió tres pools de conexión independientes a la misma instancia de Postgres** (SQLModel sync `QueuePool` 20+10, `LangGraphAgent._connection_pool` asyncpg max=20, `OdontokingAgent._connection_pool` asyncpg max=20), todos dimensionados con la misma variable `POSTGRES_POOL_SIZE`. En el peor caso un solo proceso API puede abrir ~70 conexiones más el pool interno de mem0/pgvector (5 más) — riesgo real de agotar `max_connections` de Postgres con 1-2 réplicas (`app/services/database.py:48-56`, `app/core/langgraph/graph.py:108-118`, `app/core/langgraph/odontoking_graph.py:169-187`).
- **Decenas de endpoints `async def` ejecutan `with Session(database_service.engine)` (motor SÍNCRONO) directamente en el event loop**, sin `asyncio.to_thread` ni `run_in_executor` — esto bloquea el loop de FastAPI en cada request a `/admin/*`, `/internal/usage`, `get_tenant_async()` (llamado en CADA webhook de WhatsApp) y `crm.py`. Bajo carga esto degrada la latencia de TODOS los requests concurrentes, no solo el que hace la query.
- **No existe ninguna política de retención/limpieza para `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` ni `chat_histories_odonto`** — crecen indefinidamente. El único mecanismo de borrado es manual (`clear_history` / endpoint admin DELETE), por lo que en producción estas tablas crecerán sin límite.
- **mem0/pgvector usa `collection_name="longterm_memory"` global compartida entre TODOS los tenants** (no hay namespacing por tenant en el vector store, solo por `user_id`/`wa_id` dentro de la colección) — funcionalmente correcto para un solo tenant activo (`odontoking`), pero es deuda técnica si se onboardean más tenants con el `agent_type=odontoking`.
- **Las migraciones Alembic son correctas y reversibles** (down_revision encadenados correctamente, `downgrade()` presente en las 4), pero falta un índice compuesto en `chat_histories_odonto (session_id, created_at)` que es exactamente el patrón de consulta usado por `get_conversation` (admin) y por la persistencia de mensajes.

---

## 🐛 Bugs / Riesgos de datos (severidad, archivo:línea, fix)

### CRÍTICO

1. **`get_tenant_async()` ejecuta una query SQLModel síncrona dentro de un endpoint async, en el path crítico de CADA mensaje de WhatsApp entrante.**
   `app/core/tenant.py:149-161` (`_db_get`) → `with Session(database_service.engine) as session: session.exec(select(Tenant)...)`. Esto se llama desde `get_tenant_async()` (línea 168-180), que a su vez se invoca en `verify_webhook_tenant` y `receive_message_tenant` (`app/api/v1/whatsapp.py:330,347`) en CADA request del webhook.
   - **Impacto**: bloquea el event loop de uvicorn por la duración de la query (red + Postgres). Con tráfico simultáneo de varios pacientes, todos los requests (incluyendo `/health`) se serializan detrás de esta query.
   - **Mitigación parcial existente**: hay caché Redis con TTL 300s (`TENANT_CACHE_TTL`), así que en estado estable el cache-hit evita la DB. Pero en cold-start, cache-miss tras invalidación, o sin Redis configurado, **cada webhook bloquea el loop**.
   - **Fix**: envolver `_db_get` en `asyncio.to_thread(...)`, o mejor, migrar a una sesión async (`AsyncSession` de SQLAlchemy con `asyncpg`) para esta tabla pequeña y de alta frecuencia de lectura.

2. **Mismo patrón en `app/api/admin/*.py` y `app/api/v1/internal.py`**: 13 endpoints `async def` (`tenants.py:120,130,162,174,218,233`; `conversations.py:98,143,174`; `billing.py:43,96`; `users.py:39,54,74`; `stats.py:23,65`; `internal.py:51`) usan `with Session(database_service.engine)` síncrono. `/internal/usage` es llamado por servicios de agentes externos potencialmente con alta frecuencia (cada mensaje procesado) — bloqueo del loop en producción.
   - **Fix**: mismo patrón — `asyncio.to_thread` como parche rápido, o introducir un segundo engine async (`create_async_engine` + `asyncpg`) para CRUD relacional simple, dejando el sync engine solo para scripts/Alembic.

3. **Triple pool de conexiones contra el mismo Postgres, sin coordinación de tamaño total.**
   - `app/services/database.py:48-56` → `QueuePool(pool_size=POSTGRES_POOL_SIZE, max_overflow=POSTGRES_MAX_OVERFLOW)` = 20+10 = 30 conexiones máx (sync).
   - `app/core/langgraph/graph.py:100-118` → `AsyncConnectionPool(max_size=POSTGRES_POOL_SIZE)` = 20 (LangGraphAgent, agente "template" — ¿se usa realmente en este proyecto? ver sección Cosas que pasamos por alto).
   - `app/core/langgraph/odontoking_graph.py:169-187` → otro `AsyncConnectionPool(max_size=POSTGRES_POOL_SIZE)` = 20 (OdontokingAgent, el agente real).
   - mem0/pgvector internamente abre su propio `psycopg_pool.ConnectionPool(maxconn=5)` (`.venv/lib/python3.13/site-packages/mem0/vector_stores/pgvector.py:104`).
   - **Total teórico por proceso**: 30 + 20 + 20 + 5 = **75 conexiones**. Con `.env.example` (`POSTGRES_POOL_SIZE=20`) y un Postgres `max_connections` default de 100, **dos réplicas del API ya superan el límite**, sin contar el worker (`app/worker.py`) que abre su propio pool de OdontokingAgent.
   - **Fix**: definir variables de pool independientes por consumidor (`POSTGRES_SYNC_POOL_SIZE`, `POSTGRES_CHECKPOINT_POOL_SIZE`), bajar los `max_size` (5-8 es suficiente para checkpointer en este volumen), y documentar el presupuesto total de conexiones por instancia vs `max_connections` de Postgres (recomendado subir a 200 si se usa pgbouncer, o usar pgbouncer en modo transaction).

### ALTO

4. **`LangGraphAgent` (`app/core/langgraph/graph.py`) parece código "template" no usado por el flujo real de WhatsApp**, pero su pool se pre-calienta en el lifespan (`app/main.py:64-68: await agent.create_graph()`), abriendo un `AsyncConnectionPool` completo (hasta 20 conexiones) que probablemente nunca se usa en producción (el flujo real usa `odontoking_agent`, ver `app/api/v1/whatsapp.py:27,73`). Esto desperdicia conexiones de Postgres de forma permanente.
   - **Fix**: confirmar si `chatbot.py`/`LangGraphAgent` sigue siendo parte del producto (¿endpoint `/chat` genérico todavía expuesto?). Si no, no pre-calentar su pool, o reducir `max_size` a 2-3.

5. **No hay índice compuesto `(session_id, created_at)` en `chat_histories_odonto`.**
   `app/models/chat_history_odonto.py:15` solo indexa `session_id` (vía `Field(index=True)`, ver `352d5a24eefc...py:32`). Pero `get_conversation` (`app/api/admin/conversations.py:148-152`) hace `WHERE session_id = sid ORDER BY created_at` — con un índice solo en `session_id`, Postgres debe ordenar en memoria/disco para conversaciones largas. Para un agente WhatsApp con cientos de mensajes por paciente a lo largo de meses, esto degradará con el tiempo.
   - **Fix**: nueva migración `op.create_index("ix_chat_histories_odonto_session_created", "chat_histories_odonto", ["session_id", "created_at"])`.

6. **Sin retención/TTL para tablas de checkpoint de LangGraph (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`).**
   `AsyncPostgresSaver` (`app/core/langgraph/odontoking_graph.py:261-262`) escribe un checkpoint completo del estado de la conversación (incluyendo TODO el historial de mensajes serializado) en cada turno del grafo. No hay job de limpieza — `checkpoint_blobs` puede crecer muy rápido (cada mensaje = nuevo blob con el array de mensajes completo, no solo el delta, dependiendo de la implementación de `AsyncPostgresSaver`).
   - **Riesgo**: para una clínica con cientos de pacientes activos y conversaciones largas (recordemos `GraphRecursionError` se maneja limpiando el historial — `odontoking_graph.py:375-382` — lo cual sugiere que esto YA ocurre en producción), `checkpoint_blobs` puede llegar a ser la tabla más pesada de la DB sin que nadie lo note.
   - **Fix**: cron/job periódico que borre checkpoints de threads inactivos > N días (`DELETE FROM checkpoints WHERE thread_id IN (SELECT ... WHERE last_message_at < now() - interval '90 days')`), o usar `checkpointer.adelete_thread()` si la versión de langgraph lo soporta.

7. **`alembic/env.py` excluye `longterm_memory` y `mem0migrations` de `EXCLUDE_TABLES` (línea 45-46), pero estas tablas pgvector son creadas por mem0 en runtime (`list_cols()` / `create_col()` en `pgvector.py:109-111`), NO por Alembic.** Si `ODONTOKING_MEMORY_ENABLED=false` (default actual en `.env.development` y `.env.example`, línea 72/35), la tabla `longterm_memory` y la extensión `pgvector` **nunca se crean**. Esto está bien mientras memory esté deshabilitada, pero es un riesgo de "funciona en mi env de desarrollo, falla en prod" si alguien activa `ODONTOKING_MEMORY_ENABLED=true` sin verificar que la extensión `CREATE EXTENSION vector` esté habilitada en la DB de destino (el `docker-compose.yml` usa `pgvector/pgvector:pg16`, que sí la trae preinstalada — pero no se ejecuta `CREATE EXTENSION` explícitamente en ninguna migración).
   - **Fix**: agregar una migración Alembic temprana con `op.execute("CREATE EXTENSION IF NOT EXISTS vector")` — idempotente, documenta el requisito, y evita fallos silenciosos de mem0 en `_get_memory()` (`app/services/memory.py:46`) cuando se activa memory en un entorno donde la extensión no fue habilitada manualmente.

### MEDIO

8. **`Tenant.is_active` no tiene índice**, pero se filtra constantemente: `app/api/admin/stats.py:24` (`WHERE is_active == True`), `app/api/admin/billing.py:97`, `app/core/tenant.py:156`. Con pocos tenants (single-tenant real hoy) es irrelevante, pero si el modelo crece a docenas de tenants, un índice parcial `WHERE is_active = true` ayudaría. Severidad baja hoy, pero documentar.

9. **`UsageLog.cost_usd`: tipo `Numeric(10,4)` en la migración (`c3f1a2b4d5e6...py:63`) pero el modelo SQLModel define `cost_usd: float` (`app/models/usage_log.py:27`)**. Esto es una discrepancia de tipos — SQLModel/SQLAlchemy mapeará `float` a `Float`/`DOUBLE PRECISION` en autogenerate futuro, lo que podría generar una migración de alteración de columna no deseada (`ALTER COLUMN cost_usd TYPE double precision`) la próxima vez que se corra `make migration`. Además, `internal.py:76` hace `round(log.cost_usd + body.cost_usd, 6)` — redondeo a 6 decimales sobre una columna `NUMERIC(10,4)`, perdiendo precisión silenciosamente (Postgres truncará a 4 decimales en el INSERT/UPDATE).
   - **Fix**: o bien usar `Decimal` en el modelo SQLModel (`from decimal import Decimal; cost_usd: Decimal = Field(..., sa_column=Column(Numeric(10,4)))`) para que coincida con la columna real, o cambiar la migración a `DOUBLE PRECISION` y eliminar el `round(..., 6)` engañoso. Hoy hay un mismatch silencioso entre lo que el ORM "cree" que es la columna y lo que realmente es.

10. **`message.cost_usd` columna `Numeric(10,4)` con `server_default="0"`** — funciona, pero el límite de 10 dígitos totales / 4 decimales permite un máximo de `999999.9999` USD por (tenant, día). Para el caso de uso actual (clínica dental, mensajes baratos) es más que suficiente; mencionado solo como nota de diseño, no acción requerida.

### BAJO

11. **`save_chat_message` (`app/services/database.py:230-236`) es un método NO async (`def`, no `async def`) pero se llama desde `_persist_messages_async` vía `asyncio.to_thread` (`odontoking_graph.py:139-140`)** — esto está bien hecho (correcto patrón), solo señalo la inconsistencia de naming: el resto de `DatabaseService` usa `async def` aunque internamente sea síncrono (documentado en CLAUDE.md), mientras este método es honestamente síncrono. Sugerencia cosmética: documentarlo con un comentario para que nadie lo "arregle" agregando `async` sin el `to_thread`.

12. **`ChatHistoryOdonto.message` es `Text` sin límite — correcto para JSON serializado de mensajes**, pero no hay validación de tamaño máximo. Un mensaje con tool_calls muy grandes (p.ej. resultado de `get_doctor_schedule` con muchos horarios) podría generar filas de varios KB. No es un bug, pero vale la pena monitorear el tamaño promedio de fila con `pg_column_size`.

---

## 🗄️ Esquema / Migraciones (problemas, índices faltantes, reversibilidad)

**Estado general: BUENO.** Las 4 migraciones (`b25d38b0cd7c` → `352d5a24eefc` → `c3f1a2b4d5e6` → `d4e5f6a7b8c9`) forman una cadena lineal correcta, cada una con `downgrade()` funcional y simétrico. `alembic/env.py` filtra correctamente las tablas externas (`checkpoint_*`, `longterm_memory`, `mem0migrations`) vía `include_object` (líneas 40-54), lo cual es la práctica correcta para convivir con LangGraph/mem0.

Hallazgos específicos:

- **`b25d38b0cd7c` (initial schema)**: `session.user_id` tiene FK a `user.id` sin `ondelete`. Si se borra un `User` que tiene sesiones, fallará por violación de FK (comportamiento RESTRICT por default) — `app/services/database.py:116-133` (`delete_user_by_email`) no borra primero las sesiones del usuario, así que **borrar un usuario con sesiones existentes lanzará `IntegrityError`** sin manejo explícito (la `SQLAlchemyError` no se captura ahí). Esto es más un bug funcional que de migración, pero su origen es el esquema. Fix: `ondelete="CASCADE"` en la FK, o borrar sesiones explícitamente antes del usuario.

- **`352d5a24eefc` (chat_histories_odonto + thread)**: el modelo `Thread` (`app/models/thread.py`) se crea en esta migración pero **no se usa en ningún servicio** (`grep` no encontró referencias activas a `Thread` fuera de `app/models/database.py` y `alembic/env.py`). Posible resto de un scaffold genérico (template FastAPI+LangGraph) sin limpiar. No es un riesgo de datos, pero es ruido de esquema — considerar eliminarlo en una migración futura si se confirma que no se usa.

- **`c3f1a2b4d5e6` (Fase 4 — tenants/usage_logs)**: bien diseñada — `UniqueConstraint("tenant_id", "log_date")` para upsert idempotente diario, `ondelete="CASCADE"` correcto en `usage_logs.tenant_id → tenants.id`. Falta: índice en `tenants.is_active` (ver hallazgo MEDIO #8). El `op.create_index("ix_tenants_slug", ...)` es redundante con el `UniqueConstraint("slug")` ya declarado en `create_table` — Postgres ya crea un índice único implícito para el `UniqueConstraint`, así que `ix_tenants_slug` es un índice duplicado (no rompe nada, pero desperdicia espacio/escritura). Verificar con `\di+ tenants` en producción y considerar `op.drop_index` si es efectivamente redundante.

- **`d4e5f6a7b8c9` (agent_endpoint_url / agent_api_key)**: migración trivial y correcta, columnas nullable, downgrade simétrico.

- **Índice faltante (ya mencionado en bugs)**: `chat_histories_odonto (session_id, created_at)` compuesto.

- **Zero-downtime**: las migraciones actuales son compatibles con despliegues sin downtime (solo `ADD COLUMN nullable` o con `server_default`, `CREATE TABLE`, `CREATE INDEX` no concurrente). **Ojo**: `op.create_index` sin `postgresql_concurrently=True` toma un lock `SHARE` que bloquea escrituras en la tabla durante la construcción del índice. Para `chat_histories_odonto` (tabla de alta escritura), si en el futuro se agrega el índice compuesto recomendado, usar:
  ```python
  op.create_index(
      "ix_chat_histories_odonto_session_created",
      "chat_histories_odonto",
      ["session_id", "created_at"],
      postgresql_concurrently=True,
  )
  ```
  y correr esa migración con `op.execute("COMMIT")` antes (Alembic no permite `CREATE INDEX CONCURRENTLY` dentro de una transacción) — esto requiere `with op.get_context().autocommit_block():`.

---

## 🔌 Connection pooling & lifespan

- **`DatabaseService` (sync, `app/services/database.py:48-56`)**: `QueuePool`, `pool_pre_ping=True` (bueno — detecta conexiones muertas), `pool_recycle=1800` (bueno — evita conexiones zombie tras 30 min, útil con proxies/firewalls que cierran idle connections). `pool_timeout=30`. Configuración sólida para un pool sync.
  - **Riesgo de inicialización**: si `create_engine` lanza `SQLAlchemyError` en producción (`except SQLAlchemyError as e: ... if settings.ENVIRONMENT != PRODUCTION: raise`), en producción el error se traga y **`self.engine` queda sin asignar** (`AttributeError` en el primer uso, no una excepción clara de "DB no disponible"). El comentario dice "allow app to start even with DB issues" pero el resultado es un `AttributeError` confuso más adelante en vez de un health check claro. Fix: asignar `self.engine = None` en el except y chequear `is None` en `health_check()` y en cada método público.

- **`LangGraphAgent._connection_pool` (`app/core/langgraph/graph.py:90-128`)**: `AsyncConnectionPool(open=False, ...)` + `await pool.open()` — correcto (evita el warning de psycopg_pool sobre abrir en `__init__`). `autocommit=True`, `prepare_threshold=None` (deshabilita prepared statements — correcto para compatibilidad con pgbouncer en modo transaction si se usa). **No tiene `min_size`** (default de psycopg_pool es `min_size == max_size` si no se especifica `min_size`... en realidad el default de `AsyncConnectionPool` es `min_size=4`) — confirmar que esto no abre 4 conexiones de golpe en el lifespan para un agente que (según hallazgo ALTO #4) podría no usarse.

- **`OdontokingAgent._connection_pool` (`app/core/langgraph/odontoking_graph.py:161-192`)**: mejor configurado que el anterior — `min_size=1`, `reconnect_timeout=30`, y **TCP keepalives explícitos** (`keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5`), lo cual es una buena práctica para conexiones de larga duración a Postgres detrás de balanceadores cloud (Railway/Neon/etc. cierran conexiones idle agresivamente). `await pool.open(wait=True, timeout=30)` — espera explícitamente a que el pool esté listo, buen patrón para fail-fast en startup.
  - Inconsistencia: este pool SÍ tiene keepalives, el de `LangGraphAgent` NO. Si `LangGraphAgent` se mantiene, alinear configuración.

- **Lifespan (`app/main.py:46-108`)**: orden de cleanup correcto y bien comentado — `message_buffer_service.close()` → `odontoking_agent.close()` (espera tareas de persistencia pendientes) → `cache_service.close()` → `broker.close()` → `agent._connection_pool.close()`. Buen manejo de "tareas en vuelo" (`_persist_tasks` en `OdontokingAgent.close()`, `_background_tasks` en `MessageBufferService.close()`).
  - **Falta**: `memory_service` (mem0/pgvector) **no se cierra explícitamente en el shutdown**. mem0's `PGVector` mantiene su propio `ConnectionPool` (maxconn=5) — al no cerrarlo, esas conexiones quedan abiertas hasta que el proceso termine (en la práctica no es grave porque el proceso sí termina, pero en hot-reload de desarrollo (`make dev`) puede acumular pools huérfanos). Fix: agregar un método `close()` a `MemoryService` que llame `self._memory.vector_store.connection_pool.close()` si existe, e invocarlo en el lifespan.

- **`agent._connection_pool` vs `odontoking_agent` en shutdown**: el lifespan cierra `agent._connection_pool` (LangGraphAgent, línea 105-107) explícitamente, y `odontoking_agent.close()` (línea 99) que internamente cierra su propio pool. Correcto, ambos se cierran. Bien.

- **`database_service.engine` (sync QueuePool) nunca se cierra explícitamente en el lifespan** — `engine.dispose()` no se llama en shutdown. SQLAlchemy lo limpia al terminar el proceso, pero en tests/hot-reload puede dejar conexiones colgando hasta el GC. Menor, pero fácil de agregar: `database_service.engine.dispose()` en el bloque de cleanup.

---

## 🧠 Memoria (pgvector/mem0) & Caché (TTL, namespacing)

### Memoria (mem0 + pgvector)

- `app/services/memory.py` — diseño limpio: lazy init (`_get_memory`), pre-warm opcional (`initialize()`, llamado solo si `ODONTOKING_MEMORY_ENABLED=true`, `app/main.py:71-75`), maneja tanto API sync como async de mem0 (`inspect.isawaitable`).
- **Colección única `longterm_memory`** (`settings.LONG_TERM_MEMORY_COLLECTION_NAME`, default `"longterm_memory"`) — namespacing de usuarios se hace vía `user_id` (que aquí es `wa_id`, el número de WhatsApp) dentro de la misma tabla/colección de mem0. Para un solo tenant (`odontoking`) esto es correcto. **Si se agregan más tenants con `agent_type=odontoking`**, todos compartirían la misma colección — los `wa_id` son globalmente únicos (números de teléfono) así que no hay colisión de datos entre tenants, pero **no hay forma de hacer "borrar todos los datos del tenant X"** sin iterar por `wa_id`. Documentar como deuda si el modelo de negocio crece a multi-tenant real.
- **`search()` usa caché ANTES de pgvector** (`app/services/memory.py:74-90`): clave = `cache_key("memory", str(user_id), query)` — el hash SHA256 de `user_id:query` (16 hex chars). **Problema de cache hit-rate**: dado que `query` es el texto literal del último mensaje del usuario (`messages[-1].content`, ver `app/core/langgraph/graph.py:314` y `odontoking_graph.py:314`), y los usuarios de WhatsApp raramente repiten el mensaje exacto, **el cache-hit-rate de memoria será muy bajo en la práctica** — la mayoría de las búsquedas de memoria van a pgvector de todas formas. El TTL de 60s (`CACHE_TTL_SECONDS` default) ayuda solo si el mismo usuario reenvía el mismo texto en <60s (p.ej. por el message buffer/debounce, donde varios mensajes se combinan — pero el combinado rara vez es idéntico).
  - **No es un bug**, pero vale la pena medir el hit-rate real; si es <5%, el caché de memoria aporta poco y podría simplificarse o cambiarse a cachear por `user_id` solamente (ignorando `query`) con invalidación al hacer `add()`.
- **Solo se cachean resultados no vacíos** (`if result: await cache_service.set(...)`, línea 87-88) — correcto según la regla "cachear solo éxitos" del proyecto. Pero el caso "no hay memoria relevante" (resultado vacío `""`) **nunca se cachea**, así que cada query sin memoria relevante vuelve a golpear pgvector — para usuarios nuevos (la mayoría en una clínica dental con alta rotación de pacientes nuevos) esto es notable.

### Caché (Valkey/Redis vs in-memory)

- `app/core/cache.py` — buen diseño con interfaz dual (`InMemoryCacheService` / `ValkeyCacheService`), selección automática vía `_create_cache_service()` (líneas 192-209) basada en `VALKEY_HOST` + disponibilidad del paquete `redis`.
- **TTL único global** `CACHE_TTL_SECONDS=60` para TODO lo que pasa por `cache_service` — esto incluye memoria semántica (`memory:*`) Y `tenant:config:*` (vía `app/core/tenant.py:117`, que pasa `ttl=settings.TENANT_CACHE_TTL=300` explícitamente, sobreescribiendo el default). Mezclar dos dominios de caché muy distintos (config de tenant, semi-estática, TTL 5min; resultados de búsqueda semántica, TTL 60s) en el mismo backend está bien, pero **no hay namespacing de keys por dominio más allá del prefijo** (`cache_key("memory", ...)` → `memory:<hash>`, `tenant:config:<slug>` con prefijo manual hardcodeado en `tenant.py:93`). Esto es funcional pero inconsistente: un dominio usa el helper `cache_key()`, el otro construye el string a mano. Sugerencia: unificar bajo `cache_key("tenant_config", slug)`.
- **`InMemoryCacheService`**: `dict` simple en memoria de proceso — correcto para single-instance dev, pero **si se despliega sin `VALKEY_HOST` en producción con múltiples réplicas**, cada réplica tiene su propia caché de tenant (TTL 5min) y de memoria — inconsistencia entre réplicas (poco grave, solo afecta latencia/cache-hit, no correctness, porque el fallback siempre va a DB/pgvector).
- **No hay invalidación de `cache_service` al cerrar (`close()`)** para `InMemoryCacheService.close()` — sí existe (`self._cache.clear()`, línea 92-94), correcto.
- **`message_buffer.py` y `broker.py` (Redis Streams) usan el MISMO host/puerto Redis (`VALKEY_HOST`) pero con clientes Redis SEPARADOS** — `MessageBufferService.initialize()` crea un `Redis(...)` (línea 177-183), `create_broker()` crea OTRO `Redis(...)` (`app/core/broker.py:557-563`), y `ValkeyCacheService.initialize()` crea un TERCER `Redis(...)` (`app/core/cache.py:111-118`). Tres pools de conexión Redis independientes (cada uno con su propio `max_connections`) contra la misma instancia Valkey. No es incorrecto, pero podría consolidarse en un único cliente Redis compartido (inyectado) para reducir el número de conexiones TCP y simplificar el lifecycle/health-check.

### Namespacing de keys Redis (resumen)
| Dominio | Patrón de key | TTL | Archivo |
|---|---|---|---|
| Memoria semántica | `memory:<sha256[:16]>` | 60s (CACHE_TTL_SECONDS) | `cache.py:212-224`, `memory.py:74` |
| Config de tenant | `tenant:config:<slug>` | 300s (TENANT_CACHE_TTL) | `tenant.py:92-93,117` |
| Índice de teléfono (declarado, no usado) | `tenant:phone_index` | — | `tenant.py:96-98` (función `_phone_index_key` definida pero nunca llamada — código muerto) |
| Buffer entrante | `wa_incoming:<wa_id>` | debounce+LLM_TIMEOUT+30 | `message_buffer.py:82,89` |
| Lock de worker | `wa_worker:<wa_id>` | mismo TTL que arriba | `message_buffer.py:84-89` |
| Stream de mensajes | `wa:<tenant_slug>` | maxlen=10000 (no TTL, trim por tamaño) | `broker.py:60,70,134` |
| DLQ | `wa:dlq:<tenant_slug>` | sin TTL (lista, crece indefinidamente) | `broker.py:61,74,253` |
| Retry counter | `wa:retry:<stream_id>` | sin TTL explícito (se borra en ack/dlq) | `broker.py:62,78,199,255` |

**Hallazgo**: `wa:dlq:<tenant_slug>` (Redis List) **no tiene TTL ni límite de tamaño** — `_send_to_dlq` hace `lpush` sin `ltrim` (`broker.py:253`). Si el agente downstream falla persistentemente (p.ej. OpenAI caído por horas), la DLQ puede crecer sin límite y consumir memoria de Redis. Fix: `LTRIM` a un máximo razonable (p.ej. 1000) tras cada `LPUSH`, y/o agregar alerta de profundidad de DLQ (ya existe `dlq_depth` en `stats.py:84-85` pero sin alerta automática more allá del email de `notify()`).

---

## 🔧 Mejoras / Deuda técnica

1. **Unificar acceso a Postgres**: introducir un segundo engine async (`sqlalchemy.ext.asyncio.create_async_engine` con `asyncpg` o `psycopg[async]`) para los CRUD relacionales (`tenants`, `usage_logs`, `users`, `sessions`, `chat_histories_odonto`) usados en rutas `async def`, eliminando los `with Session(database_service.engine)` bloqueantes. Mantener el sync engine solo para Alembic y scripts CLI.

2. **Centralizar configuración de pools**: una variable por consumidor (`DB_POOL_SYNC_SIZE`, `DB_POOL_CHECKPOINT_SIZE`, `DB_POOL_MEMORY_SIZE`) en vez de reusar `POSTGRES_POOL_SIZE` para tres pools distintos. Documentar el presupuesto total esperado vs `max_connections` de Postgres en el `.env.example`.

3. **Job de retención de checkpoints**: cron (o tarea periódica con `asyncio` + `asyncio.sleep`) que limpie `checkpoints`/`checkpoint_writes`/`checkpoint_blobs` de threads inactivos > N días. Esto también mitigaría el `GraphRecursionError` recurrente que ya se maneja con un parche reactivo (`odontoking_graph.py:375-382`).

4. **`Thread` model y `thread` table sin uso** — confirmar y eliminar en una migración de limpieza si efectivamente es scaffold residual del template FastAPI+LangGraph.

5. **`tenant:phone_index` — código muerto**: `_phone_index_key()` está definido (`tenant.py:96-98`) pero nunca usado; `get_tenant_by_phone_id` (línea 191-193) itera sobre `_ENV_REGISTRY` en memoria en vez de usar un índice Redis. Si el plan era usar Redis para resolver `phone_number_id → tenant_slug` en el webhook (necesario si Meta solo manda `phone_number_id` y no el slug en la URL — lo cual SÍ pasa si Meta llama al webhook legado `/webhook` sin slug), esto está incompleto. Revisar si `get_tenant_by_phone_id` se usa en algún lado (no se encontró referencia activa fuera de su definición) — posible código muerto a remover, o funcionalidad incompleta a terminar.

6. **`UsageLog.cost_usd` tipo Decimal/float mismatch** (ver hallazgo MEDIO #9) — alinear el modelo SQLModel con la columna `Numeric(10,4)` real.

7. **DLQ de Redis sin trim** (ver tabla de namespacing) — agregar `LTRIM` o alerta de profundidad.

8. **Health check no cubre el broker ni la pool de checkpoints**: `/health` (`app/main.py:203-244`) chequea `database_service.health_check()` (sync engine) y `cache_service.health_check()`, pero NO verifica `odontoking_agent._connection_pool` (el pool realmente usado para el flujo de negocio) ni el estado del broker (`broker.health_check()` no existe como método de la interfaz `MessageBroker`). Si el pool de checkpoints falla tras el arranque, `/health` seguiría reportando "healthy" mientras el agente real está degradado.

---

## 🕳️ Cosas que estamos pasando por alto

- **`LangGraphAgent` (`app/core/langgraph/graph.py`) parece no formar parte del flujo de producción** (el flujo real usa `OdontokingAgent`), pero se sigue pre-calentando en el lifespan, consumiendo un pool de conexiones completo y memoria. Si es código legado del template original, debería eliminarse o aislarse detrás de un flag para no consumir recursos en producción.
- **No hay pruebas de integración que ejerciten `_db_get` / `get_tenant_async` bajo concurrencia** para detectar el bloqueo del event loop descrito en los hallazgos CRÍTICOS 1-2. Un test de carga simple (10 requests concurrentes a `/health` mientras se procesa un webhook) revelaría el problema rápidamente.
- **El esquema no tiene ninguna tabla para "pacientes" o "citas" propia** — toda esa información vive en el CRM externo (`crm.py`, `ODONTOKING_API_URL`). Esto es correcto arquitectónicamente (single source of truth externo), pero significa que **la base de datos de este proyecto es 100% "operacional/efímera"** (sesiones, checkpoints, logs de uso, config de tenant) — vale la pena documentar explícitamente en el README/AGENTS.md que un `DROP DATABASE` no pierde datos clínicos, solo historial conversacional y config — esto cambia drásticamente la criticidad de los backups.
- **Backups**: no se encontró ninguna configuración de `pg_dump`/backup automatizado en `docker-compose.yml` ni en `.github/workflows/`. Para producción (Railway/Easypanel según `docker-compose.easypanel.yml`), confirmar que la plataforma gestiona backups del volumen Postgres — si no, es un gap crítico de continuidad de negocio (se perdería el historial de conversaciones y la config de tenants encriptada).
- **`ENCRYPTION_KEY` rotación**: `app/services/encryption.py` usa Fernet con una sola clave desde `settings.ENCRYPTION_KEY`. No hay mecanismo de rotación de clave (`MultiFernet`) — si la clave se compromete o necesita rotarse, todos los `wa_access_token`/`crm_token`/`verify_token` encriptados en `tenants` quedarían ilegibles hasta re-encriptar manualmente. Para un solo tenant hoy es manejable, pero documentar el procedimiento de rotación antes de que existan más tenants.
- **`docker-compose.yml` define `db` (puerto 5432) Y `db-dev` (puerto 5433→5434, usuario `dev/dev`, DB `platform_db`)** — dos instancias de Postgres en el mismo compose. No quedó claro en el código revisado cuál usa la app por default en `development` (el `.env.development` apunta a `POSTGRES_HOST=localhost` con `POSTGRES_POOL_SIZE=5` — probablemente `db-dev`). Si ambos contenedores corren simultáneamente en una laptop de desarrollo, es doble consumo de RAM/CPU sin necesidad clara. Confirmar si `db-dev` sigue siendo necesario o es remanente de otro proyecto/plantilla.

---

## ✨ Nuevas funcionalidades propuestas

1. **Endpoint admin `GET /admin/db/health-detailed`**: exponer `pool.get_stats()` de los tres pools (sync QueuePool vía `engine.pool.status()`, y `AsyncConnectionPool.get_stats()` de psycopg_pool para `odontoking_agent` y `LangGraphAgent`) — daría visibilidad inmediata sobre saturación de conexiones antes de que se conviertan en errores `TooManyConnectionsError`.

2. **Job de "vacuum lógico" de checkpoints**: endpoint admin `POST /admin/maintenance/vacuum-checkpoints?older_than_days=90` que borre checkpoints de threads sin actividad reciente (cruzando con `chat_histories_odonto.created_at` o el propio timestamp del checkpoint), con dry-run (`?dry_run=true`) que solo cuenta filas afectadas.

3. **Migración `CREATE EXTENSION IF NOT EXISTS vector`** explícita en Alembic (idempotente) — previene fallos silenciosos cuando se active `ODONTOKING_MEMORY_ENABLED=true` en un entorno nuevo.

4. **Métrica Prometheus de profundidad de DLQ y de pools de conexión**: `app/core/metrics.py` ya tiene infraestructura — agregar gauges `db_pool_in_use`, `db_pool_idle`, `redis_dlq_depth{tenant=...}` actualizados periódicamente (cada 30s) vía un task de background, para alimentar el dashboard de Grafana ya existente (`grafana/dashboards/`).

5. **Índice compuesto + paginación cursor-based para `chat_histories_odonto`**: además del índice `(session_id, created_at)`, considerar `created_at` como cursor para `get_conversation` cuando el historial supere ~1000 mensajes (paginación, no `SELECT *` completo).

---

## 📋 Prioridades

| Hallazgo | Severidad | Esfuerzo | Impacto |
|---|---|---|---|
| `get_tenant_async`/`_db_get` bloquea el event loop en cada webhook (cache-miss) | CRÍTICO | Medio (envolver en `asyncio.to_thread` o migrar a engine async) | Alto — latencia/availability bajo carga |
| 13 endpoints admin/internal con sesión sync bloqueante en rutas async | CRÍTICO | Medio-Alto (engine async paralelo o `to_thread` masivo) | Alto — afecta `/internal/usage` (alta frecuencia) y panel admin |
| Triple pool de conexiones sin presupuesto coordinado (~75 conexiones/proceso) | CRÍTICO | Bajo (ajustar `max_size`/vars de env) | Alto — riesgo de agotar `max_connections` con 2+ réplicas |
| `LangGraphAgent` pre-calentado sin uso aparente, consume pool completo | ALTO | Bajo (confirmar uso, deshabilitar pre-warm o reducir `max_size`) | Medio — desperdicio de conexiones |
| Sin retención de `checkpoints`/`checkpoint_blobs`/`checkpoint_writes` | ALTO | Medio (cron + query de limpieza) | Alto — crecimiento ilimitado de la DB principal |
| Falta índice `(session_id, created_at)` en `chat_histories_odonto` | ALTO | Bajo (1 migración) | Medio — degradación gradual de `get_conversation` |
| `CREATE EXTENSION vector` no está en Alembic | ALTO | Bajo (1 migración idempotente) | Medio — fallo silencioso al activar memoria |
| FK `session.user_id → user.id` sin `ondelete`, `delete_user_by_email` no limpia sesiones | ALTO | Bajo (migración + ajuste de método) | Medio — `IntegrityError` no manejado al borrar usuarios con sesiones |
| `UsageLog.cost_usd` mismatch float/Numeric(10,4) + `round(...,6)` engañoso | MEDIO | Bajo (alinear tipo en modelo) | Medio — riesgo de migración autogenerada espuria + pérdida de precisión silenciosa |
| DLQ Redis (`wa:dlq:*`) sin TTL/trim | MEDIO | Bajo (LTRIM tras LPUSH) | Medio — crecimiento de memoria Redis ante fallos prolongados |
| `tenant:phone_index` / `get_tenant_by_phone_id` código muerto o incompleto | MEDIO | Bajo (eliminar o completar) | Bajo-Medio — claridad de código, posible gap funcional para webhook legado |
| 3 clientes Redis independientes (cache, buffer, broker) contra el mismo Valkey | BAJO | Medio (refactor a cliente compartido) | Bajo — simplicidad operativa |
| `ix_tenants_slug` posiblemente redundante con `UniqueConstraint("slug")` | BAJO | Bajo (verificar y `drop_index` si redundante) | Bajo |
| `Thread` model/tabla sin uso aparente | BAJO | Bajo (eliminar en migración) | Bajo — limpieza de esquema |
| `memory_service` no se cierra en lifespan shutdown | BAJO | Bajo (agregar `close()`) | Bajo — solo relevante en hot-reload/dev |
| Caché de memoria semántica (`memory:*`) probablemente con hit-rate bajo | BAJO | Medio (medir + rediseñar key) | Bajo-Medio — oportunidad de optimización de costo LLM |

---

*Fin del reporte. Todas las referencias de archivo:línea fueron verificadas contra el código real en `/Users/javier/proyectos/02.agentes/01.odontoking` el 2026-06-10.*
