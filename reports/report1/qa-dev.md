# QA-Dev — Reporte de Calidad y Cobertura

## Resultado del test suite (X passing / Y failing, comando usado, errores)

**Comando usado:** `cd /Users/javier/proyectos/02.agentes/01.odontoking && uv run pytest -q`

**`uv` no estaba instalado** en el entorno (`command not found: uv`). Se instaló vía `pip3 install --user uv` para poder ejecutar la suite. Esto en sí mismo es una señal: nadie ha podido correr `pytest` localmente con el flujo documentado sin pasos manuales adicionales.

### Hallazgo crítico de entorno: `pytest-asyncio` no estaba instalado

`pyproject.toml` declara `pytest-asyncio>=0.23.0` en el grupo de dependencias `test` y configura `asyncio_mode = "auto"` (líneas ~`[tool.pytest.ini_options]`). Sin embargo, el `.venv` existente **no tenía `pytest-asyncio` instalado**.

- **Antes de `uv sync --all-extras --all-groups`:**
  `uv run pytest -q` → `2 failed, 147 passed, 115 skipped, 236 warnings in 52.28s`
  De los 264 tests recolectados, **115 (43%)** son funciones `async def` decoradas con `@pytest.mark.asyncio` que **se saltan silenciosamente** con `PytestUnhandledCoroutineWarning: async def functions are not natively supported and have been skipped`. Esto incluye **el 100% de `tests/unit/test_memory_service.py`** (11/11 tests del servicio de memoria/caché — una de las prioridades explícitas de esta revisión).

- **Después de `uv sync --all-extras --all-groups`** (instala `pytest-asyncio==1.3.0`, `redis`, etc.):
  `uv run pytest -q` → **`4 failed, 260 passed, 8 warnings in 45.45s`** (264 tests recolectados, 0 skipped)

Es decir: el baseline "146p/112s/0f" documentado en la memoria de qa-dev (`.claude/agent-memory/qa-dev/`) corresponde a un entorno donde **115 tests nunca se ejecutaron de verdad**. Con el entorno correctamente sincronizado, aparecen **2 fallas nuevas** además de las **2 ya conocidas**, todas reales (ver sección de bugs).

### Las 4 fallas reales (con `pytest-asyncio` instalado):

1. `tests/unit/tools/test_get_doctor_schedule.py::TestEdgeCases::test_empty_slots_list_returned_gracefully`
   `AssertionError: assert 'Sin disponibilidad' == []`
2. `tests/unit/tools/test_patient_flow.py::TestVerifyInsuranceUnifiedEndpoint::test_alianza_vencido_returns_has_insurance_blocked`
   `AssertionError: assert 'VIGENTE' != 'VIGENTE'`
3. `tests/unit/tools/test_odontoking_tools.py::TestGetDoctors::test_returns_filtered_doctor_fields`
   `IndexError: list index out of range`
4. `tests/unit/tools/test_odontoking_tools.py::TestGetDoctors::test_doctor_with_no_specialties`
   `IndexError: list index out of range`

Las 4 son **bugs reales de producción o desincronización test↔implementación**, no errores de test mal escrito (ver sección "Bugs/regresiones").

`uv run pytest --co -q` → **264 tests collected** (sin errores de colección).

### CI no ejecuta tests

`.github/workflows/ci.yaml` solo corre:
```yaml
- run: uv sync --all-extras --all-groups
- run: uv run ruff check .
- run: uv run ruff format --check .
- run: uv run pyright
```
**No hay paso `uv run pytest`**. Esto explica por qué 115 tests llevan tiempo (probablemente desde que se introdujo `asyncio_mode = "auto"`) sin ejecutarse nunca y por qué 4 bugs reales en tools de producción (insurance, doctor schedule, get_doctors) no fueron detectados.

---

## Resumen ejecutivo (3-5 bullets)

