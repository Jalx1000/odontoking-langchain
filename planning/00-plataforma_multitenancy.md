# Plataforma Multi-Tenant de Agentes IA — Plan de Arquitectura

**Fecha:** 2026-05-13  
**Autor:** Agencia de Desarrollo  
**Estado:** En planificación  
**Clientes actuales:** Odontoking (local), Kohlberg (nacional)

---

## Contexto y Problema

La agencia crea agentes de WhatsApp para distintas empresas. Hoy el repositorio está
construido como un producto mono-tenant (solo Odontoking). Cada cliente nuevo requeriría
forkear el repo, duplicar infraestructura y mantener múltiples bases de código.

**Objetivo:** Convertir este repositorio en una plataforma multi-tenant donde agregar
un cliente nuevo sea una cuestión de configuración, no de código.

---

## Estado Actual del Proyecto (Baseline)

### Lo que ya está funcionando
- `OdontokingAgent` — LangGraph + GPT-4o-mini + tools propios (CRM, seguros, doctores)
- `LangGraphAgent` — agente genérico para la API REST
- Webhook WhatsApp en `/api/v1/whatsapp/webhook` (mono-tenant)
- `MessageBufferService` — debounce por `wa_id` con Redis o in-memory
- PostgreSQL + pgvector para checkpointing y memoria semántica
- Langfuse para trazabilidad de LLM
- Prometheus + Grafana para métricas
- 84 tests unitarios pasando
- Sistema de alertas por email (SMTP SSL)
- Health check completo (DB + Redis + buffer)
- Startup recovery para mensajes huérfanos en Redis

### Deuda técnica ya identificada
- Webhook hardcodeado para Odontoking — no soporta múltiples clientes
- Sin tenant registry — no hay forma de mapear `phone_number_id → cliente`
- `whatsapp_client.py` usa `settings.WHATSAPP_ACCESS_TOKEN` global — no per-tenant
- Workers in-process (asyncio) — sin garantía at-least-once si el proceso cae
- Sin worker process separado — el webhook y el LLM corren en el mismo proceso

---

## Arquitectura Objetivo

```
                    ┌────────────────────────────────────────────────┐
                    │              PLATAFORMA AGENCIA                 │
                    │                                                  │
  Meta App Odonto ─▶│  ┌──────────────────────────────────────────┐  │
  Meta App Kohlbg ─▶│  │     Webhook Router (FastAPI)             │  │
  Meta App N     ─▶│  │                                          │  │
                    │  │  GET /{tenant_slug}/webhook  ← verify    │  │
                    │  │  POST /{tenant_slug}/webhook ← messages  │  │
                    │  │                                          │  │
                    │  │  Lookup: phone_number_id → TenantConfig  │  │
                    │  │  → publica en cola del tenant            │  │
                    │  └──────────────────┬───────────────────────┘  │
                    │                     │ 200 OK inmediato          │
                    │                     ▼                           │
                    │  ┌──────────────────────────────────────────┐  │
                    │  │         Message Broker                   │  │
                    │  │  (Redis Streams — Phase 2: RabbitMQ)     │  │
                    │  │                                          │  │
                    │  │  stream:odontoking  ←─── mensajes        │  │
                    │  │  stream:kohlberg    ←─── mensajes        │  │
                    │  │  stream:cliente_n   ←─── mensajes        │  │
                    │  │                                          │  │
                    │  │  dlq:odontoking  (fallos tras 3 retries) │  │
                    │  │  dlq:kohlberg                            │  │
                    │  └──────────────────┬───────────────────────┘  │
                    │                     │                           │
                    │       ┌─────────────┼──────────────┐           │
                    │       ▼             ▼              ▼           │
                    │  ┌─────────┐  ┌─────────┐  ┌─────────┐       │
                    │  │ Worker  │  │ Worker  │  │ Worker  │       │
                    │  │ Odonto  │  │ Kohlbg  │  │ Clte N  │       │
                    │  │ x1 inst │  │ x3 inst │  │ x1 inst │       │
                    │  └────┬────┘  └────┬────┘  └────┬────┘       │
                    │       │            │             │            │
                    │       ▼            ▼             ▼            │
                    │  OdontokingAgent  KohlbergAgent  AgentN       │
                    │  GPT-4o-mini      GPT-4o         ...          │
                    │  CRM Sofopolis    CRM Kohlberg                │
                    └────────────────────────────────────────────────┘
```

---

## Decisión de Tecnología: Broker de Mensajes

### Fase 1 — Redis Streams (ya parcialmente implementado)
Usar el Redis/Valkey que ya existe. Costo $0 extra.

