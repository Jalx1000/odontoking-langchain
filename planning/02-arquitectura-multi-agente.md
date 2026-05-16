# 02 — Arquitectura Multi-Agente (Event-Driven)

## Contexto

El sistema actual (`03.agent-production`) actúa como plataforma monolítica:
recibe webhooks de Meta, procesa con LangGraph internamente y responde a WhatsApp.
Este documento planifica la evolución hacia una arquitectura donde cada cliente
tiene su propio agente aislado, desplegado independientemente.

---

## Arquitectura objetivo

```
Meta / WhatsApp Cloud API
         │
         ▼ POST /{tenant_slug}/webhook
┌────────────────────────────────────────────────┐
│              PLATFORM (router puro)             │
│  03.agent-production                            │
│                                                 │
│  • Verifica tenant (Redis → PostgreSQL)         │
│  • Rate limiting (Redis)                        │
│  • Parsea payload (text/audio/interactive)      │
│  • Mark as read (background)                    │
│  • Publica a RabbitMQ wa.{tenant}               │
│  • Admin API · Billing · Stats · Users          │
└─────────────────┬──────────────────────────────┘
                  │ RABBITMQ (compartido)
      ┌───────────┼────────────┬────────────┐
      │           │            │            │
wa.odontoking  wa.kohlberg  wa.tenant-N  wa.tenant-M
      │           │            │            │
      ▼           ▼            ▼            ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│odontoking│ │kohlberg  │ │tenant-N  │ │tenant-M  │
│-agent    │ │-agent    │ │-agent    │ │-agent    │
│          │ │          │ │          │ │          │
│LangGraph │ │LangGraph │ │LangGraph │ │LangGraph │
│PG propio │ │PG propio │ │PG propio │ │PG propio │
│Tools     │ │Tools     │ │Tools     │ │Tools     │
└────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
     │             │            │             │
     └─────────────┴────────────┴─────────────┘
                         │
               WhatsApp Cloud API
         (directo, credenciales del tenant)
```

---

## Responsabilidades por servicio

### Platform (03.agent-production)
| Responsabilidad | Queda | Sale |
|----------------|-------|------|
| Recibir webhook Meta | ✅ | |
| Parsear payload WA | ✅ | |
| Rate limiting IP + user | ✅ | |
| Tenant registry (DB + cache) | ✅ | |
| Publicar a RabbitMQ | ✅ | |
| Admin API (tenants, stats, billing, DLQ) | ✅ | |
| Usuarios y sesiones | ✅ | |
| Prometheus + Grafana | ✅ | |
| LangGraph processing | | ❌ |
| LLM Service (retry + fallback) | | ❌ |
| Memory Service (mem0) | | ❌ |
| Enviar respuesta a WhatsApp | | ❌ |
| Persistir historial de chat | | ❌ |

### Cada Agent Service
| Responsabilidad |
|----------------|
| Consumir de `wa.{slug}.messages` (RabbitMQ) |
| Typing indicator (WhatsApp) |
| LangGraph: chat node + tool_call node |
| LLM Service con retry + fallback |
| Memory Service (mem0 + pgvector propio) |
| Persistir historial en su PostgreSQL |
| Enviar respuesta directo a WhatsApp |
| Reportar usage al platform (`POST /internal/usage`) |

---

## Infraestructura compartida vs aislada

| Recurso | Compartido | Aislado por tenant |
|---------|-----------|-------------------|
| RabbitMQ | ✅ (exchange por tenant) | |
| Redis / Valkey | ✅ (namespaceado por slug) | |
| Langfuse | ✅ (tag `tenant=slug`) | |
| Prometheus | ✅ | |
| PostgreSQL | | ✅ (DB propia) |
| pgvector (mem0) | | ✅ (en su misma DB) |
| LangGraph checkpoints | | ✅ (en su misma DB) |
| Historial de chat | | ✅ (en su misma DB) |

---

## Contrato de comunicación

### Platform → RabbitMQ (publish)
```json
// Exchange: wa.{tenant_slug}
// Routing key: messages
{
  "wa_id": "593987654321",
  "text": "Quiero una cita",
  "tenant_slug": "odontoking-dev",
  "message_id": "wamid.xxx",
  "timestamp": 1716000000
}
```

### Agent → WhatsApp Cloud API (directo)
```
POST https://graph.facebook.com/v25.0/{phone_number_id}/messages
Authorization: Bearer {wa_access_token}
```
Las credenciales vienen de env vars propias del agente.

### Agent → Platform (usage report)
```
POST {PLATFORM_URL}/api/v1/internal/usage
X-Internal-Key: {PLATFORM_INTERNAL_KEY}

{
  "tenant_slug": "kohlberg",
  "wa_id": "593987654321",
  "tokens_input": 1500,
  "tokens_output": 800,
  "cost_usd": 0.0023,
  "msg_processed": 1
}
```

