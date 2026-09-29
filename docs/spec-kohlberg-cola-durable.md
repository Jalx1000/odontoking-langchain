# Spec: cola durable para el agente de Kohlberg (nada se pierde en una caída)

> Estado: **implementado (código), detrás del flag `KOHLBERG_USE_BROKER` (OFF por default)**.
> Rama: `kohlberg-v2`. Backend: **Redis Streams**. Iniciativa separada de la optimización
> de tokens (`spec-kohlberg-optimizacion-tokens.md`), mismo principio anti-regresiones.
>
> **Hecho:** `KOHLBERG_USE_BROKER` + `KOHLBERG_BROKER_TENANT` (config); `crm.py` publica al
> broker con fallback al buffer; tenant `kohlberg` registrado; `worker.py` con handler
> propio de Kohlberg (`_handle_kohlberg_message`: responde por `reply_url`, hace handoff,
> y **propaga** errores para que la cola reintente); tests herméticos. Ruff (mis archivos)
> + pyright limpios.
>
> **Falta (ops, lo hace el usuario):** desplegar el servicio `worker-kohlberg` en Railway
> (`WORKER_TENANT=kohlberg`, mismas envs, Valkey/Redis, replicas=1); validar local con el
> flag OFF→ON; recién ahí `KOHLBERG_USE_BROKER=true` en prod. Idempotencia (dedupe por
> `message_id` en reclaim) queda como mejora futura: hoy se acepta una posible respuesta
> duplicada en el caso raro de crash entre el envío y el ACK.

## Objetivo

Que **ningún mensaje se pierda** si algo del agente se cae (cuota de OpenAI, crash,
timeout). Hoy el path de Kohlberg usa el `message_buffer` en-proceso, que **borra el
mensaje al drenar ANTES de procesar** → un fallo del LLM lo pierde sin replay (esto
causó los mensajes sin responder en la caída por cuota). Con una cola durable de
**ACK-tras-respuesta-exitosa + reintento**, un fallo deja el mensaje **sin ACK → se
reprocesa solo** cuando el servicio vuelve. No hace falta que el CRM re-emita nada.

## Estado actual (medido en el código)

- `app/core/broker.py`: broker completo — publish/consume, ACK explícito, reintentos
  (3), reclaim de pendientes (`XAUTOCLAIM`), DLQ durable (Redis List). Backends
  RabbitMQ y **Redis Streams**. ✔ existe.
- `app/worker.py`: proceso consumidor (`python -m app.worker`, `WORKER_TENANT`). ✔ existe.
- `app/api/v1/whatsapp.py:329`: ya publica al broker (otro gateway). ✔
- **Huecos para Kohlberg:**
  1. `crm.py` NO publica al broker — usa `message_buffer` (lossy).
  2. `worker.py` no arma `KohlbergAgent` (registry solo odontoking/imprimir) y su
     `Destination(wa_id=...)` no lleva `reply_url`/`conversation_id` (el CRM responde
     por ahí), ni maneja handoff/dedupe/lead/person.
  3. Precondición: `get_tenant("kohlberg")` debe existir (el worker sale si es None).

## Decisiones tomadas (2026-09-26)

- **Backend: Redis Streams.** Valkey/Redis ya está desplegado → cero infra nueva; DLQ
  durable; el worker ya lo soporta. (RabbitMQ descartado: infra nueva, guard del worker
  que lo rechaza, DLQ en-memoria.)
- **Conservar agrupado (5s) + FIFO por `wa_id`**: el debounce y el orden se hacen en el
  worker. Preserva el ahorro de tokens y evita carreras en el checkpointer.

## Diseño

### Flujo
```text
CRM → POST /webhook (crm.py)
  guards: handoff-open skip · dedupe · type text/interactive · arma patient_ctx+Destination
  → broker.publish("kohlberg", convo_key, payload)   ← DURABLE desde acá (XADD)
  → 200 OK inmediato
                                   (proceso aparte)
worker-kohlberg (python -m app.worker, WORKER_TENANT=kohlberg, replicas=1)
  consume (XREADGROUP, block 5s, count 10)
  → agrupa por wa_id la tanda leída (debounce natural de la ventana) + FIFO
  → KohlbergAgent.get_response(...) con lead_id/person_id/nombres
  → gateway.send_response(dest={wa_id, conversation_id, reply_url})
  → handoff después de responder (si aplica)
  → XACK  (solo si TODO lo anterior salió bien)
  fallo reintentable → NO ACK → reclaim/reintento → 3 fallos → DLQ + alerta email
```

### Payload publicado (lo que el worker necesita para responder)
`text`, `message_id`, `conversation_id`, `reply_url`, `contact` (`lead_id`,
`person_id`, `name`, `channel`), y el estado de handoff. (Hoy `crm.py` ya tiene todo
esto en el evento; solo hay que serializarlo al publicar.)

