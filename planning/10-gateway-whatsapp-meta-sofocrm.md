# 10 · Capa de gateway de WhatsApp: Meta Cloud API ↔ sofo-crm

## Contexto

Hoy el agente habla **directo con Meta Cloud API**: el inbound llega por el webhook de Meta
(`app/api/v1/whatsapp.py`) y el outbound sale por `graph.facebook.com` desde
`app/services/whatsapp_client.py`. Queremos poder elegir por env, con
`WHATSAPP_GATEWAY` (`meta` | `sofo-crm`), si el agente envía/recibe por Meta (actual) o a través
de un **CRM intermediario** (Krayin, `https://imprimir.sofopolis.com`) que a su vez entrega por
Kommo salesbot o Cloud API — según describe `integracion-gateway-whatsapp.md`.

**Confirmado:** el CRM consume los endpoints de Kommo y el agente consume los endpoints del CRM.
Por lo tanto las env vars pasadas (`KOMMO_TOKEN`, `KOMMO_SUBDOMAIN`, `KOMMO_MESSAGE_FIELD_ID`,
`KOMMO_BOT_ID`, `KOMMO_WEBHOOK_SECRET`, `KOMMO_WEBHOOK_INSECURE`) **son configuración del CRM**, no
del agente: el agente **no** llama a la API de Kommo directamente. El agente sólo necesita:

- `CRM_BASE_URL` = `https://imprimir.sofopolis.com`
- `WHATSAPP_AGENT_TOKEN` — el CRM lo manda como `Bearer` en cada evento inbound; lo validamos.
- `CRM_API_KEY` — token Sanctum de un usuario dedicado del CRM, para el outbound. Se obtiene
  **una sola vez** con `POST /api/v1/login` y se guarda en env (la doc advierte que re-loguear
  invalida el token de otras instancias, así que **no** auto-logueamos).

### Contrato de la doc (dos direcciones)

- **Inbound**: `POST <nuestro webhook>` con `Authorization: Bearer <WHATSAPP_AGENT_TOKEN>`, body
  `event=message.received` (contact, message.text, history, window, `reply.url`, `conversation_id`).
- **Outbound**: `POST {reply.url}` (= `.../api/v1/whatsapp/conversations/{id}/messages`) con
  `{"text": ...}` y `Authorization: Bearer <CRM_API_KEY>`. `422` = fuera de la ventana de 24h → no reintentar.

## Enfoque

Introducir una **capa de gateway** de envío seleccionable por env, y una ruta inbound nueva para
el CRM. `WHATSAPP_GATEWAY=meta` (default) mantiene el comportamiento actual; `=sofo-crm` activa el
camino CRM.

### 1. Settings — `app/core/config.py` (junto al bloque "WhatsApp Cloud API", ~línea 239)

```python
self.WHATSAPP_GATEWAY = os.getenv("WHATSAPP_GATEWAY", "meta").strip().lower()  # "meta" | "sofo-crm"
self.CRM_BASE_URL = os.getenv("CRM_BASE_URL", "https://imprimir.sofopolis.com").rstrip("/")
self.WHATSAPP_AGENT_TOKEN = os.getenv("WHATSAPP_AGENT_TOKEN", "")   # inbound auth (CRM→agente)
self.CRM_API_KEY = os.getenv("CRM_API_KEY", "")                    # outbound auth (agente→CRM, Sanctum)
self.WHATSAPP_AUTO_CREATE_PERSON = os.getenv("WHATSAPP_AUTO_CREATE_PERSON", "true").lower() in ("true", "1", "yes")
```

Documentar las mismas en `.env.example`.

### 2. Capa de gateway — nuevo paquete `app/services/gateway/`

Abstrae **solo el envío** (la descarga/transcripción de media siguen en `whatsapp_client.py`).

- `base.py`:
  ```python
  @dataclass
  class Destination:
      wa_id: str                       # teléfono/identificador (ambos + logging)
      phone_number_id: str = ""        # Meta
      token: str = ""                  # Meta
      conversation_id: int | None = None  # CRM
      reply_url: str = ""              # CRM (viene en el evento inbound)

  class MessageGateway(Protocol):
      name: str
      async def send_response(self, dest: Destination, text: str) -> None
      async def send_text(self, dest: Destination, text: str) -> None
      async def send_typing(self, dest: Destination) -> None
      async def mark_read(self, dest: Destination, message_id: str) -> None
  ```
- `meta.py` — `MetaGateway`: delega en las funciones existentes de `app/services/whatsapp_client.py`
  (`send_response`, `send_text_message`, `send_typing_indicator`, `mark_as_read`) usando
  `dest.phone_number_id` / `dest.token`. Reutiliza todo lo actual (interactive payloads, strip markdown).
- `crm.py` — `CrmGateway`: `send_response`/`send_text` → `POST` a `dest.reply_url`
  (o `f"{CRM_BASE_URL}/api/v1/whatsapp/conversations/{dest.conversation_id}/messages"`) con
  `{"text": text}` y `Authorization: Bearer {settings.CRM_API_KEY}`; `httpx.AsyncClient`, timeout ~15s.
  Maneja `422` (ventana cerrada/validación) → `logger.warning` sin re-raise; otros errores → log + raise.
  `send_typing`/`mark_read` → no-op (`logger.debug`, el CRM no expone esas APIs). Logs estilo structlog
  (`crm_text_sent`, `crm_window_closed`, etc.).