- **CI no corre `pytest`** (`.github/workflows/ci.yaml:9-19`) — solo lint/format/typecheck. Combinado con `pytest-asyncio` ausente del `.venv`, **el 43% de la suite (115/264 tests) nunca se ejecutó**, incluyendo toda la cobertura de `MemoryService` (cache).
- Con el entorno corregido aparecen **4 fallas reales**, dos de ellas son **bugs de negocio activos en producción**: (1) `verify_insurance` siempre devuelve `status: "VIGENTE"` cuando `has_insurance=True`, **incluso si el seguro está VENCIDO** (`app/core/langgraph/tools/insurance.py:54`) — riesgo directo de billing/cobertura; (2) `get_doctor_schedule` devuelve un **string** `"Sin disponibilidad"` en el campo `schedule` en vez de `[]` cuando no hay cupos, rompiendo el contrato `schedule: list` (`app/core/langgraph/tools/odontoking.py:226`).
- **Flujos críticos sin ningún test**: el loop principal de LangGraph (`app/core/langgraph/graph.py` — nodos `_chat`/`_tool_call`, `Command(resume=...)`, checkpointing con `AsyncPostgresSaver`), `app/core/langgraph/odontoking_graph.py` (mismo loop para el agente Odontoking), `LLMService` (retry/fallback circular, `app/services/llm/service.py`), JWT/auth (`app/api/v1/auth.py`), `DatabaseService` (`app/services/database.py`, todas las operaciones SQLModel), y **todo `app/api/admin/*`** (billing, tenants, users, conversations, stats).
- **Anti-patrón de DB mockeada** confirmado en `tests/api/test_internal.py` (endpoint de billing/usage): `Session` de SQLModel se reemplaza por `MagicMock` con `session_mock.exec.return_value.first.side_effect = [...]` — las queries SQL reales (`SELECT ... WHERE tenant_id=...`) nunca se ejecutan, por lo que un `WHERE` roto o columna mal nombrada no se detectaría.
- `evals/` (framework Langfuse de evaluación de calidad LLM) **no está integrado en CI** — no se ejecuta automáticamente en ningún punto.

---

## 📊 Inventario de tests existentes (qué cubren)

| Archivo | Qué cubre | # tests aprox. |
|---|---|---|
| `tests/unit/test_broker.py` | `InMemoryBroker`, `RedisStreamBroker`, `RabbitMQBroker`: publish, DLQ list/retry, ack/retry counters, fallback a in-memory cuando no hay Valkey | 25 |
| `tests/unit/test_memory_service.py` | `MemoryService._get_memory` (sync/async `from_config`), caching de resultados de búsqueda mem0 | 11 (100% `@pytest.mark.asyncio`, antes saltados) |
| `tests/unit/test_message_buffer.py` | `_InMemoryMessageBuffer` (push/drain/cap/workers), `MessageBufferService` (enqueue, batching por ventana, retry de `process_fn`), `RedisMessageBuffer` (rpush/drain/locks) | 27 |
| `tests/unit/test_tenant_webhook.py` | Verify/receive webhook multi-tenant, registro de tenant desconocido (200 silencioso), `_AGENT_REGISTRY` por `agent_type`, fallback a `odontoking_agent` | ~13 |
| `tests/unit/test_webhook.py` | Webhook legacy: verify token, mensajes de texto/audio/imagen, dedup por `msg.id` con TTL, payload malformado → 200 | ~13 |
| `tests/unit/test_whatsapp.py` | `_AGENT_REGISTRY` resuelve por `agent_type`, fallback a `odontoking_agent` para tipos desconocidos | 2 |
| `tests/unit/test_whatsapp_client.py` | `build_interactive_payload` (botones/listas, truncado de texto), `send_text_message` (éxito y error HTTP), `transcribe_audio` (éxito, vacío, extensión por mime) | ~14 |
| `tests/unit/tools/test_crm_tool.py` | `update_crm`, `_parse_appointment_datetime`, `_person_payload`, `get_citas`, `ensure_person_registered` (parcial) | ~19 |
| `tests/unit/tools/test_get_doctor_schedule.py` | `get_doctor_schedule`: contrato `/api/doctors/{id}/slots`, días, duración, edge cases | ~20 |
| `tests/unit/tools/test_get_services.py` | `get_services` (filtro por keyword, campos) | ~20 |
| `tests/unit/tools/test_insurance_tool.py` | `verify_insurance` — casos generales | 8 |
| `tests/unit/tools/test_odontoking_tools.py` | `get_services`, `get_specialties`, `get_doctors`, `get_horarios`, `get_disponibilidad` | ~19 |
| `tests/unit/tools/test_patient_flow.py` | Flujo paciente end-to-end de tools: `ensure_person_registered`, `verify_insurance` (Alianza/Nacional Vida/Membresía), `get_doctors`, `update_crm`, `get_doctor_schedule`, `_db_get`/`get_tenant*`, `list_tenants` | ~25 |
| `tests/api/test_internal.py` | `POST /api/v1/internal/usage`: auth por `X-Internal-Key`, tenant no encontrado, creación/incremento de `UsageLog` | 5 |
| `tests/api/test_sprint4.py` | Routing del webhook por `tenant.agent_endpoint_url` (broker RabbitMQ vs buffer interno), contrato del payload publicado, swallow de errores del broker | 5 |
| `tests/conftest.py` | Fixtures globales: env vars de test, payloads de WhatsApp (texto/audio/status), reset de `_seen_message_ids` | — |