```
Pros:  ya instalado, Python nativo, startup recovery implementado
Cons:  DLQ manual, sin UI de colas, menos features enterprise
```

### Fase 2 — RabbitMQ via CloudAMQP (objetivo a mediano plazo)
Cuando haya 3+ clientes o un cliente nacional de alto volumen.

```
Pros:  DLQ nativa, exchanges para routing, Management UI, AMQP estándar enterprise
Cons:  +$20/mes (CloudAMQP Little Pony), nueva dependencia operacional
Pricing: Little Lemur = gratis (1M msg/mes) → Little Pony = $19/mes (ilimitado)
```

**Decisión:** Implementar con Redis Streams en Fase 2 y diseñar el broker como
interfaz abstracta para migrar a RabbitMQ en Fase 3 sin cambiar el código del worker.

---

## Tenant Registry — Diseño

### Fase 1: In-memory (archivo `app/core/tenant.py`)
Config cargada desde variables de entorno al iniciar. Agregar cliente = agregar env vars
+ desplegar.

```python
# Variables por tenant (ejemplo Kohlberg):
KOHLBERG_PHONE_NUMBER_ID=789012
KOHLBERG_VERIFY_TOKEN=tok_kohlberg_xyz
KOHLBERG_ACCESS_TOKEN=EAAxxxx
KOHLBERG_API_URL=https://api.kohlberg.com
KOHLBERG_API_TOKEN=kohlberg_api_token
```

### Fase 2: DB-backed (tabla `tenants` en PostgreSQL)
Agregar cliente = insertar fila en DB. Sin redeploy.

```sql
CREATE TABLE tenants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            VARCHAR(64) UNIQUE NOT NULL,    -- "odontoking", "kohlberg"
    display_name    VARCHAR(255) NOT NULL,
    phone_number_id VARCHAR(64) UNIQUE NOT NULL,    -- Meta identificador
    wa_access_token TEXT NOT NULL,                  -- Meta token (cifrado)
    verify_token    VARCHAR(255) NOT NULL,           -- Meta webhook verify
    agent_class     VARCHAR(64) NOT NULL,            -- "OdontokingAgent"
    llm_model       VARCHAR(64) DEFAULT 'gpt-4o-mini',
    crm_url         TEXT DEFAULT '',
    crm_token       TEXT DEFAULT '',                 -- (cifrado)
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Plan B: Endpoints por Tenant

Cada cliente tiene su propia URL de webhook configurada en su Meta Developer App.

```
Odontoking:
  GET  /api/v1/whatsapp/odontoking/webhook   ← Meta verification
  POST /api/v1/whatsapp/odontoking/webhook   ← Meta messages

Kohlberg:
  GET  /api/v1/whatsapp/kohlberg/webhook
  POST /api/v1/whatsapp/kohlberg/webhook

Cliente N:
  GET  /api/v1/whatsapp/{slug}/webhook
  POST /api/v1/whatsapp/{slug}/webhook
```

**Backward compatibility:** las rutas legacy `/api/v1/whatsapp/webhook` se mantienen
como alias de `odontoking` durante el periodo de migración.

### Routing logic en el webhook POST:
```
1. Extraer {tenant_slug} del path
2. Buscar TenantConfig en registry (by slug)
3. Verificar que phone_number_id del payload coincide con el del tenant
4. Publicar mensaje en stream/cola del tenant
5. Retornar 200 OK inmediatamente
```

---

## Worker Process — Diseño

### Separación API ↔ Worker
El webhook solo publica en la cola y retorna 200. El worker (proceso separado) consume
y procesa.

```
app/
  main.py          ← FastAPI app (solo webhook + API REST)
  worker.py        ← Worker entrypoint (nuevo archivo)
```

```python
# app/worker.py — estructura básica
async def main():
    tenant_slug = os.getenv("WORKER_TENANT")  # "odontoking" | "kohlberg"
    tenant = get_tenant(tenant_slug)
    agent = build_agent(tenant)               # factoría de agentes

    async for message in consume_stream(tenant_slug):
        await process_message(message, agent, tenant)
        await ack(message)

# Railway: un servicio por tenant con variable WORKER_TENANT distinta
```

---

## Factoría de Agentes — Diseño

```python
# app/core/langgraph/base_agent.py
class BaseAgent(ABC):
    """Clase base abstracta para todos los agentes de la plataforma."""

    TOOLS: list = []
    PROMPT_FILE: str = ""
    LLM_MODEL: str = "gpt-4o-mini"

    @abstractmethod
    async def get_response(self, messages: list, wa_id: str) -> str: ...

    @abstractmethod
    async def clear_history(self, wa_id: str) -> None: ...

    async def close(self) -> None: ...


