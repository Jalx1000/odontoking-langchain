# Lead-Dev — Reporte de Síntesis y Arquitectura

> Consolida los reportes de `platform-dev`, `infra-dev`, `security-dev`, `qa-dev` y `devops-dev`
> sobre el proyecto **01.odontoking** (agente de WhatsApp para clínica dental: FastAPI + LangGraph + PostgreSQL + Redis/Valkey + mem0).
> Fecha: 2026-06-10.

---

## Veredicto ejecutivo

El proyecto **funciona y está razonablemente bien estructurado**, pero hoy **NO está listo para producción** sin intervención. Los cinco agentes, de forma independiente, convergen en los mismos tres focos de riesgo:

1. **Existen DOS implementaciones del agente en paralelo** (`LangGraphAgent` legacy vs `OdontokingAgent` real). El agente que realmente atiende pacientes (`OdontokingAgent`) se saltó las protecciones que sí tiene el legacy: sin retries/fallback de LLM, sin trimming de historial, sin tracing consistente. El legacy, que nadie usa, se sigue pre-calentando en el arranque y consume un pool de conexiones completo.
2. **Superficie de seguridad abierta en los webhooks**: sin verificación de firma `X-Hub-Signature-256` y con un endpoint `DELETE history` sin autenticación. Cualquiera con la URL puede inyectar mensajes al CRM real, gastar cuota de OpenAI, o borrar el historial de un paciente.
3. **La red de seguridad de calidad no existe en la práctica**: el 43% de los tests estaba silenciosamente *skipped*, CI no ejecuta `pytest`, y el job de deploy llama a un target de Makefile que no existe (probablemente nunca ha desplegado por CI). Esto permitió que 3 bugs reales (incluido uno de seguros/facturación) llegaran sin detección.

**Severidad global del proyecto: ALTO.** Hay 4 hallazgos CRÍTICOS que deben resolverse antes del próximo despliegue a producción.

---

## Hallazgos transversales (confirmados por ≥2 agentes)

Estos son los más importantes porque varios especialistas los detectaron desde ángulos distintos — señal de que son problemas reales, no ruido.

| # | Hallazgo | Detectado por | Severidad |
|---|----------|---------------|-----------|
| 1 | **`OdontokingAgent` (agente real) bypassa `llm_service`** → sin tenacity, sin fallback circular de modelos, sin `LLM_TOTAL_TIMEOUT`. Una caída/rate-limit de OpenAI tumba la atención. | platform-dev, qa-dev | CRÍTICO |
| 2 | **Historial de mensajes sin límite** se manda completo al LLM cada turno → `GraphRecursionError` que se "arregla" **borrando el checkpoint completo del paciente** (pérdida de contexto/datos). | platform-dev, infra-dev | CRÍTICO |
| 3 | **Webhook de WhatsApp sin verificación de firma `X-Hub-Signature-256`** → inyección de payloads falsos al CRM y consumo de cuota. | platform-dev, security-dev | CRÍTICO |
| 4 | **`DELETE /whatsapp/{tenant}/history/{wa_id}` sin autenticación** (ya en `todo/54`, sin parchear). | security-dev, platform-dev | CRÍTICO |
| 5 | **Secretos reales en disco** (`.env.development`: OpenAI, WA token, JWT, CRM, Langfuse, Admin key) + claves Langfuse reales en historial git de `.env.example`. Hay que **rotar todo**. | security-dev, devops-dev | ALTO |
| 6 | **`LangGraphAgent` legacy no usado** pero pre-calentado en `app/main.py` lifespan, consumiendo un pool asyncpg completo para nada. | platform-dev, infra-dev | ALTO |
| 7 | **CI no ejecuta `pytest` ni security scan**; job de deploy roto (`make docker-build-env` no existe). | qa-dev, devops-dev | ALTO |
| 8 | **Sprawl de pools de conexión** (~75 conexiones/proceso entre SQLModel QueuePool + 2 pools asyncpg + mem0) vs `max_connections=100` de Postgres → 2 réplicas pueden agotarlo. | infra-dev | ALTO |
| 9 | **`DEFAULT_LLM_MODEL="gpt-5-mini"`** no existe en `LLMRegistry` (solo gpt-4o-mini/gpt-4o) → fallback silencioso (`todo/02`). | platform-dev | MEDIO |
| 10 | **Llamadas DB síncronas dentro de handlers `async`** (`get_tenant_async` → `_db_get`, +13 endpoints admin/internal) → bloqueo del event loop. | infra-dev | ALTO |

---

## Bugs concretos descubiertos (de qa-dev, verificados ejecutando)

Tras arreglar el entorno (`uv sync --all-extras --all-groups`): **264 tests, 260 passing, 4 failing**. Antes, 115/264 estaban *skipped* en silencio (100% de `test_memory_service.py`).

- **`insurance.py:54`** — `verify_insurance` siempre sobrescribe `status="VIGENTE"` cuando `has_insurance=True`, incluso si el upstream dice `"VENCIDO"`. **Bug de seguros/facturación.** (CRÍTICO de negocio)
- **`odontoking.py:226`** — `get_doctor_schedule` devuelve el string `"Sin disponibilidad"` en el campo `schedule` en vez de `[]`, rompiendo el contrato `schedule: list`.
- **`odontoking.py:148`** — filtro de `get_doctors` exige `has_availability` truthy; fixtures no lo setean → `IndexError`. O los tests están desactualizados, o hay doctores desapareciendo silenciosamente del agente.