**Total: 264 tests recolectados, 260 pasan / 4 fallan con el entorno correctamente sincronizado.**

---

## 🕳️ Gaps de cobertura críticos (severidad, módulo sin test, riesgo)

### Severidad ALTA

1. **`app/core/langgraph/graph.py`** (484 líneas) — `LangGraphAgent`: nodos `_chat` (línea 130) y `_tool_call` (línea 187), routing `chat → tool_call → chat → END`, `get_response`/`get_stream_response`, manejo de `AsyncPostgresSaver`/`AsyncConnectionPool`, `get_chat_history`, `clear_chat_history`. **CERO tests directos.** Es el corazón del flujo "FastAPI route → LangGraphAgent → StateGraph" descrito en `CLAUDE.md`. Riesgo: un cambio en el routing chat/tool_call o en el manejo de `Command` rompe toda conversación sin que ningún test lo detecte.

2. **`app/core/langgraph/odontoking_graph.py`** (411 líneas) — `OdontokingAgent`: mismo patrón (`_chat` línea 194, `_tool_call` línea 226, `get_response` línea 280 con `Command(resume=messages[-1].content)` en línea 329 para el flujo `ask_human`/`NodeInterrupt`). **CERO tests.** El flujo de interrupción humana (`ask_human` + resume) es exactamente el tipo de lógica con estado que se rompe silenciosamente.

3. **`app/services/llm/service.py`** (345 líneas) — `LLMService`: `_invoke_with_retry` (retry con tenacity sobre `RateLimitError`/`APITimeoutError`/`APIError`, línea 171-178), `_switch_to_next_model` (fallback circular, línea 211), `_call_with_fallback`/`_fallback_loop` (líneas 239-319), `LLM_TOTAL_TIMEOUT`. **CERO tests.** Es exactamente "LLM service retry/fallback" pedido como prioridad — sin tests que verifiquen que el fallback circular realmente cambia de modelo tras agotar reintentos, o que el timeout global corta el loop.

4. **`app/api/v1/auth.py`** (367 líneas) — JWT register/login/sesiones: `get_current_user` (línea 52), `get_current_session` (línea 103), `register_user` (156), `login` (199-245), `create_session`/`update_session_name`/`delete_session`/`get_user_sessions`. **CERO tests.** Flujo de autenticación completo (happy + error paths: credenciales inválidas, token expirado, usuario duplicado, sesión ajena) sin cobertura.

5. **`app/services/database.py`** (263 líneas) — `DatabaseService`: `create_user`, `get_user_by_email`, `delete_user_by_email`, `create_session`, `delete_session`, `get_session`, `get_user_sessions`, `update_session_name`, `health_check`. **CERO tests con DB real o in-memory SQLite.** Toda la capa de persistencia de usuarios/sesiones (motor SQLModel síncrono) está sin probar.

6. **`app/api/admin/*`** (billing.py, conversations.py, stats.py, tenants.py, users.py — ~782 líneas combinadas). **CERO tests** en todo el namespace `/admin`. Riesgo: endpoints de billing y gestión de tenants (multi-tenant es central a esta arquitectura) sin red de seguridad.

### Severidad MEDIA

7. **`app/core/langgraph/tools/ask_human.py`** — tool de interrupción humana (`interrupt(question)`). **CERO tests.** Aunque es corto (26 líneas), es el mecanismo de pausa/resume del grafo; un test de regresión evitaría que un cambio en `langgraph.types.interrupt` rompa el flujo silenciosamente.