- `__init__.py` — `get_gateway() -> MessageGateway`: singleton por `settings.WHATSAPP_GATEWAY`
  (`sofo-crm` → CrmGateway; cualquier otro / `meta` → MetaGateway).

### 3. Inbound del CRM — schema + router nuevos (sin tocar el flujo de Meta)

- `app/schemas/crm.py`: modelos Pydantic del evento `message.received` (contact, message, history,
  window, reply, `conversation_id`, `gateway`, `ai_enabled`). Patrón igual a `app/schemas/whatsapp.py`.
- `app/api/v1/crm.py`: router nuevo con `POST /webhook`, montado en **su propio prefijo** para evitar
  choque con el catch-all `/{tenant_slug}/webhook` de `whatsapp.py`.
  - Registrar en `app/api/v1/api.py`: `api_router.include_router(crm_router, prefix="/crm", tags=["crm"])`
    → URL final `/api/v1/crm/webhook` (ésta es la `WHATSAPP_AGENT_WEBHOOK_URL` que se configura en el CRM).
  - `@limiter.limit(...)` como las otras rutas.
  - Lógica: (1) validar `Authorization: Bearer` == `settings.WHATSAPP_AGENT_TOKEN` → si no, `401`.
    (2) parsear; si `event != "message.received"` o sin texto → `200` ignore. (3) construir
    `Destination(wa_id=contact.phone, conversation_id=..., reply_url=reply.url)`. (4) responder `200`
    rápido y procesar async. (5) `get_gateway().send_response(dest, response_text)`.
  - Procesamiento async: reutilizar `message_buffer_service` con un `process_fn` closure (mismo patrón
    que `_make_process_fn` en `whatsapp.py`) que liga la `Destination` del CRM y llama
    `odontoking_agent.get_response(messages, wa_id=phone, is_new_patient=..., ...)`.
  - Registro de paciente: si `WHATSAPP_AUTO_CREATE_PERSON`, reusar
    `ensure_person_registered` / `ensure_lead_registered` de `app/core/langgraph/tools/crm.py`
    con `wa_id=contact.phone` y `contact.name` (igual que el path de Meta).
  - **History**: ignoramos el `history` del evento; el agente ya persiste su contexto por `wa_id` vía
    el checkpointer de LangGraph. (Nota: si un asesor tomó y devolvió la conversación, los historiales
    pueden divergir — aceptable para v1.)

### 4. Enrutar el outbound de Meta por la capa (para que el switch sea real)

En `app/api/v1/whatsapp.py` (`_make_process_fn`, líneas ~139/166/171/185/191) y `app/worker.py`
(líneas ~58/86/94/109/118): reemplazar las llamadas directas a `send_response`/`send_text_message`/
`send_typing_indicator`/`mark_as_read` por
`get_gateway().<método>(Destination(wa_id=..., phone_number_id=tenant.phone_number_id, token=tenant.wa_access_token), ...)`.
`download_media`/`transcribe_audio` **no** cambian (siguen en `whatsapp_client.py`). Cambio mecánico,
bajo riesgo; deja un único selector `WHATSAPP_GATEWAY` para ambos caminos.

## Archivos

- **Nuevos**: `app/services/gateway/{__init__,base,meta,crm}.py`, `app/schemas/crm.py`, `app/api/v1/crm.py`.
- **Editar**: `app/core/config.py`, `.env.example`, `app/api/v1/api.py` (montar crm_router),
  `app/api/v1/whatsapp.py` y `app/worker.py` (outbound vía gateway).

## Reglas del repo a respetar

structlog en minúsculas_con_guiones sin f-strings; imports al tope; `async`; type hints + Pydantic;
rate-limit en rutas; tenacity si se agregan reintentos; sólo cachear éxito. Debe pasar `make check`
(ruff + pyright) — ver `AGENTS.md`.

## Verificación

1. `make typecheck` y `make lint` (pyright + ruff).
2. Unit del `CrmGateway` con `httpx.MockTransport`: 200 → ok; 422 → warning sin raise.
3. `WHATSAPP_GATEWAY` no seteada → comportamiento Meta idéntico (probar `POST /api/v1/whatsapp/webhook`).
4. `WHATSAPP_GATEWAY=sofo-crm` + `WHATSAPP_AGENT_TOKEN`/`CRM_API_KEY`: `curl -X POST /api/v1/crm/webhook`
   con Bearer válido y body `message.received` de ejemplo (el de la doc) → `200`, el agente procesa y
   hace `POST` al `reply.url`. Verificar el `401` con Bearer inválido.
5. E2E (staging): apuntar `WHATSAPP_AGENT_WEBHOOK_URL` del CRM a `/api/v1/crm/webhook`, mandar un
   WhatsApp real y confirmar que llega la respuesta.