---

## Conflictos / decisiones de arquitectura a resolver

1. **¿Un agente o dos? → Decisión: uno.** Hay que **eliminar `LangGraphAgent` y `graph.py` legacy** (o promoverlo si era el camino correcto, pero infra-dev y platform-dev coinciden en que el vivo es `OdontokingAgent`). Mantener dos grafos duplica deuda y fue la causa raíz de los hallazgos #1, #2 y #6. *Decisión mía como lead: consolidar en `OdontokingAgent` y portarle las protecciones del legacy (llm_service, trimming).*

2. **Multi-tenant: ¿"Plan B" vivo o aspiracional?** El código tiene `TenantConfig` con `crm_token`/`crm_url` per-tenant, pero las tools (`crm.py`, `insurance.py`, `odontoking.py`) construyen los headers con `settings.ODONTOKING_API_TOKEN` **en tiempo de import** — ignorando el tenant. O se compromete con multi-tenant (y se arregla), o se borra el andamiaje muerto. No dejarlo a medias. *Pendiente de tu confirmación de roadmap de producto.*

3. **Health check de Railway apunta al endpoint equivocado** (`/api/v1/health` stub que siempre da 200, en vez de `/health` con verificación real de DB/cache → 503). Decisión trivial: apuntar Railway a `/health`.

---

## Roadmap priorizado

### 🔴 P0 — Antes del próximo deploy a producción (esta semana)
1. Verificar firma `X-Hub-Signature-256` en el webhook de WhatsApp. **(security/platform)**
2. Autenticar (o eliminar) `DELETE /history/{wa_id}`. **(security/platform)**
3. **Rotar TODOS los secretos** expuestos en `.env.development` y los Langfuse del historial git. **(devops/security)**
4. Portar `OdontokingAgent` a `llm_service` (retries + fallback + timeout). **(platform)**
5. Implementar `trim_messages`/ventana de contexto y **eliminar el "fix" que borra el checkpoint**. **(platform)**
6. Arreglar el bug de `verify_insurance` (status sobrescrito). **(platform + qa regression test)**

### 🟠 P1 — Sprint siguiente (estabilidad y red de seguridad)
7. **Añadir `pytest` y security scan (pip-audit/bandit) a CI**; arreglar el job de deploy (`make docker-build-env`). **(devops/qa)**
8. Arreglar los 4 tests fallando y des-skippear los 115; congelar el entorno (`uv sync` en CI). **(qa)**
9. Eliminar `LangGraphAgent` legacy + su pre-warm en `main.py`. **(platform/infra)**
10. Consolidar/limitar pools de conexión; eliminar llamadas DB síncronas en paths async calientes (`get_tenant_async`). **(infra/platform)**
11. Corregir `DEFAULT_LLM_MODEL` y alinearlo con el registry. **(platform)**
12. Apuntar healthcheck de Railway a `/health` real. **(devops)**

### 🟡 P2 — Backlog (deuda y robustez)
13. Política de retención para `checkpoints*` y `chat_histories_odonto` (crecimiento ilimitado). **(infra)**
14. Índice compuesto `(session_id, created_at)`; migración `CREATE EXTENSION vector`; FK `ondelete` en sessions; tipo `cost_usd`. **(infra)**
15. Tests de los flujos críticos sin cobertura: grafo chat/tool_call, `ask_human`/`Command(resume)`, `llm_service` fallback, auth/JWT, admin/billing. Quitar el anti-patrón de DB mockeada en `test_internal.py`. **(qa)**
16. Decidir y ejecutar la postura multi-tenant en las tools (per-tenant token). **(platform/infra)**
17. `hmac.compare_digest` para `X-Admin-Key`; rate-limit en webhook GET, `/internal/usage` y `/admin/*`; `logger.exception` en bloques except. **(platform/security)**
18. Observabilidad en producción (Prometheus/Grafana no están en el compose de prod) + estrategia de backup del volumen Postgres (PII médica). **(devops)**
19. Pinear imágenes `:latest`, Dockerfile multi-stage real, password de Grafana, `logs-conversacion.txt` fuera del repo, PII fuera de logs INFO. **(devops/security)**

---

## Asignaciones

- **platform-dev:** P0 #4, #5, #6; P1 #9, #11; P2 #16, #17. Líder técnico de la consolidación del agente.
- **security-dev:** P0 #1, #2, #3 (con devops); P2 #17, #18 (PII). Verifica cada PR de los P0.
- **infra-dev:** P1 #10; P2 #13, #14. Propuesta de límites de pool antes de tocar `main.py`.
- **qa-dev:** P0 #6 (regression test del seguro primero); P1 #7, #8; P2 #15. Bloquea merges de webhook/billing sin test.
- **devops-dev:** P0 #3 (rotación); P1 #7, #12; P2 #18, #19.

---

## Blocked on (requiere tu decisión)
- **Postura multi-tenant** (conflicto #2): ¿comprometemos el "Plan B" per-tenant o borramos el andamiaje? Define el alcance del item #16.
- **Confirmación para rotar secretos** y para eliminar el agente legacy `LangGraphAgent` (cambio irreversible de código, aunque está sin uso).

## Next check-in
Tras cerrar P0 (estimado: fin de semana). Reportes individuales en `reports/report1/{platform,infra,security,qa,devops}-dev.md`.