8. **`app/core/langgraph/tools/crm.py`** — funciones sin test: `find_person_by_wa_id` (línea 63), `_update_person_attributes` (línea 102), `_pick_agent_user` (línea 37), `_person_email_from_wa_id` (línea 59 — parcialmente cubierta), `sync_transcript_to_crm` + `_fetch_transcript` (líneas 554-589, sincronización de transcripción al CRM). El "lookup de persona por wa_id" es la base de `ensure_person_registered` pero no se prueba de forma aislada (solo indirectamente).

9. **`app/services/whatsapp_client.py`** — funciones sin test: `send_interactive_message` (línea 56), `mark_as_read` (línea 131), `send_typing_indicator` (línea 158), `send_response` (línea 187), `download_media` (línea 205, error paths de descarga de media de Meta).

10. **`app/services/session_naming.py`** (91 líneas) — `SessionNamingService`, usa `session_title.md` (mencionado en CLAUDE.md). **CERO tests.**

### Severidad BAJA

11. **`evals/`** — framework de evaluación LLM (Langfuse) no corre en CI (`railway.evals.toml` sugiere que corre como job aparte en Railway, pero no hay verificación automatizada en PRs).

---

## 🔍 Calidad de los tests existentes (anti-patrones, DB mockeada, happy-path only) con archivo:línea

### 1. DB mockeada con `MagicMock` en flujo de billing — anti-patrón crítico

`tests/api/test_internal.py:65-71` y `tests/api/test_internal.py:114-119` y `tests/api/test_internal.py:142-148`:

```python
session_mock = MagicMock()
session_mock.__enter__ = MagicMock(return_value=session_mock)
session_mock.__exit__ = MagicMock(return_value=False)
session_mock.exec.return_value.first.side_effect = [tenant, usage_log]

with patch("app.api.v1.internal.Session", return_value=session_mock):
    response = app_client.post(_ENDPOINT, json={...}, headers={...})
```

`Session` de SQLModel se sustituye completamente por un `MagicMock`. La query real `select(Tenant).where(Tenant.slug == tenant_slug)` (o similar en `app/api/v1/internal.py`) **nunca se construye ni se valida contra un esquema**. Si alguien renombra una columna del modelo `UsageLog`/`Tenant`, o cambia el `WHERE`, estos tests siguen pasando porque `session_mock.exec(...)` devuelve lo que el test programó manualmente, sin importar el SQL real. Para un endpoint de **billing** (la regla del proyecto exige happy+error paths reales para billing), esto es justo el anti-patrón que la consigna prohíbe ("never mock the database to make tests easier").

**Recomendación:** usar SQLite en memoria (`create_engine("sqlite://")`) o un Postgres de test (testcontainers) con las tablas reales creadas vía `SQLModel.metadata.create_all`, e insertar filas reales de `Tenant`/`UsageLog`.

### 2. `database_service` mockeado a nivel de módulo en TODOS los tests de webhook

`tests/unit/test_webhook.py:17`, `tests/unit/test_tenant_webhook.py:15`, `tests/unit/test_whatsapp.py:14`, `tests/api/test_internal.py:24`, `tests/api/test_sprint4.py:50`:

```python
patch("app.services.database.database_service"),
```

Esto reemplaza el servicio de base de datos completo con un `MagicMock` automático para **todos** los tests de webhook/routing. Es razonable para tests de "routing" que no deben tocar la BD, **pero significa que el flujo completo "webhook recibe mensaje → persiste en BD → responde" nunca se prueba con persistencia real**. No hay ningún test de integración que verifique que `ensure_person_registered`/`save_chat_message`/`create_session` realmente escriben filas válidas.

### 3. `tests/unit/test_memory_service.py` — 100% del archivo no se ejecutaba (ahora corre, pero sigue siendo solo mocks)

`tests/unit/test_memory_service.py:1-179` — los 11 tests usan `@pytest.mark.asyncio` (líneas 10, 28, 44, etc.) y mockean `AsyncMemory` por completo (`patch("app.services.memory.AsyncMemory")`, línea 20). **Nunca se ejercita el cache real** (Valkey/Redis o in-memory TTL descrito en `app/core/cache.py`) — solo se verifica que `_get_memory()` cachea la instancia de `AsyncMemory`, no que `MemoryService.search()` consulta primero el cache antes de pgvector y solo cachea respuestas exitosas (regla explícita del proyecto: "Only cache successful responses, never cache errors"). **No hay ningún test que verifique la regla de "no cachear errores"** del `MemoryService`.