# app/core/langgraph/odontoking_graph.py
class OdontokingAgent(BaseAgent):
    TOOLS = [get_services, get_specialties, get_doctors, ...]
    PROMPT_FILE = "odontoking.md"
    LLM_MODEL = "gpt-4o-mini"


# app/core/langgraph/kohlberg_graph.py  (Phase 3)
class KohlbergAgent(BaseAgent):
    TOOLS = [get_kohlberg_catalog, update_kohlberg_crm, ...]
    PROMPT_FILE = "kohlberg.md"
    LLM_MODEL = "gpt-4o"


# Factoría
def build_agent(tenant: TenantConfig) -> BaseAgent:
    agents = {
        "odontoking": OdontokingAgent,
        "kohlberg": KohlbergAgent,
    }
    cls = agents.get(tenant.agent_type)
    if cls is None:
        raise ValueError(f"Unknown agent_type: {tenant.agent_type}")
    return cls(tenant=tenant)
```

---

## WhatsApp Client — Cambio Per-Tenant

Hoy `whatsapp_client.py` usa `settings.WHATSAPP_ACCESS_TOKEN` global.
Con multi-tenant, cada función necesita recibir el token del tenant.

```python
# HOY (mono-tenant):
async def send_text_message(to: str, text: str) -> dict:
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}

# OBJETIVO (multi-tenant):
async def send_text_message(to: str, text: str, wa_token: str) -> dict:
    headers = {"Authorization": f"Bearer {wa_token}"}
```

El `wa_token` se propaga desde `TenantConfig.wa_access_token` a través del worker
hasta las llamadas al cliente. No es un breaking change si se hace con valor por defecto.

---

## Estructura de Archivos — Delta

```
app/
  core/
    tenant.py              ← NUEVO: tenant registry
    broker.py              ← NUEVO: abstracción broker (Redis Streams / RabbitMQ)
    langgraph/
      base_agent.py        ← NUEVO: clase base abstracta
      odontoking_graph.py  ← REFACTOR: hereda BaseAgent
      kohlberg_graph.py    ← NUEVO (Phase 3)
  api/
    v1/
      whatsapp.py          ← REFACTOR: rutas /{tenant_slug}/webhook
  worker.py                ← NUEVO: entrypoint del worker process
  models/
    tenant.py              ← NUEVO (Phase 2): SQLModel para DB registry

planning/
  00-plataforma_multitenancy.md   ← este archivo
  01-redis-streams.md             ← diseño detallado del broker (por crear)
  02-kohlberg-onboarding.md       ← plan de onboarding Kohlberg (por crear)
```

---

## Deployment en Railway — Configuración Objetivo

```toml
# railway.toml

[[services]]
name = "api"
startCommand = "uvicorn app.main:app --host 0.0.0.0 --port $PORT"
# Variables: todas las compartidas + Odontoking (mientras no hay DB registry)

[[services]]
name = "worker-odontoking"
startCommand = "python -m app.worker"
# Variables: WORKER_TENANT=odontoking + credenciales Odontoking

