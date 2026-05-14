# Changelog y Guía de Migraciones

**Fecha última actualización:** 2026-05-14  
**Branch activo:** `dev`

---

## Resumen de cambios por fase

### ✅ Fase 0 — Estabilización Odontoking

| Archivo | Cambio |
|---|---|
| `app/services/whatsapp_client.py` | Bug fix: `len(options) == 2` → `<= 3` (botones WhatsApp) |
| `app/services/whatsapp_client.py` | Nuevas funciones: `mark_as_read()`, `send_typing_indicator()` |
| `app/api/v1/whatsapp.py` | Rate limiting por `wa_id` (20 msg/min, ventana 60s) |
| `app/api/v1/whatsapp.py` | Fallback de timeout LLM con mensaje al usuario |
| `app/api/v1/whatsapp.py` | Mark as read + typing indicator al recibir mensaje |
| `app/core/langgraph/odontoking_graph.py` | `_persist_tasks` set + método `close()` para shutdown seguro |
| `app/core/langgraph/tools/crm.py` | N+1 fix: `limit=200` → `limit=10, person_id=X` en ambas tools |
| `app/services/message_buffer.py` | Lock renewal Redis cada 30s + startup recovery de huérfanos |
| `app/services/message_buffer.py` | TTL dinámico basado en `LLM_TOTAL_TIMEOUT` |
| `app/core/cache.py` | Nuevo método `health_check()` en ambos backends |
| `app/core/config.py` | Modelos ficticios corregidos: `gpt-5-*` → `gpt-4o-mini/gpt-4o` |
| `app/core/config.py` | Nuevas vars: `MAIL_*`, `NOTIFICATION_COOLDOWN_SECONDS` |
| `app/core/notifications.py` | **NUEVO** — alertas email SMTP SSL con debounce 5 min |
| `app/core/logging.py` | Integración `alert_processor` en cadena structlog |
| `app/main.py` | Health check completo (DB + Redis + buffer) |
| `app/main.py` | Startup recovery del buffer + `odontoking_agent.close()` en shutdown |
| `.env.example` | Actualizado con todas las variables |
| `tests/` | **84 tests unitarios** (buffer, webhook, WhatsApp client, tools) |

**Sin migraciones de DB en esta fase.**

---

### ✅ Fase 1 — Webhook Multi-Tenant (Plan B)

| Archivo | Cambio |
|---|---|
| `app/core/tenant.py` | **NUEVO** — tenant registry con lookup en capas (Redis → DB → env) |
| `app/api/v1/whatsapp.py` | Rutas `GET/POST /{tenant_slug}/webhook` por tenant |
| `app/api/v1/whatsapp.py` | Rutas legacy `/webhook` mantenidas como alias de `odontoking` |
| `app/api/v1/whatsapp.py` | `_make_process_fn(tenant)` — closure por tenant para el buffer |
| `app/api/v1/whatsapp.py` | `_AGENT_REGISTRY` dict slug → agente |
| `tests/unit/test_tenant_webhook.py` | **NUEVO** — tests de routing por tenant |

**Sin migraciones de DB en esta fase.**

---

### ✅ Fase 2 — Redis Streams + Worker Process

| Archivo | Cambio |
|---|---|
| `app/core/broker.py` | **NUEVO** — `MessageBroker` abstracto + `RedisStreamBroker` + `InMemoryBroker` |
| `app/core/broker.py` | At-least-once via XREADGROUP/XACK + DLQ tras 3 fallos |
| `app/worker.py` | **NUEVO** — proceso standalone (`python -m app.worker`) |
| `app/worker.py` | Lee `WORKER_TENANT` env var, warmup del agente, shutdown graceful |
| `railway.toml` | Comentarios de configuración para servicios worker por tenant |
| `tests/unit/test_broker.py` | **NUEVO** — tests del broker (publish, ACK, DLQ, retries) |

**Sin migraciones de DB en esta fase.**

---

### ✅ Fase 4 — DB Registry + Admin Panel

| Archivo | Cambio |
|---|---|
| `app/models/tenant.py` | **NUEVO** — SQLModel `Tenant` con todos los campos + billing |
| `app/models/usage_log.py` | **NUEVO** — SQLModel `UsageLog` (contadores diarios por tenant) |
| `app/models/user.py` | Campo `is_admin: bool = False` añadido |
| `app/services/encryption.py` | **NUEVO** — Fernet encrypt/decrypt para tokens en DB |
| `app/core/tenant.py` | Actualizado: lookup en 3 capas (Redis → PostgreSQL → env-vars) |
| `app/core/config.py` | Nuevas vars: `ADMIN_API_KEY`, `ENCRYPTION_KEY`, `TENANT_CACHE_TTL` |
| `app/api/admin/deps.py` | **NUEVO** — dependencia `require_admin` (header X-Admin-Key) |
| `app/api/admin/tenants.py` | **NUEVO** — CRUD completo de tenants |
| `app/api/admin/stats.py` | **NUEVO** — métricas globales y por tenant + profundidad de cola |
| `app/api/admin/conversations.py` | **NUEVO** — gestión de DLQ (listar, reintentar, descartar) |
| `app/api/admin/billing.py` | **NUEVO** — reportes mensuales + export CSV |
| `app/api/admin/router.py` | **NUEVO** — router unificado bajo `/api/v1/admin` |
| `app/main.py` | Include del admin router |
| `alembic/env.py` | Import de `Tenant` y `UsageLog` para autogenerate |
| `.env.example` | Completamente rehecho con todas las variables documentadas |
| `pyproject.toml` | Nueva dep: `cryptography>=44.0.2` |