### 4. Tests "happy path only" en flujos con manejo de errores documentado

- `app/services/llm/service.py` documenta explícitamente retry + fallback circular + timeout global (`LLM_TOTAL_TIMEOUT`), pero **no existe `tests/unit/test_llm_service.py`** — cero tests de error path (rate limit, timeout, fallback a segundo modelo, agotamiento del budget de 60s).
- `app/core/langgraph/tools/insurance.py` — los tests cubren "Alianza vigente" (`tests/unit/tools/test_patient_flow.py:304-323`) y un intento de "Alianza vencido" (líneas 333-345) que **falla** porque la implementación tiene un bug (ver más abajo). No hay test de error 5xx del API de seguros con `retry`/`reraise` (sí existe `_is_transient` en `insurance.py:18-21` pero sin test que dispare un 500 y verifique 3 reintentos).
- `app/core/langgraph/tools/odontoking.py::get_doctor_schedule` — sí tiene buena cobertura de error paths (404, parámetros inválidos, ver `tests/unit/tools/test_get_doctor_schedule.py`), pero el caso "0 slots disponibles" (`TestEdgeCases::test_empty_slots_list_returned_gracefully`) **falla** por el bug de tipo descrito abajo.

### 5. Test brittle / acoplado a orden de ejecución implícito

`tests/unit/test_webhook.py:257-274` (`test_dedup_cache_expires_after_ttl`) manipula directamente el diccionario interno `wa_module._seen_message_ids` para simular expiración de TTL:

```python
for k in wa_module._seen_message_ids:
    wa_module._seen_message_ids[k] = 0.0
```

Esto es frágil porque depende de un detalle de implementación interno (`_seen_message_ids` como dict con timestamps mutables). Si el dedup cache se mueve a Redis/Valkey (como ya ocurre con el message buffer), este test deja de tener sentido y probablemente falle de forma confusa en vez de fallar claramente. El `conftest.py:36-45` (`reset_wa_seen_ids`) ya depende de la misma estructura interna — doble acoplamiento.

---

## 🐛 Bugs/regresiones descubiertos al revisar/ejecutar

### BUG 1 (ALTO — billing/seguro): `verify_insurance` siempre marca el seguro como "VIGENTE" si `has_insurance=True`, ignorando el `status` real

**Archivo:** `app/core/langgraph/tools/insurance.py:51-56`

```python
result = await _call_verify(client, ci_paciente, seguro_paciente)
if result.get("has_insurance"):
    result["status"] = "VIGENTE"
else:
    result.setdefault("status", "NO_VIGENTE")
```

El test `tests/unit/tools/test_patient_flow.py::TestVerifyInsuranceUnifiedEndpoint::test_alianza_vencido_returns_has_insurance_blocked` (línea 333-345) simula que el API externo devuelve `{"has_insurance": True, "status": "VENCIDO", ...}` (fixture `_INSURANCE_VENCIDA`, línea 80-86). La implementación **sobrescribe** `status` a `"VIGENTE"` con la línea 54 porque `has_insurance` es `True`, sin mirar el valor original de `status`. Resultado: `result["status"] == "VIGENTE"` aunque el API dijo `"VENCIDO"`.

**Impacto de negocio:** el agente de WhatsApp le dirá al paciente (o al flujo de agendamiento) que su seguro está vigente cuando en realidad está vencido — esto puede llevar a agendar citas con cobertura que el seguro no va a pagar, o a no cobrar el copago correcto. Es un bug de **billing/insurance**, exactamente la categoría que la consigna marca como crítica.

**Fix sugerido:** no sobrescribir `status`; usar el valor que ya viene del API (`result.setdefault("status", ...)` en ambas ramas, o simplemente no tocar `status` si ya existe en `result`).

### BUG 2 (MEDIO — contrato de tipos): `get_doctor_schedule` devuelve un `str` en `schedule` cuando no hay disponibilidad, rompiendo el contrato `schedule: list`

