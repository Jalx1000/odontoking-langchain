# CRM (Krayin) — Soporte de mensajes interactivos de WhatsApp (botones y listas)

Documento para el equipo del CRM. Describe qué falta implementar para que el agente IA
pueda **enviar botones/listas** por WhatsApp y **recibir la opción que el cliente toca**.

Complementa a `integracion-gateway-whatsapp.md` (contrato base de mensajería). Aquí solo se
describe lo **nuevo**; todo lo demás (auth, webhook, ventana de 24h) sigue igual.

Base URL (despliegue Ponce): `https://poncedeleon.sofopolis.com`

> El agente responde siempre a la `reply.url` que viene en el evento entrante (no arma la URL desde
> una plantilla), así que un cambio de host del lado del CRM no lo rompe.

---

## 1. Contexto y objetivo

Hoy la mensajería es **solo texto**:

- **Salida** (agente → CRM): `POST /api/v1/whatsapp/conversations/{id}/messages` acepta `{ "text": "..." }`.
- **Entrada** (cliente → CRM → agente): el evento `message.received` trae `message.type: "text"`.

WhatsApp Cloud API soporta **mensajes interactivos** (botones de respuesta rápida y listas).
Queremos usarlos para, por ejemplo, ofrecer *"¿2 o 3 dormitorios?"* como botones en vez de texto.

**El agente ya genera el payload interactivo en el formato de Cloud API.** Lo único que falta
está **del lado del CRM**: (A) aceptar y reenviar ese payload a WhatsApp, y (B) avisarle al
agente qué opción tocó el cliente.

> Alcance: aplica cuando el gateway activo de la conversación es **WhatsApp Cloud API**. Kommo
> salesbot no maneja interactivos nativos (ver §6, degradación).

---

## 2. Resumen de lo que hay que implementar

| # | Dirección | Cambio | Endpoint / Evento |
|---|---|---|---|
| A | Agente → CRM → WhatsApp | Aceptar un campo `interactive` en el body y reenviarlo a Cloud API | `POST /api/v1/whatsapp/conversations/{id}/messages` |
| B | WhatsApp → CRM → Agente | Al tocar un botón/lista, mandar el evento con `type: "interactive"` (id + título) | webhook `message.received` |

---

## 3. (A) Salida — enviar botones/listas

### 3.1 Cambio en el endpoint de respuesta

`POST /api/v1/whatsapp/conversations/{id}/messages` debe aceptar, **además de `text`**, un
campo opcional `interactive`. Reglas:

- Si viene `interactive` → el CRM arma el mensaje interactivo de Cloud API y lo envía.
- Si viene solo `text` → comportamiento actual (sin cambios).
- `text` pasa a ser **opcional cuando** viene `interactive` (hoy es obligatorio).

Campos del body:

| Campo | Req | Descripción |
|---|---|---|
| `text` | condicional | Texto plano. Requerido si **no** hay `interactive`. |
| `interactive` | no | Objeto interactivo en **formato WhatsApp Cloud API** (ver 3.2). |
| `reply_to_id` | no | igual que hoy |

El objeto `interactive` que manda el agente es **exactamente** el que espera la Cloud API en
`messages` con `type: "interactive"`. El CRM solo tiene que envolverlo:

```jsonc
// Lo que el CRM debe enviar a WhatsApp Cloud API:
{
  "messaging_product": "whatsapp",
  "to": "<telefono del contacto>",
  "type": "interactive",
  "interactive": { /* ← tal cual llega en el body del agente */ }
}
```

### 3.2 Formato del objeto `interactive`

**Botones (hasta 3 opciones):**

```json
{
  "type": "button",
  "body": { "text": "¿Está buscando un departamento de 2 o 3 dormitorios?" },
  "action": {
    "buttons": [
      { "type": "reply", "reply": { "id": "btn_1", "title": "De 2 dormitorios" } },
      { "type": "reply", "reply": { "id": "btn_2", "title": "De 3 dormitorios" } }
    ]
  }
}
```

**Lista (4 a 10 opciones):**

```json
{
  "type": "list",
  "header": { "type": "text", "text": "Seleccione una opción" },
  "body": { "text": "Estas son las opciones disponibles:" },
  "action": {
    "button": "Ver opciones",
    "sections": [
      {
        "title": "Opciones disponibles",
        "rows": [
          { "id": "opt_1", "title": "Opción A" },
          { "id": "opt_2", "title": "Opción B" }
        ]
      }
    ]
  }
}
```

### 3.3 Límites de WhatsApp (validar o dejar pasar)

