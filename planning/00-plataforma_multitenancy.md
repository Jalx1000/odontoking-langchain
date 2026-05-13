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
    crm_url         TEXT DEFAULT '',                 -- Sofopolis u otro CRM
    crm_token       TEXT DEFAULT '',                 -- (cifrado)
    billing_model   VARCHAR(16) DEFAULT 'fixed',    -- "fixed" | "volume"
    billing_tier    VARCHAR(16) DEFAULT 'local',    -- "local" | "national" | "enterprise"
    msg_limit_month INT DEFAULT 5000,               -- umbral para alerta de tier
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

### ✅ Fase 1 — Webhook Multi-Tenant (Plan B)
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

### ✅ Fase 2 — Redis Streams + Worker Process Separado
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

**Contexto Kohlberg:**
- Empresa de vinos, venta nacional en Bolivia
- ~20 conversaciones simultáneas en hora pico → 2 réplicas worker
- Usa Sofopolis CRM (mismo que Odontoking → tools reutilizables ~60%)
- Ya tiene agente en n8n → los flujos n8n son la fuente de verdad para tools

**Input requerido antes de empezar:**
- [ ] Compartir flujos n8n de Kohlberg para mapear tools y lógica de negocio
- [ ] Confirmar endpoints de Sofopolis que usa Kohlberg (¿mismo que Odontoking?)
- [ ] Definir el tono y restricciones del prompt (ej: solo vinos de su catálogo)

**Tareas:**
1. Analizar flujos n8n de Kohlberg → identificar todas las tools necesarias
2. Crear `app/core/langgraph/base_agent.py` — clase base abstracta
3. Refactorizar `OdontokingAgent` para heredar `BaseAgent`
4. Crear tools de Kohlberg en `app/core/langgraph/tools/kohlberg/`:
   - `get_wine_catalog()` — consultar catálogo de vinos
   - `check_stock(product_id)` — disponibilidad por zona/depósito
   - `create_order(...)` — registrar pedido en CRM
   - `get_delivery_zones()` — zonas y tiempos de entrega nacionales
   - `get_price_list(volume)` — precios por volumen (mayorista vs minorista)
   - *(ajustar según flujos n8n reales)*
5. Crear `app/core/langgraph/kohlberg_graph.py`
6. Crear `app/core/prompts/kohlberg.md`
7. Agregar entrada en tenant registry (env vars Kohlberg)
8. Configurar `worker-kohlberg` en Railway (x2 réplicas)
9. Tests del agente Kohlberg (mínimo tools + webhook routing)

**Resultado:** Kohlberg operativo. Template documentado para futuros clientes.
Costo adicional en Railway: ~$10–15/mes (2 workers).

---

### 🔜 Fase 4 — DB Registry + Admin Panel
**Objetivo:** Agregar clientes sin redeploy. Panel de administración para la agencia.

**Tareas:**
1. Modelo SQLModel `Tenant` + migración Alembic (con campos `billing_model`,
   `billing_tier`, `msg_limit_month`)
2. Reemplazar registry in-memory por DB lookup con caché Redis (TTL 5 min)
3. Endpoints admin CRUD de tenants (protegidos por JWT + rol `admin`):
   - `GET /admin/tenants` — listar todos los clientes
   - `POST /admin/tenants` — crear cliente
   - `PATCH /admin/tenants/{slug}` — editar (credenciales, prompt, modelo)
   - `DELETE /admin/tenants/{slug}` — desactivar (soft delete)
   - `POST /admin/tenants/{slug}/test` — verificar credenciales Meta + CRM
4. Endpoints de monitoring:
   - `GET /admin/stats` — métricas globales (mensajes, errores, costos)
   - `GET /admin/tenants/{slug}/stats` — métricas por tenant
   - `GET /admin/tenants/{slug}/conversations` — historial de conversaciones
   - `GET /admin/tenants/{slug}/dlq` — mensajes en Dead Letter Queue
   - `POST /admin/tenants/{slug}/dlq/{msg_id}/retry` — reintentar mensaje
5. Endpoint de facturación:
   - `GET /admin/billing/{slug}?month=2026-05` — reporte mensual
   - `GET /admin/billing/export?month=2026-05` — CSV de todos los tenants
6. Cifrado de tokens sensibles en DB (Fernet/AES con clave en env var)
7. **Frontend MVP:** Retool o Metabase conectado a la DB para el panel visual
   (sin desarrollo frontend propio — más rápido y suficiente para agencia)
8. **Frontend v2 (opcional):** React + Tailwind en repo separado para panel
   branded de la agencia