**Archivo:** `app/core/langgraph/tools/odontoking.py:225-228`

```python
return json.dumps(
    {"doctor_id": id_doctor, "schedule": schedule if total_slots > 0 else "Sin disponibilidad", "days_queried": days},
    ensure_ascii=False,
)
```

El test `tests/unit/tools/test_get_doctor_schedule.py::TestEdgeCases::test_empty_slots_list_returned_gracefully` espera `result["schedule"] == []` cuando el API devuelve días con `slots=[]`. La implementación, en cambio, devuelve la **string literal** `"Sin disponibilidad"` en el campo `schedule` cuando `total_slots == 0`. Esto contradice el propio test `test_response_structure_matches_contract` (línea 191-197) que asserta `isinstance(result["schedule"], list)` — ese test pasa solo porque usa fixtures con slots no vacíos.

**Impacto:** cualquier código (o el LLM al razonar sobre la respuesta JSON) que asuma `schedule` es siempre una lista (`len(result["schedule"])`, iteración, etc.) puede fallar o comportarse de forma inesperada cuando no hay cupos — justo el caso más común para doctores con agenda llena.

**Fix sugerido:** mantener `"schedule": schedule` (lista vacía) siempre, y comunicar "sin disponibilidad" mediante un campo adicional opcional, p.ej. `"message": "Sin disponibilidad"` o dejar que el LLM interprete `schedule == []`.

### BUG 3 / DESINCRONIZACIÓN (MEDIO): `get_doctors` filtra por `has_availability` y los tests no lo contemplan → `IndexError`

**Archivo:** `app/core/langgraph/tools/odontoking.py:134-149`

```python
filtered = [
    {
        "id": d["id"],
        "name": d["name"],
        "status": d.get("is_active"),
        "has_availability": d.get("has_availability"),
        ...
    }
    for d in data
    if d.get("is_active") and d.get("has_availability")
]
```

Tests afectados:
- `tests/unit/tools/test_odontoking_tools.py::TestGetDoctors::test_returns_filtered_doctor_fields` (líneas 122-148)
- `tests/unit/tools/test_odontoking_tools.py::TestGetDoctors::test_doctor_with_no_specialties` (líneas 150-166)

Ambos fixtures de respuesta del API (`raw["data"][0]`) **no incluyen la clave `has_availability`**, por lo que `d.get("has_availability")` devuelve `None` (falsy) y el filtro de la línea 148 **descarta al doctor**, dejando `data: []`. Los tests luego hacen `result["data"][0]` → `IndexError: list index out of range`.

Dos lecturas posibles:
1. **Si el filtro `has_availability` es un requisito de negocio nuevo** (introducido después de escribir estos tests): los tests están desactualizados y deben actualizarse para incluir `"has_availability": True` en sus fixtures — **regresión de test, no de producción**.
2. **Si el filtro es demasiado estricto** (un doctor activo pero sin el campo `has_availability` poblado por el API upstream debería seguir apareciendo, quizá con `has_availability: false` informativo en vez de ser excluido): es un **bug de producción** — doctores activos podrían desaparecer silenciosamente de `get_doctors()` para el agente, impidiendo agendar citas con ellos.

En cualquier caso, **es una desincronización real entre test e implementación que debe resolverse explícitamente** (no ignorarse) — actualmente oculta si el comportamiento de `get_doctors` es el deseado.

### Bug de configuración de entorno: `pytest-asyncio` ausente del lockfile/`.venv` instalado

No es un bug de código de aplicación, pero es una **regresión de calidad de proceso**: 115/264 tests (43%) llevaban tiempo sin ejecutarse. Dado que **los 3 bugs anteriores fueron descubiertos precisamente al instalar `pytest-asyncio` y ejecutar esos 115 tests**, es razonable asumir que pueden existir más bugs latentes en código no cubierto por los 149 tests que sí corrían antes.

---

## ✨ Estrategia de testing propuesta (qué tests escribir primero)

