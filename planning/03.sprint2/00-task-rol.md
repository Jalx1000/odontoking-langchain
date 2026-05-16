# Sprint 2 — Agent Template

## Objetivo

Crear el repositorio `agent-template`: una base standalone que cualquier agente cliente forkea, configura con sus variables de entorno y despliega en Railway en menos de una hora.

---

## Que hace el repo agent-template

El `agent-template` es un microservicio Python que vive independiente del platform. Su ciclo de vida es:

```
RabbitMQ queue (wa.{TENANT_SLUG}.messages)
        │
        ▼  aio-pika consumer (prefetch=10)
  AgentWorker.start()
        │
        ├─ send_typing_indicator(wa_id)            ← WhatsApp Cloud API (credenciales propias)
        │
        ├─ BaseAgent.get_response(text, wa_id)
        │     ├─ asyncio.gather(graph.aget_state, memory.search)
        │     ├─ LangGraph: chat node → [tool_call node →] END
        │     │     LLM: tenacity retry + fallback gpt-4o-mini
        │     └─ post-response: memory.add + usage.report_usage (fire-and-forget)
        │
        ├─ send_response(wa_id, response)           ← WhatsApp Cloud API
        ├─ mark_as_read(wa_id, message_id)          ← WhatsApp Cloud API
        └─ message.ack()
```

El agente NO recibe webhooks de Meta. El platform (03.agent-production) ya parseo el payload y lo publico en RabbitMQ. El agente solo consume, procesa y responde.

---

## Estructura del repositorio

```
agent-template/
├── app/
│   ├── main.py                  # FastAPI app: GET /health + lifespan con AgentWorker
│   ├── config.py                # Pydantic Settings (todas las env vars)
│   ├── agent.py                 # BaseAgent: LangGraph + get_response()
│   ├── worker.py                # AgentWorker: consume loop aio-pika
│   ├── llm.py                   # LLMService: tenacity retry + fallback
│   ├── memory.py                # MemoryService: mem0 + pgvector
│   ├── usage.py                 # report_usage() → POST platform /internal/usage
│   ├── whatsapp_client.py       # send_text/interactive/typing/mark_as_read
│   └── tools/
│       └── __init__.py          # tools = []  (override al forkear)
├── prompts/
│   └── system.md                # System prompt plantilla
├── Dockerfile                   # Multi-stage: builder + runner (python:3.13-slim)
├── railway.toml                 # builder=dockerfile, healthcheck=/health, timeout=120
├── pyproject.toml               # Dependencias con uv
├── uv.lock                      # Lock file (commitear)
├── .env.example                 # Todas las vars documentadas con ejemplos
├── .github/
│   └── workflows/
│       └── ci.yml               # lint + typecheck + docker build
└── README.md                    # Guia de fork + deploy
```

---

## Diferencias clave vs el monolito (03.agent-production)

| Aspecto | Platform (monolito) | Agent Template |
|---------|--------------------|--------------------|
| Entrada | POST /webhook de Meta | Queue RabbitMQ (aio-pika) |
| Credenciales WA | Por tenant en DB/Redis | Env vars fijas (es un solo tenant) |
| PostgreSQL | Compartida entre tenants | Una DB propia por agente |
| LLM service | Registry multi-modelo, fallback circular | Un modelo default + fallback a gpt-4o-mini |
| Memory | `memory_service` singleton con config global | `MemoryService` con collection `{slug}_memory` |
| Escalado | FastAPI workers + workers separados | Un solo proceso (RabbitMQ consumer es async) |
| Sesiones/Auth | JWT, usuarios, sesiones | No tiene — solo wa_id como thread_id |
| Billing | Integrado | Solo reporta via POST /internal/usage |

---

## Asignacion de agentes

| Agente | Responsabilidad | Archivos |
|--------|----------------|----------|
| `template-dev` | Todo el codigo Python | `app/` completa, `prompts/system.md`, `pyproject.toml` |
| `devops-dev` | Infraestructura + CI | `Dockerfile`, `railway.toml`, `.env.example`, `.github/workflows/ci.yml`, `README.md` |

---