| Elemento | Límite |
|---|---|
| Botones (`type: button`) | máx **3** |
| Título de botón (`reply.title`) | máx **20** caracteres |
| Filas de lista (`type: list`) | máx **10** en total |
| Título de fila (`row.title`) | máx **24** caracteres |
| `body.text` | máx **1024** caracteres |
| Formato en títulos | **sin Markdown** (Meta rechaza `*_~` con error #131009) |

El agente ya respeta estos límites al construir el payload. Si el CRM quiere validar por las
dudas, mejor; pero no debería reescribir los títulos.

### 3.4 Ejemplo curl (agente → CRM)

```bash
curl -X POST https://imprimir.sofopolis.com/api/v1/whatsapp/conversations/5/messages \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer 12|AbCdEf...' \
  -d '{
    "interactive": {
      "type": "button",
      "body": { "text": "¿Está buscando un departamento de 2 o 3 dormitorios?" },
      "action": {
        "buttons": [
          { "type": "reply", "reply": { "id": "btn_1", "title": "De 2 dormitorios" } },
          { "type": "reply", "reply": { "id": "btn_2", "title": "De 3 dormitorios" } }
        ]
      }
    }
  }'
```

Respuesta OK esperada (igual que hoy):

```json
{ "message": { "id": 165, "status": "queued", "sender": "ia" } }
```

Aplican los mismos errores que hoy (§5 de `integracion-gateway-whatsapp.md`), incluido el
**422 fuera de la ventana de 24h** (los interactivos también son mensajes de sesión).

---

## 4. (B) Entrada — recibir la opción que el cliente tocó

Cuando el cliente **toca un botón o elige una fila**, WhatsApp Cloud API manda al CRM un webhook
con un objeto interactivo. Ejemplo de lo que llega desde Meta:

```json
"interactive": {
  "type": "button_reply",
  "button_reply": { "id": "btn_1", "title": "De 2 dormitorios" }
}
```

El CRM debe **reenviar esa selección al agente** en el evento `message.received`, con este
formato (extiende el evento actual):

```jsonc
{
  "event": "message.received",
  "conversation_id": 5,
  "gateway": "cloud_api",
  "ai_enabled": true,
  "contact": { "phone": "+59176616013", "name": "Alejandro", "person_id": 7, "lead_id": null },
  "message": {
    "id": 170,
    "type": "interactive",              // ← nuevo tipo
    "text": "De 2 dormitorios",         // ← el TÍTULO elegido, en texto (importante, ver nota)
    "interactive": {                    // ← id + título de la opción
      "button_reply": { "id": "btn_1", "title": "De 2 dormitorios" }
      // para listas: "list_reply": { "id": "opt_1", "title": "..." }
    },
    "timestamp": "2026-07-16T07:12:00-04:00"
  },
  "history": [ /* ... */ ],
  "window": { "open": true, "expires_at": "..." },
  "reply": { "method": "POST", "url": "https://imprimir.sofopolis.com/api/v1/whatsapp/conversations/5/messages" }
}
```

> **Nota clave:** poné el **título elegido también en `message.text`**. Así el agente entiende la
> respuesta aunque solo lea texto, y no hay que cambiar nada más en su lógica. El objeto
> `interactive` (con el `id`) es un extra por si en el futuro se enruta por `id`.

El agente ya entiende este formato: parsea `message.type == "interactive"` con
`interactive.button_reply` / `interactive.list_reply` (`{ id, title }`).

---

## 5. Compatibilidad hacia atrás

- Si el agente **no** manda `interactive`, todo sigue igual (texto).
- Conversaciones y clientes viejos no se ven afectados.
- El agente hace **fallback a texto** automáticamente cuando el canal no soporta interactivos
  (ver §6), así que nunca queda un mensaje sin enviar.

---

## 6. Degradación por gateway (Kommo vs Cloud API)

Los interactivos nativos son de **WhatsApp Cloud API**. Si la conversación va por **Kommo
salesbot** (u otro gateway sin botones), el CRM debería, ante un body con `interactive`:

- **Opción recomendada:** convertirlo a texto — el `body.text` seguido de las opciones numeradas
  (`1. ...`, `2. ...`) y enviarlo como mensaje de texto normal.
- Alternativa: responder un error claro (p. ej. `422` con `message: "interactive_not_supported"`)
  para que el agente reintente en texto.

Decidan una y avísennos cuál, así el agente se alinea.

---

## 7. Checklist para el CRM

- [ ] `POST .../messages` acepta `interactive` (y `text` pasa a opcional si viene `interactive`).
- [ ] El CRM reenvía el objeto `interactive` a Cloud API envuelto en `type: "interactive"`.
- [ ] (Opcional) Validación de límites de Meta (§3.3).
- [ ] Al tocar botón/lista, el evento `message.received` llega con `type: "interactive"`,
      `message.text` = título elegido, y `message.interactive.{button_reply|list_reply}`.
- [ ] Degradación definida para gateways sin interactivos (§6).
- [ ] Probado extremo a extremo: agente manda botones → al cliente le llegan → toca uno → el
      agente recibe la selección y continúa.

---

## 8. Referencia — formato oficial de Meta

- Enviar interactivos (botones/listas): documentación de WhatsApp Cloud API,
  *Interactive Messages* (`type: "interactive"`, objetos `button` y `list`).
- Webhook de respuesta interactiva: *Received Messages → interactive → button_reply / list_reply*.