### Fase 0 — Arreglar el entorno y CI (bloqueante, esfuerzo bajo)
1. Verificar que `uv.lock` incluye `pytest-asyncio` y que `uv sync --all-extras --all-groups` (el comando que usa CI) lo instala correctamente — confirmar que no es un problema de `uv.lock` desactualizado.
2. **Agregar `uv run pytest -q` como step en `.github/workflows/ci.yaml`**, después de `uv sync`. Esto es la mejora de mayor impacto/menor esfuerzo de todo el reporte: hubiera detectado los 3 bugs de negocio inmediatamente.
3. Decidir y arreglar los 3 bugs/desincronizaciones encontrados (insurance status, schedule type, get_doctors has_availability) — son bloqueantes para que `pytest` quede en verde.

### Fase 1 — Flujos críticos sin cobertura (esfuerzo medio-alto, máximo impacto)

1. **`tests/unit/test_llm_service.py`** (nuevo) — `LLMService`:
   - Happy path: `call()` devuelve respuesta del primer modelo.
   - Retry: `RateLimitError`/`APITimeoutError` en el primer intento → reintenta con backoff (mockear `tenacity` con tiempos cortos o usar `wait_fixed(0)` vía monkeypatch).
   - Fallback circular: modelo 1 agota reintentos → `_switch_to_next_model` cambia al modelo 2 → éxito.
   - Timeout global: simular que `LLM_TOTAL_TIMEOUT` se agota antes de que cualquier modelo responda → error controlado, no excepción cruda.
   - Caso de fallo total: todos los modelos del registry fallan → excepción/])error propagado correctamente.

2. **`tests/unit/test_graph.py`** (nuevo) — `LangGraphAgent`:
   - `_chat`: dado un `GraphState` con mensajes, mockear `LLMService.call` para devolver (a) respuesta sin tool calls → `Command(goto=END)`; (b) respuesta con tool_calls → `Command(goto="tool_call")`.
   - `_tool_call`: ejecuta tools mockeadas y vuelve a `chat`.
   - `get_response`: integración mockeando el grafo compilado, verificar que persiste en checkpointer.
   - Manejo de error cuando `_get_connection_pool` falla (DB de checkpointing caída) — debe degradar con gracia, no crashear el webhook.

3. **`tests/unit/test_odontoking_graph.py`** (nuevo) — `OdontokingAgent`, especial atención a:
   - Flujo `ask_human` → `NodeInterrupt` → `state.next` seteado → siguiente request resuelve con `Command(resume=messages[-1].content)` (línea 329 de `odontoking_graph.py`). Este es uno de los flujos con estado más delicados del proyecto y tiene cero tests.

4. **`tests/unit/test_auth.py`** (nuevo) — `app/api/v1/auth.py`, usando `TestClient` + DB real (SQLite en memoria, NO mock de `Session`):
   - `register_user`: éxito, email duplicado (409/400), validación de password.
   - `login`: credenciales correctas → JWT válido; password incorrecto → 401; usuario inexistente → 401.
   - `get_current_user`/`get_current_session`: token expirado/inválido → 401; token válido → usuario correcto.
   - `create_session`/`delete_session`/`update_session_name`/`get_user_sessions`: happy path + intento de borrar/renombrar sesión de **otro usuario** (autorización).

5. **`tests/unit/test_database_service.py`** (nuevo) — `DatabaseService` contra **SQLite en memoria con `SQLModel.metadata.create_all`** (no mocks):
   - `create_user` + `get_user_by_email` (incluye colisión de email único).
   - `create_session`/`get_session`/`delete_session`/`get_user_sessions`/`update_session_name`.
   - `health_check` con DB caída (engine apuntando a host inválido) → debe devolver `False`, no excepción.

### Fase 2 — Cerrar gaps medios (esfuerzo medio)

6. **`tests/api/test_admin_*.py`** — al menos un happy path + un error path por router de `app/api/admin/`: `tenants.py` (CRUD tenant), `billing.py` (consultas de uso/costo), `users.py`, `conversations.py`, `stats.py`. Usar DB real (SQLite/Postgres de test), no `MagicMock` de `Session`.

7. **`tests/unit/tools/test_ask_human.py`** (nuevo) — verificar que `ask_human.ainvoke`/`invoke` lanza `NodeInterrupt`/`interrupt(question)` con el `question` correcto (regression test simple, bajo esfuerzo).