## Grafo de dependencias

```
[template-dev]
    T-1: config.py + pyproject.toml
         │
         ├─► T-2: whatsapp_client.py   (necesita settings)
         ├─► T-3: llm.py               (necesita settings)
         ├─► T-4: memory.py            (necesita settings + POSTGRES_DSN)
         ├─► T-5: usage.py             (necesita settings + PLATFORM_URL)
         │
         T-2 + T-3 + T-4 + T-5 ────► T-6: agent.py    (usa todo)
                                           │
                                           ▼
                                      T-7: worker.py   (usa agent + whatsapp_client)
                                           │
                                           ▼
                                      T-8: tools/ + prompts/system.md
                                           │
                                           ▼
                                      main.py          (usa worker)

[devops-dev]  — puede trabajar en paralelo con template-dev desde D-1
    D-1: Dockerfile          (no depende de codigo Python)
    D-2: railway.toml        (no depende de codigo Python)
    D-3: .env.example        (depende de T-1 — lista de vars)
    D-4: GitHub Actions CI   (depende de Dockerfile + pyproject.toml)
    D-5: README.md           (depende de todo para documentar)
```

**Orden de arranque:**
1. `template-dev` empieza con T-1 (config + pyproject)
2. `devops-dev` empieza con D-1 (Dockerfile) en paralelo
3. `devops-dev` espera a que T-1 este listo para D-3 (.env.example)
4. `devops-dev` hace D-4 y D-5 al final

---

## Definition of Done

- [ ] `GET /health` responde 200 con `{"status": "ok", "tenant": "{slug}"}`
- [ ] Worker consume mensajes de `wa.{TENANT_SLUG}.messages` y hace ACK correcto
- [ ] Typing indicator se envia antes de procesar
- [ ] LangGraph checkpointing funciona con PostgreSQL propio
- [ ] Memory search y graph state se consultan concurrentemente (asyncio.gather)
- [ ] Memory se actualiza post-respuesta como fire-and-forget
- [ ] `report_usage()` hace POST al platform y no explota si falla (best-effort)
- [ ] Mensajes fallidos van a DLQ de RabbitMQ (nack requeue=False)
- [ ] Dockerfile buildea sin errores (`docker build .`)
- [ ] `railway.toml` con healthcheck en `/health` y timeout 120s
- [ ] `.env.example` documenta todas las variables de T-1
- [ ] CI pasa: lint (ruff) + typecheck (pyright) + docker build
- [ ] `make typecheck` (pyright) pasa sin errores en modo standard

---

## Como usar este template (Sprint 3 en adelante)

Despues de Sprint 2, crear un nuevo agente cliente es:

1. **Fork** este repo → renombrar a `{cliente}-agent`
2. **Agregar tools** del cliente en `app/tools/__init__.py`
3. **Personalizar** `prompts/system.md` con las instrucciones del negocio
4. **Configurar env vars** (ver `.env.example`): credenciales WA del cliente, su POSTGRES_DSN propio, TENANT_SLUG, PLATFORM_URL
5. **Deploy en Railway**: nuevo servicio + nuevo PostgreSQL plugin (Railway lo provisiona en 1 click)
6. **Registrar tenant** en el Admin Portal del platform con `tenant_slug` correcto

El platform automaticamente empieza a publicar mensajes en `wa.{slug}.messages` cuando el tenant esta registrado y activo.

---

## Notas de arquitectura

- **Un solo worker process**: el consumer RabbitMQ es completamente async. Usar multiples workers causaria procesamiento duplicado del mismo mensaje. `--workers 1` en uvicorn es intencional.
- **PostgreSQL propio**: cada agente tiene su propia DB para checkpoints (LangGraph) y memoria vectorial (pgvector). Esto garantiza aislamiento total de datos entre clientes.
- **Sin Redis**: el agente template no requiere Redis. La cache de memoria es in-process (opcional). Si un cliente la necesita, puede agregar la dependencia como optional en pyproject.toml.
- **Credenciales WA en env vars**: a diferencia del platform donde las credenciales estan en DB por tenant, aqui son env vars directas porque el proceso ES el tenant.