**Estructura de archivos nueva:**
```
app/
  api/
    admin/
      tenants.py       ← CRUD de tenants
      stats.py         ← métricas y monitoring
      conversations.py ← historial y DLQ
      billing.py       ← reportes de uso y facturación
  models/
    tenant.py          ← SQLModel Tenant
    usage_log.py       ← SQLModel UsageLog (mensajes procesados por tenant/día)
  services/
    billing.py         ← cálculo de uso y generación de reportes
    encryption.py      ← cifrado/descifrado de tokens sensibles
```

**Tabla adicional `usage_logs`:**
```sql
CREATE TABLE usage_logs (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   UUID REFERENCES tenants(id),
    date        DATE NOT NULL,
    msg_received    INT DEFAULT 0,
    msg_processed   INT DEFAULT 0,
    msg_failed      INT DEFAULT 0,
    tokens_input    BIGINT DEFAULT 0,
    tokens_output   BIGINT DEFAULT 0,
    cost_usd        DECIMAL(10,4) DEFAULT 0,
    UNIQUE (tenant_id, date)
);
```

**Resultado:** Onboardear un cliente nuevo toma < 10 minutos sin redeploy.
La agencia tiene visibilidad total de todos los clientes desde un panel.

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

## Contexto de Clientes — Respondido

### 1. Kohlberg — Empresa de Vinos, Bolivia (Nacional)
- **Rubro:** Venta de vinos a nivel nacional en Bolivia
- **Escala:** Nacional — mayor volumen que Odontoking
- **Agente existente:** Ya tiene flujos construidos en n8n (pendiente compartir)
- **Acción:** Compartir flujos n8n de Kohlberg para mapear las tools y el prompt
  del `KohlbergAgent`. Los flujos definen el comportamiento actual del bot
  y son la fuente de verdad para la migración a LangGraph.
- **Tools estimadas:** catálogo de vinos, consulta de stock, gestión de pedidos,
  zonas de entrega, precios por volumen

### 2. CRM — Sofopolis (compartido entre clientes)
- **Mismo CRM para Odontoking y Kohlberg** — ventaja significativa
- Las tools de CRM existentes (`update_crm`, `get_citas`) son **reutilizables**
  con ajustes mínimos de configuración (distinto `crm_url` o `crm_token` por tenant)
- La tabla `tenants` almacena `crm_url` y `crm_token` por cliente
- A futuro: si un cliente usa otro CRM, solo se implementa una nueva tool
- **Ahorro estimado:** ~60% del trabajo de tools en Kohlberg vs hacerlo desde cero

### 3. Capacidad Kohlberg — 20 conversaciones simultáneas
- **Estimado:** 20 conversaciones concurrentes en hora pico
- **Cálculo de workers:**
  - Cada conversación toma ~3–15 segundos de procesamiento LLM
  - Con asyncio un worker maneja múltiples coroutines concurrentes
  - Recomendación: **2 réplicas del worker-kohlberg** como base
  - Escalar a 3 si se superan los 15 msg/seg sostenidos
- **Comparación con Odontoking:** local → 1 réplica es suficiente

```toml
# railway.toml
[[services]]
name = "worker-kohlberg"
startCommand = "python -m app.worker"
replicas = 2  # escalar a 3 si pico supera 15 msg/seg
```

### 4. Panel de Control de Agencia — Contenido Detallado

El panel permite a la agencia monitorear y gestionar todos los clientes desde
un solo lugar sin acceder directamente a la base de datos ni a los logs.

#### 4.1 Dashboard Overview (pantalla principal)
Vista unificada de todos los tenants en tiempo real:

| Sección | Datos mostrados |
|---|---|
| Estado de tenants | Semáforo: activo / degradado / caído por tenant |
| Mensajes hoy | Total procesados por tenant (gráfico de barras) |
| Cola en tiempo real | Mensajes pendientes en cada stream/cola |
| Errores últimas 24h | Contador de errores y tasa de error % por tenant |
| Conversaciones activas | Número de wa_ids con actividad en los últimos 5 min |
| Costo LLM del mes | Tokens consumidos y costo USD por tenant |
| Alertas activas | Listado de alertas sin resolver (DLQ, fallos, etc.) |

#### 4.2 Gestión de Tenants
CRUD completo para onboardear y mantener clientes:

- **Crear tenant:** formulario con todos los campos de `TenantConfig`
  (slug, nombre, tokens Meta, CRM, modelo LLM, prompt base)
- **Editar tenant:** actualizar credenciales sin redeploy
- **Activar / desactivar tenant:** pausar el bot de un cliente sin borrar datos
- **Ver historial de cambios:** quién cambió qué y cuándo
- **Test de conexión:** botón para verificar que las credenciales de Meta y CRM
  son válidas antes de activar

#### 4.3 Monitor de Conversaciones
Vista de las conversaciones de cualquier tenant:

- **Feed en vivo:** mensajes entrantes y respuestas del agente en tiempo real
- **Búsqueda por wa_id:** ver el historial completo de un número de teléfono
- **Transcripción completa:** mensajes del usuario + respuestas del agente
  + tool calls internos (qué consultó el agente y qué respondió el CRM)
- **Marcar para revisión:** flag una conversación problemática para auditoría
- **Escalar a humano:** botón para asignar la conversación a un agente humano
  (integración futura con sistema de tickets)
- **Filtros:** por tenant, fecha, tipo (texto/audio), estado (completada/error)

#### 4.4 Gestión de Prompts
Control del comportamiento del agente sin tocar código:

- **Editor de prompt:** editar el system prompt de cada tenant con syntax highlight
- **Versionado:** historial de versiones del prompt (quién lo editó, cuándo)
- **Rollback:** restaurar una versión anterior con un clic
- **Preview:** simular una conversación con el nuevo prompt antes de publicar
- **A/B testing** (avanzado): dividir el tráfico entre dos versiones de prompt
  y comparar métricas (resolución, duración de conversación, errores)

#### 4.5 Dead Letter Queue (DLQ)
Mensajes que fallaron después de 3 reintentos:

- **Listado de mensajes en DLQ** por tenant con: wa_id, texto, error, timestamp
- **Ver detalle del error:** stack trace completo y tool calls que fallaron
- **Reintentar manualmente:** reencolar un mensaje para que el worker lo procese
- **Descartar:** eliminar un mensaje de la DLQ (con confirmación)
- **Notificación automática:** email a la agencia cuando un mensaje llega a DLQ

#### 4.6 Facturación y Uso
Tracking de consumo por tenant para cobro y control de costos:

| Métrica | Descripción |
|---|---|
| Mensajes recibidos | Total de mensajes entrantes por tenant/mes |
| Mensajes procesados | Los que el agente respondió exitosamente |
| Mensajes en DLQ | Fallidos — indicador de calidad del servicio |
| Tokens LLM | Input + output tokens por tenant (via Langfuse) |
| Costo LLM estimado | Basado en el modelo y tokens (USD) |
| Notas de voz | Cantidad transcrita (costo Whisper) |
| Uptime del bot | % de disponibilidad mensual |

- **Reporte mensual:** PDF/CSV exportable por tenant para facturación
- **Alertas de gasto:** notificación cuando un tenant supera el umbral de costo
- **Proyección:** gráfico de tendencia del consumo

#### 4.7 Configuración de Alertas
Reglas configurables por tenant:

- Umbral de error rate (ej: alertar si >5% de mensajes fallan)
- Umbral de latencia (ej: alertar si P95 > 30 segundos)
- Umbral de costo mensual (ej: alertar si LLM supera $50/mes)
- Destinatarios del email por tenant (cliente puede recibir sus propias alertas)

#### Stack técnico del panel
- **Backend:** FastAPI (mismo repo, rutas `/admin/`)
- **Auth:** JWT con rol `admin` — solo la agencia accede
- **Frontend:** React + Tailwind (repo separado) o Retool/Metabase para MVP
- **Datos:** combinación de PostgreSQL (conversaciones, tenants) + Langfuse API
  (tokens, costos) + Redis (colas en tiempo real)
- **MVP viable con:** Swagger UI del admin API + Grafana para métricas

### 5. Modelo de Cobro — Tarifa Fija + Opción Volumen

**Tarifa fija mensual (recomendada para empezar):**
La agencia cobra un fee fijo por cliente independiente del uso.
Simple de gestionar, predecible para el cliente.

| Tier | Mensajes/mes | Precio sugerido |
|---|---|---|
| Local (ej: Odontoking) | hasta 5,000 | $150–$300/mes |
| Nacional (ej: Kohlberg) | hasta 20,000 | $400–$800/mes |
| Enterprise | ilimitado + SLA | negociado |

**Cobro por volumen (para escalar):**
Útil cuando los clientes crecen mucho. Se cobra por bloque de mensajes.
Ejemplo: $0.05 por mensaje procesado + $50/mes base.

**Implementación en el panel:**
- El panel trackea `mensajes_procesados` por tenant por mes
- Al final del mes se genera el reporte de facturación automáticamente
- Si se supera el tier, el panel alerta a la agencia para renegociar
- El campo `billing_model` en la tabla `tenants` define: `"fixed"` o `"volume"`

---

## Referencias

- Código base: `/home/jal/09.platzi/03.agent-production/`
- Tests: `tests/` (84 passing)
- LangGraph docs: https://langchain-ai.github.io/langgraph/
- Redis Streams: https://redis.io/docs/data-types/streams/
- CloudAMQP pricing: https://www.cloudamqp.com/plans.html
- aio-pika (RabbitMQ async Python): https://aio-pika.readthedocs.io/
