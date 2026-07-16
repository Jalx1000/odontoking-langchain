# Integración del agente IA con el CRM (WhatsApp)

Guía para el equipo que construye el agente LangChain. Describe cómo el CRM le
**manda eventos** y cómo el agente **responde texto**.

Base URL: `https://imprimir.sofopolis.com`

## El flujo en una imagen

```
Cliente por WhatsApp
      │
      ▼
Kommo / Cloud API ──webhook──► CRM  (persiste el mensaje)
                                 │
                                 │ ¿agente activo en esta conversación?
                                 │  sí ▼
                                 └──► POST al AGENTE  (evento message.received)
                                        Authorization: Bearer <WHATSAPP_AGENT_TOKEN>
                                              │
                                       el agente decide la respuesta
                                              │
              POST /api/v1/whatsapp/conversations/{id}/messages ◄──┘
              Authorization: Bearer <API_KEY_DE_USUARIO>
                                 │
                                 ▼
              CRM → gateway activo (Kommo salesbot / Cloud API) → Cliente
```

Dos credenciales distintas, en dos direcciones:

| Dirección | Quién autentica | Con qué |
|---|---|---|
| CRM → Agente (evento) | el CRM se identifica ante el agente | `WHATSAPP_AGENT_TOKEN` (lo definís vos) |
| Agente → CRM (respuesta) | el agente se identifica ante el CRM | **API key de usuario** (token Sanctum de Krayin) |

---

## 1. Configuración en el CRM (`.env`)

```dotenv
# A dónde el CRM manda los eventos entrantes
WHATSAPP_AGENT_WEBHOOK_URL=https://tu-agente-langchain.example/webhook

# Secreto que el CRM manda como Bearer para que tu agente sepa que es el CRM.
# Generá algo aleatorio y largo; tu agente lo valida en cada evento.
WHATSAPP_AGENT_TOKEN=un-secreto-largo-y-aleatorio

# Cuántos mensajes de historial se incluyen en cada evento (opcional, def. 15)
WHATSAPP_AGENT_HISTORY_SIZE=15

# Default global del switch de IA (cada conversación lo puede sobrescribir).
# true = las conversaciones nuevas arrancan con el agente activo.
WHATSAPP_AI_ENABLED=true
```

Después de tocar el `.env`:

```bash
php artisan config:clear
php artisan queue:restart   # el worker relee la config nueva
```

> El agente **solo recibe eventos de conversaciones donde está activado**. El
> asesor lo apaga desde el inbox para tomar el control humano; ahí el CRM deja
> de mandarte eventos de esa conversación.

---

## 2. Autenticación del agente (obtener el API key)

Tu agente necesita un token Sanctum de un usuario del CRM para poder responder.

**Recomendación fuerte: creá un usuario dedicado** (p. ej. `agente@tudominio`) y
usá su token. Motivo abajo.

```bash
curl -X POST https://imprimir.sofopolis.com/api/v1/login \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
    "email": "agente@tudominio.com",
    "password": "········",
    "device_name": "agente-langchain"
  }'
```

Respuesta:

```json
{ "data": { ... }, "token": "12|AbCdEf...elTokenLargo" }
```

Guardá ese `token` y usalo como `Authorization: Bearer 12|AbCdEf...` en cada
llamada al CRM.

> ⚠️ **Importante:** el login **borra todos los tokens anteriores de ese usuario**
> antes de crear uno nuevo. Si tu agente re-loguea en cada arranque, invalida el
> token que estaba usando otra instancia. **Logueate una vez, guardá el token, y
> no vuelvas a loguear** salvo que necesites rotarlo. Por eso conviene un usuario
> exclusivo para el agente: así nadie más le pisa el token.

---

## 3. Evento que el CRM te manda (inbound)

Cuando entra un mensaje del cliente y el agente está activo, el CRM hace:

```
POST  <WHATSAPP_AGENT_WEBHOOK_URL>
Authorization: Bearer <WHATSAPP_AGENT_TOKEN>
Content-Type: application/json
```

Cuerpo:

```jsonc
{
  "event": "message.received",
  "conversation_id": 5,
  "gateway": "kommo",                 // o "cloud_api"
  "ai_enabled": true,
  "contact": {
    "phone": "+59176616013",
    "name": "Alejandro",
    "person_id": 7,                   // contacto en el CRM (puede ser null)
    "lead_id": null                   // lead asociado (puede ser null)
  },
  "message": {
    "id": 164,
    "type": "text",
    "text": "hola, quiero cotizar",
    "timestamp": "2026-07-16T07:10:01-04:00"
  },
  "history": [                        // últimos N mensajes, orden cronológico
    { "role": "user",      "content": "hola",   "type": "text" },
    { "role": "assistant", "content": "¡Hola!", "type": "text" }
  ],
  "window": {                         // ventana de 24h de WhatsApp
    "open": true,
    "expires_at": "2026-07-16T18:01:52-04:00"
  },
  "reply": {                          // dónde responder (ver punto 4)
    "method": "POST",
    "url": "https://imprimir.sofopolis.com/api/v1/whatsapp/conversations/5/messages"
  }
}
```

Qué debe hacer tu agente:

1. **Validar** `Authorization: Bearer` == tu `WHATSAPP_AGENT_TOKEN`. Si no coincide, rechazar.
2. Responder **rápido `200`** al CRM (el trabajo pesado hacelo async).
3. Procesar con LangChain usando `message.text` + `history` + `contact`.
4. Enviar la respuesta con el `reply.url` (punto 4).
5. Respetar `window.open`: si es `false`, WhatsApp no deja texto libre — el POST de respuesta te va a devolver `422`.

---

## 4. Cómo responde tu agente (enviar texto)

### 4.1 Leer contexto (opcional)

```bash
curl https://imprimir.sofopolis.com/api/v1/whatsapp/conversations/5/messages \
  -H 'Accept: application/json' \
  -H 'Authorization: Bearer 12|AbCdEf...'
```

```json
{
  "conversation_id": 5,
  "gateway": "kommo",
  "contact": { "phone": "+59176616013", "name": "Alejandro", "person_id": 7, "lead_id": null },
  "history": [ { "role": "user", "content": "hola", "type": "text" } ]
}
```

### 4.2 Enviar la respuesta

```bash
curl -X POST https://imprimir.sofopolis.com/api/v1/whatsapp/conversations/5/messages \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer 12|AbCdEf...' \
  -d '{ "text": "¡Hola! Con gusto te ayudo a cotizar. ¿Qué necesitás?" }'
```

Respuesta OK:

```json
{ "message": { "id": 165, "status": "queued", "sender": "ia" } }
```

`status: queued` = aceptado y encolado. El CRM lo entrega por el gateway activo
(Kommo salesbot o Cloud API). El mensaje queda marcado como `ia` en el inbox.

Campos del body:

| Campo | Req | Descripción |
|---|---|---|
| `text` | sí | El texto a enviar |
| `reply_to_id` | no | id de un mensaje del CRM al que estás citando (reply) |

---

## 5. Errores que tenés que manejar

| Código | Significado | Qué hacer |
|---|---|---|
| `422` (con `message` de ventana) | Fuera de la ventana de 24h de WhatsApp | No reintentar el texto libre. Esperar a que el cliente escriba, o usar plantilla (no soportado aún) |
| `422` (validación) | Falta `text` u otro campo | Corregir el body |
| `404` | La conversación no existe | Revisar el `conversation_id` |
| `401` / `500` en auth | Token inválido/ausente | Revisar el Bearer. *Nota: la REST API de Krayin hoy devuelve `500` en vez de `401` ante token inválido — es comportamiento global de la plataforma, no de este módulo* |

---

## 6. Checklist para conectar

- [ ] Usuario dedicado para el agente creado en el CRM.
- [ ] `WHATSAPP_AGENT_WEBHOOK_URL` y `WHATSAPP_AGENT_TOKEN` en el `.env` del CRM + `config:clear` + `queue:restart`.
- [ ] El agente valida el `Bearer` de los eventos contra `WHATSAPP_AGENT_TOKEN`.
- [ ] El agente obtuvo su token con `POST /api/v1/login` (una vez) y lo guardó.
- [ ] Probado: mandar un WhatsApp real → el agente recibe el evento → responde por el `reply.url` → al cliente le llega.
- [ ] El asesor puede apagar el agente desde el inbox y tomar el control (deja de recibir eventos).

## 7. Spec navegable

Los endpoints están en Swagger: `https://imprimir.sofopolis.com/api/documentation`
(tag **WhatsApp**). Podés importar el spec desde `storage/api-docs/api-docs.json`.