8. **`tests/unit/tools/test_crm_tool.py`** — agregar: `find_person_by_wa_id` (encontrado/no encontrado/error HTTP), `_update_person_attributes`, `sync_transcript_to_crm` + `_fetch_transcript` (happy + transcript vacío + error de red al CRM).

9. **`tests/unit/test_whatsapp_client.py`** — agregar `mark_as_read`, `send_typing_indicator`, `send_interactive_message`, `download_media` (incluyendo error 4xx/5xx de Meta).

10. **Reescribir `tests/api/test_internal.py`** para usar SQLite en memoria con tablas `Tenant`/`UsageLog` reales en vez de `MagicMock(Session)` — mantener los mismos casos (key inválida, tenant no encontrado, incremento de fila existente) pero contra SQL real.

### Fase 3 — Refuerzo de MemoryService cache (esfuerzo bajo, ahora que los tests corren)

11. Agregar a `tests/unit/test_memory_service.py`:
    - Test que verifica que `search()` consulta el cache **antes** de llamar a `AsyncMemory.search` (cache hit → no llamada a pgvector).
    - Test que verifica que **un error de mem0/pgvector NO se cachea** (regla explícita del proyecto "Only cache successful responses").
    - Test de TTL del cache in-memory (vía `app/core/cache.py`).

---

## 📋 Prioridades (tabla: gap/hallazgo | severidad | esfuerzo | impacto)

| Gap / Hallazgo | Severidad | Esfuerzo | Impacto |
|---|---|---|---|
| CI no ejecuta `pytest` (`.github/workflows/ci.yaml`) | ALTA | Bajo | Altísimo — hubiera detectado los 3 bugs de negocio antes de merge |
| BUG 1: `verify_insurance` siempre devuelve `status="VIGENTE"` (`insurance.py:54`) | ALTA | Bajo | Alto — riesgo de billing/cobertura de seguro mal informada al paciente |
| BUG 2: `get_doctor_schedule` devuelve `str` en `schedule` cuando vacío (`odontoking.py:226`) | MEDIA | Bajo | Medio — rompe contrato de tipos, puede confundir al LLM/consumidores |
| BUG 3: filtro `has_availability` en `get_doctors` causa `IndexError` en tests (`odontoking.py:148`) | MEDIA | Bajo-Medio | Medio — posible exclusión silenciosa de doctores activos del agente |
| `pytest-asyncio` ausente del `.venv` / 115 tests nunca corridos | ALTA | Bajo | Altísimo — invalida el "baseline" de calidad previo |
| `LLMService` retry/fallback sin tests (`llm/service.py`) | ALTA | Medio-Alto | Alto — núcleo de resiliencia ante fallas de OpenAI/proveedor |
| `LangGraphAgent`/`OdontokingAgent` `_chat`/`_tool_call`/resume sin tests (`graph.py`, `odontoking_graph.py`) | ALTA | Alto | Altísimo — corazón del agente conversacional |
| `app/api/v1/auth.py` (JWT/login/sesiones) sin tests | ALTA | Medio | Alto — seguridad y autenticación |
| `app/services/database.py` sin tests (DB real) | ALTA | Medio | Alto — capa de persistencia base |
| `tests/api/test_internal.py` mockea `Session` (anti-patrón DB) | MEDIA | Medio | Alto — billing sin red de seguridad real |
| `app/api/admin/*` sin tests (billing, tenants, users, conversations, stats) | MEDIA | Alto | Alto — multi-tenant es central a la arquitectura |
| `MemoryService` cache: falta test "no cachear errores" | MEDIA | Bajo | Medio — viola regla explícita del proyecto si no se valida |
| `crm.py`: `find_person_by_wa_id`, `sync_transcript_to_crm` sin tests | MEDIA | Medio | Medio — base de registro de pacientes en CRM |
| `whatsapp_client.py`: `mark_as_read`, `download_media`, etc. sin tests | BAJA | Bajo | Bajo-Medio |
| `ask_human.py` sin test de regresión | BAJA | Bajo | Bajo (pero barato de agregar) |
| `evals/` no integrado en CI | BAJA | Medio | Medio — calidad de respuestas LLM no monitoreada en PRs |
| Test brittle: `_seen_message_ids` manipulado directamente (`test_webhook.py:270-271`) | BAJA | Bajo | Bajo — riesgo de romperse si dedup migra a Redis |