---

## Variables de entorno

### Platform
```env
RABBITMQ_URL=amqps://shared.cloudamqp.com/...
VALKEY_HOST=shared-redis.railway.internal
POSTGRES_HOST=platform-db.railway.internal
ADMIN_API_KEY=...
INTERNAL_API_KEY=...          # nuevo — para usage reports de agentes
```

### Cada Agent Service
```env
RABBITMQ_URL=amqps://shared.cloudamqp.com/...    # mismo
VALKEY_HOST=shared-redis.railway.internal          # mismo
POSTGRES_HOST=kohlberg-db.railway.internal         # PROPIO
TENANT_SLUG=kohlberg
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_ACCESS_TOKEN=...
OPENAI_API_KEY=...
PLATFORM_URL=https://platform.up.railway.app
PLATFORM_INTERNAL_KEY=...
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
```

---

## Estructura del Agent Template

```
agent-template/
├── app/
│   ├── main.py                  # FastAPI: /health + lifespan con worker
│   ├── worker.py                # RabbitMQ consumer loop
│   ├── agent.py                 # BaseAgent: LangGraph graph + get_response()
│   ├── config.py                # Settings (Pydantic)
│   ├── whatsapp_client.py       # send_text/interactive/typing (copy del platform)
│   ├── usage.py                 # report_usage() → POST platform/internal/usage
│   ├── tools/
│   │   └── __init__.py          # tools específicos del cliente van aquí
│   └── prompts/
│       └── system.md
├── Dockerfile                   # multi-stage: python:3.13-slim → app
├── railway.toml                 # builder=dockerfile, healthcheck=/health
├── pyproject.toml
├── .env.example
└── README.md
```

---

## Sprints de implementación

### Sprint 1 — Platform como router puro
**Objetivo:** el platform publica a RabbitMQ y no procesa nada.
**Cambios en 03.agent-production:**

- [ ] Agregar `agent_type` como key del `_AGENT_REGISTRY` (en vez de slug)
- [ ] Agregar campos `agent_endpoint_url`, `agent_api_key` al modelo Tenant
- [ ] Alembic migration para los nuevos campos
- [ ] Crear `POST /api/v1/internal/usage` con auth `X-Internal-Key`
- [ ] Admin Portal: exponer `agent_type` en formulario de Tenant
- **Backward compat:** odontoking sigue funcionando como agente interno

### Sprint 2 — Agent Template
**Objetivo:** repo base que cualquier agente pueda fork-ear.

- [ ] Crear repo `agent-template`
- [ ] Worker que consume de RabbitMQ `wa.{TENANT_SLUG}.messages`
- [ ] BaseAgent con LangGraph (chat node + tool_call node)
- [ ] LLM Service con tenacity retry + fallback circular
- [ ] Memory Service con mem0 + pgvector
- [ ] `whatsapp_client.py` (send functions con credenciales del env)
- [ ] `usage.py` (reportar tokens al platform)
- [ ] Dockerfile + railway.toml
- [ ] `.env.example` documentado

### Sprint 3 — Primer agente externo
**Objetivo:** desplegar un cliente real con el nuevo patrón.

- [ ] Fork `agent-template` → `{cliente}-agent`
- [ ] Implementar tools específicos del cliente
- [ ] Configurar system prompt
- [ ] Deploy en Railway (nueva PostgreSQL + variables propias)
- [ ] Registrar tenant en Admin Portal con `agent_type` correcto
- [ ] Validar flujo end-to-end con mensajes reales

### Sprint 4 — Migrar Odontoking (opcional)
**Objetivo:** sacar odontoking del monolito cuando esté estable el patrón.

- [ ] Fork `agent-template` → `odontoking-agent`
- [ ] Mover `odontoking_graph.py` + tools + prompts al nuevo repo
- [ ] Deploy en Railway (PostgreSQL separado para odontoking)
- [ ] Migrar datos históricos (chat_history_odonto)
- [ ] Eliminar `odontoking_graph.py` del platform
- [ ] Limpiar imports y `_AGENT_REGISTRY` del platform

---

## Cuándo migrar (criterios)

| Criterio | Migrar ahora | Esperar |
|---------|-------------|---------|
| Clientes con agentes distintos | 3+ | < 3 |
| Tamaño del equipo | 3+ devs | 1-2 devs |
| Deploys independientes por cliente | Requerido | No urgente |
| DBs separadas requeridas (contrato) | Sí | No |

**Recomendación actual:** Hacer Sprint 1 (agent_type como key) y Sprint 2 (template) ahora.
Migrar Odontoking en Sprint 4 cuando haya un segundo cliente real que lo justifique.

---

## Estado

- [ ] Sprint 1 — Platform router puro
- [ ] Sprint 2 — Agent Template
- [ ] Sprint 3 — Primer agente externo
- [ ] Sprint 4 — Migrar Odontoking