### Semántica de error (CLAVE — sin esto la cola no sirve)
- **Reintentable** (cuota/timeout/5xx de OpenAI, Redis/gateway caído): el handler
  **relanza** → el entry queda pendiente → se reintenta/reclama. **No** se manda la
  disculpa (habrá reintento; evita spamear).
- **Terminal** (payload inválido, error no recuperable): se **traga** (ACK) para no
  reintentar en loop; se puede mandar la disculpa.
- Distinción por tipo de excepción (LLMService ya lanza `RateLimitError`,
  `APITimeoutError`, `APIError`, y `RuntimeError` en timeout de presupuesto).

### Idempotencia (at-least-once ⇒ puede reprocesar)
Si se responde pero el proceso cae antes del ACK, el mensaje se reclama y se
reprocesaría → doble respuesta. Mitigación: dedupe por `message_id` en el worker
(SET en Redis con TTL). El checkpointer ya evita duplicar el registro del pedido.

### Debounce + orden
`replicas=1` para el worker de Kohlberg → orden natural del stream (FIFO). El debounce
sale de la ventana `block=5s` de `XREADGROUP`: la tanda leída se agrupa por `wa_id` y
se procesa combinada (mismo efecto que la ventana de 5s del buffer). Si más adelante
hace falta escalar, se pasa a lock por `wa_id`.

## Rollout seguro (anti-regresiones)
- **Feature-flag `KOHLBERG_USE_BROKER`** (default OFF): con OFF sigue el
  `message_buffer` actual; con ON, `crm.py` publica al broker. Permite **flip
  instantáneo de ida y vuelta sin redeploy** — rollback inmediato si algo sale mal.
- **Fallback en publish**: si `broker.publish` lanza (Redis caído), `crm.py` cae al
  path en-proceso para no perder el mensaje.
- Despliegue en dos pasos: (1) desplegar el worker con el flag OFF y verlo consumir en
  vacío; (2) flip a ON en una ventana de bajo tráfico observando logs.

## Commands / archivos tocados
```bash
WORKER_TENANT=kohlberg python -m app.worker   # correr el worker local
make check && uv run pytest
```
`app/api/v1/crm.py`, `app/worker.py`, `app/core/config.py` (flag), `app/core/tenant.py`
(alta de kohlberg si falta), tests nuevos.

## Testing / Verificación
- **Test de durabilidad:** publicar un mensaje, forzar que el handler lance (simular
  cuota) → el entry queda pendiente, no se ACKea; al "reponer" (handler ok) se procesa
  y ACKea. 
- **Test de idempotencia:** reprocesar el mismo `message_id` no dispara doble respuesta.
- **Test de agrupado/orden:** 3 mensajes rápidos del mismo `wa_id` → un solo turno,
  en orden.
- **Charla dorada** (la del spec de tokens) equivalente por el path del broker.
- `make check` + `pytest` verdes.

## Boundaries
- **Always:** ACK solo tras respuesta exitosa; relanzar en errores reintentables;
  dedupe por `message_id`; flag para rollback; `make check`+tests antes de commit.
- **Ask first:** cambiar de Redis Streams a RabbitMQ; subir réplicas del worker (toca
  el orden por `wa_id`); tocar la semántica retryable/terminal.
- **Never:** ACKear un mensaje no procesado; mandar disculpa en un error reintentable
  (habrá reintento); desplegar sin el flag; romper el flujo de venta / contratos CRM.

## Success Criteria
- Con el flag ON: un fallo del LLM deja el mensaje **pendiente** (visible en el stream)
  y se **reprocesa** al reponerse el servicio, sin intervención del CRM.
- Tras 3 fallos, el mensaje va al **DLQ** con alerta por email (no se pierde).
- Mensajes rápidos del mismo cliente se agrupan (sin regresión de tokens) y en orden.
- Flip OFF↔ON sin redeploy; charla dorada equivalente; `make check`+tests verdes.

## Riesgos
| Riesgo | Mitigación |
|---|---|
| Handler traga error y ACKea (no reintenta) | Semántica retryable/terminal explícita + test de durabilidad |
| Doble respuesta al reclamar | Dedupe por `message_id` en el worker |
| Desorden/carrera con réplicas | `replicas=1` (orden natural); lock por `wa_id` si se escala |
| Worker no desplegado ⇒ silencio | Flag OFF por default hasta que el worker esté vivo y verificado |
| Publish falla (Redis caído) | Fallback al path en-proceso |

## Open Questions
1. ~~¿`get_tenant("kohlberg")` existe?~~ **NO** — el registry sync (`tenant.py:71`,
   `_ENV_REGISTRY`) solo tiene `odontoking`; el worker usa `get_tenant` (sync) y saldría.
   **Tarea:** dar de alta `kohlberg` en el env-registry (o que el worker use el lookup
   async con fallback a DB). Precondición del worker.
2. ¿El worker de Kohlberg se despliega como servicio nuevo en Railway ahora, o primero
   validamos local con el flag y luego se despliega? (Recomendado: local con flag → deploy.)