[[services]]
name = "worker-kohlberg"
startCommand = "python -m app.worker"
replicas = 3  # nacional = más volumen
# Variables: WORKER_TENANT=kohlberg + credenciales Kohlberg
```

---

## Costo Proyectado por Cliente

| Componente | Compartido | Por cliente |
|---|---|---|
| PostgreSQL (Railway) | $5/mes | — |
| Redis/Valkey (Railway) | $5/mes | — |
| CloudAMQP RabbitMQ (Phase 3) | $0–$20/mes | — |
| API Gateway service | $5/mes | — |
| Worker process (Railway) | — | $5–$10/mes |
| **Total base (2 clientes)** | **$15–$35/mes** | **+$10–$20/mes** |

Con 5 clientes el costo total estimado es **$60–$95/mes**.

---

## Fases de Implementación

### ✅ Fase 0 — Estabilización Odontoking (COMPLETADA)
- Bug buttons WhatsApp corregido (`== 2` → `<= 3`)
- 84 tests unitarios
- Health check completo (DB + Redis + buffer)
- Startup recovery para mensajes huérfanos
- Lock renewal Redis (previene race condition en LLM lento)
- Rate limiting por `wa_id`
- Typing indicator + mark as read
- Fallback timeout LLM
- Persist tasks rastreadas en shutdown
- N+1 CRM corregido (limit 200 → limit 10 + person_id filter)
- Alertas por email (SMTP SSL, debounce 5 min)
- `.env.example` completo

---

### 🔜 Fase 1 — Webhook Multi-Tenant (Plan B)
**Objetivo:** Preparar el webhook para múltiples apps de Meta sin cambiar infraestructura.

**Tareas:**
1. Crear `app/core/tenant.py` — registry in-memory desde env vars
2. Modificar `app/api/v1/whatsapp.py`:
   - Añadir rutas `GET/POST /{tenant_slug}/webhook`
   - Mantener rutas legacy `/webhook` como alias de `odontoking`
   - Routing por `phone_number_id` del payload como verificación secundaria
3. Actualizar `.env.example` con variables por tenant
4. Tests para las nuevas rutas (verify + receive por tenant)

**Resultado:** Odontoking sigue en `/webhook` (sin cambiar Meta config) y
se puede configurar `/odontoking/webhook` en paralelo para la migración.

---

### 🔜 Fase 2 — Redis Streams + Worker Process Separado
**Objetivo:** Garantía at-least-once delivery. Webhook retorna 200 en <5ms.

**Tareas:**
1. Crear `app/core/broker.py`:
   - Interfaz abstracta `MessageBroker`
   - Implementación `RedisStreamBroker` (aio-pika style API sobre redis-py)
   - Reemplazar `MessageBufferService` como backend de producción
2. Crear `app/worker.py` — proceso independiente que consume streams
3. Actualizar `railway.toml` con el nuevo servicio worker
4. Dead Letter Queue: mensajes que fallan 3 veces → email alert + DLQ key en Redis
5. Tests del broker

**Resultado:** Deploy del API no interrumpe mensajes en procesamiento.
Workers escalables independientemente.

---

### 🔜 Fase 3 — BaseAgent + Onboarding Kohlberg
**Objetivo:** Onboardear Kohlberg sin tocar código del router ni del worker.

**Tareas:**
1. Crear `app/core/langgraph/base_agent.py` — clase base abstracta
2. Refactorizar `OdontokingAgent` para heredar `BaseAgent`
3. Reunión con Kohlberg para entender flujo (CRM, catálogo, herramientas)
4. Crear `app/core/langgraph/kohlberg_graph.py`
5. Crear `app/core/prompts/kohlberg.md`
6. Crear tools específicos de Kohlberg en `app/core/langgraph/tools/`
7. Agregar entrada en tenant registry (env vars)
8. Configurar worker-kohlberg en Railway (x3 réplicas para escala nacional)
9. Tests del agente Kohlberg

**Resultado:** Kohlberg operativo. Template para futuros clientes.

---

### 🔜 Fase 4 — DB Registry + Admin Panel
**Objetivo:** Agregar clientes sin redeploy. Panel de administración para la agencia.

**Tareas:**
1. Modelo SQLModel `Tenant` + migración Alembic
2. Reemplazar registry in-memory por DB lookup con caché Redis (TTL 5min)
3. Endpoints admin CRUD de tenants (protegidos por JWT + rol admin)
4. Panel web básico (o usar Swagger) para gestionar tenants
5. Cifrado de tokens sensibles en DB (Fernet/AES)

---

### 🔜 Fase 5 — RabbitMQ + Monitoring Dashboard
**Objetivo:** Enterprise-grade para clientes nacionales de alto volumen.

**Tareas:**
1. Migrar `RedisStreamBroker` → `RabbitMQBroker` (aio-pika)
   - Un VHost por tenant en CloudAMQP
   - Exchange topic: `wa.{tenant}.messages`
   - DLQ nativa con routing key `wa.{tenant}.dlq`
2. Dashboard de colas: RabbitMQ Management UI o integración con Grafana
3. Alertas de DLQ: email cuando un mensaje llega a DLQ
4. Retry policy configurable por tenant (backoff exponencial)

---

## Preguntas Abiertas

1. **Kohlberg — ¿Qué hace exactamente?** (distribuidora, retail, servicios)
   Necesario para diseñar tools y prompt del `KohlbergAgent`.

2. **¿Kohlberg tiene CRM propio?** ¿O usa Sofopolis también?

3. **¿Cuántas conversaciones simultáneas se esperan para Kohlberg?**
   Define el número de réplicas del worker.

4. **¿La agencia necesita un panel de control propio** para ver el estado
   de todos los clientes en tiempo real?

5. **¿Se cobran los clientes por volumen de mensajes** o tarifa fija?
   Impacta el diseño del tracking de uso por tenant.

---

## Referencias

- Código base: `/home/jal/09.platzi/03.agent-production/`
- Tests: `tests/` (84 passing)
- LangGraph docs: https://langchain-ai.github.io/langgraph/
- Redis Streams: https://redis.io/docs/data-types/streams/
- CloudAMQP pricing: https://www.cloudamqp.com/plans.html
- aio-pika (RabbitMQ async Python): https://aio-pika.readthedocs.io/