**⚠️ Requiere migración de DB** — ver sección "Cómo aplicar migraciones" abajo.

---

## Cómo aplicar migraciones

### Primera vez (DB nueva)

```bash
# 1. Configura el entorno
source scripts/set_env.sh development   # o production

# 2. Aplica todas las migraciones en orden
make migrate

# 3. Verifica el estado
make migrate-history
```

### Actualización en producción (DB existente)

```bash
# 1. Haz un backup antes
pg_dump $POSTGRES_DB > backup_$(date +%Y%m%d_%H%M%S).sql

# 2. Aplica la migración de Fase 4
APP_ENV=production source scripts/set_env.sh production
uv run alembic upgrade c3f1a2b4d5e6

# 3. Verifica
uv run alembic current
```

### Rollback de Fase 4 (si algo falla)

```bash
# Vuelve a la migración anterior (elimina tenants, usage_logs y user.is_admin)
uv run alembic downgrade 352d5a24eefc

# ⚠️ ADVERTENCIA: esto elimina todos los datos de tenants y usage_logs
```

---

## Historial de migraciones

| Revision | Fecha | Descripción |
|---|---|---|
| `b25d38b0cd7c` | 2026-04-12 | Initial schema (users, sessions, threads) |
| `352d5a24eefc` | 2026-04-15 | Add chat_histories_odonto table |
| `c3f1a2b4d5e6` | 2026-05-14 | **Fase 4**: tenants + usage_logs + user.is_admin |

---

## Generar nueva migración

```bash
# Después de modificar un modelo SQLModel:
make migration MSG="descripcion del cambio"

# Revisar el archivo generado en alembic/versions/ antes de aplicar
make migrate
```

---

## Variables de entorno nuevas en Fase 4

Agregar a Railway / `.env.production`:

```bash
# Panel de administración
ADMIN_API_KEY=<generar con: python -c "import secrets; print(secrets.token_urlsafe(32))">

# Cifrado de tokens (muy recomendado en producción)
ENCRYPTION_KEY=<generar con: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# Cache de configuración de tenants
TENANT_CACHE_TTL=300
```

---

## Endpoints Admin API

Base URL: `https://TU-DOMINIO/api/v1/admin`  
Auth header: `X-Admin-Key: <ADMIN_API_KEY>`

### Tenants

```
GET    /admin/tenants                    → listar todos
POST   /admin/tenants                    → crear nuevo tenant
GET    /admin/tenants/{slug}             → detalle de un tenant
PATCH  /admin/tenants/{slug}             → editar campos
DELETE /admin/tenants/{slug}             → desactivar (soft delete)
POST   /admin/tenants/{slug}/test        → verificar credenciales Meta + CRM
```

### Monitoring

```
GET    /admin/stats                      → vista global todos los tenants (30 días)
GET    /admin/stats/{slug}?days=30       → detalle por tenant + cola Redis
```

### Dead Letter Queue

```
GET    /admin/tenants/{slug}/dlq         → mensajes fallidos
POST   /admin/tenants/{slug}/dlq/{i}/retry   → reintentar mensaje
DELETE /admin/tenants/{slug}/dlq/{i}    → descartar mensaje
```

### Facturación

```
GET    /admin/billing/{slug}?month=YYYY-MM        → reporte mensual de un tenant
GET    /admin/billing/export/csv?month=YYYY-MM    → CSV de todos los tenants
```

---

## Onboarding de nuevo cliente (Fase 4+)

Con la DB registry activa, agregar un cliente nuevo NO requiere redeploy:

```bash
curl -X POST https://TU-DOMINIO/api/v1/admin/tenants \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "slug": "kohlberg",
    "display_name": "Kohlberg",
    "phone_number_id": "789012345",
    "wa_access_token": "EAAxxx...",
    "verify_token": "tok_kohlberg_xyz",
    "agent_type": "kohlberg",
    "llm_model": "gpt-4o",
    "crm_url": "https://kohlberg.sofopolis.com",
    "crm_token": "api_token_here",
    "billing_tier": "national",
    "msg_limit_month": 20000
  }'
```

Luego configurar en Meta Developer App:
- Callback URL: `https://TU-DOMINIO/api/v1/whatsapp/kohlberg/webhook`
- Verify Token: `tok_kohlberg_xyz`

Y arrancar el worker en Railway:
```toml
# Nuevo servicio Railway
startCommand = "python -m app.worker"
WORKER_TENANT = kohlberg
replicas = 2
```

---

## Tests

```bash
# Correr suite completa
APP_ENV=test uv run pytest tests/ -q

# Solo tests de un módulo
APP_ENV=test uv run pytest tests/unit/test_broker.py -v
APP_ENV=test uv run pytest tests/unit/test_tenant_webhook.py -v

# Estado actual: 112 passed, 1 skipped
```
