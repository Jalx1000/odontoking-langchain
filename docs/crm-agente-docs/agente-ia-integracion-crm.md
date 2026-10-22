# Agente de IA de Sensia ↔ CRM — documentación completa de integración

Documento para el equipo que construye y mantiene el agente (servicio en Python con
LangChain). Está escrito para leerse **desde cero**: qué es cada pieza, qué manda el
CRM, qué tiene que hacer el agente y con qué endpoints.

Reemplaza a los documentos anteriores (`agente-ia-asignacion-y-clientes-conocidos.md`,
`agente-ia-productos-y-ubicacion.md`, `agente-ia-recordatorio-de-recompra.md`). Si algo
de esos documentos contradice a este, **vale este**. El prompt completo está en
`docs/prompt-sensia.md`.

Última actualización: 22/10/2026.

---

## Índice

- [Agente de IA de Sensia ↔ CRM — documentación completa de integración](#agente-de-ia-de-sensia--crm--documentación-completa-de-integración)
  - [Índice](#índice)
  - [1. Cómo funciona, en un dibujo](#1-cómo-funciona-en-un-dibujo)
  - [2. Datos de conexión](#2-datos-de-conexión)
  - [3. El evento que manda el CRM](#3-el-evento-que-manda-el-crm)
    - [Qué mensajes llegan](#qué-mensajes-llegan)
    - [Ejemplo completo](#ejemplo-completo)
    - [Campos que pueden venir en `null` (y es normal)](#campos-que-pueden-venir-en-null-y-es-normal)
  - [4. Regla 1 — `responder`: cuándo contestar y cuándo no](#4-regla-1--responder-cuándo-contestar-y-cuándo-no)
  - [5. Regla 2 — Productos y precios: el bloque `catalogo`](#5-regla-2--productos-y-precios-el-bloque-catalogo)
    - [Cómo usarlo](#cómo-usarlo)
    - [Productos inactivos](#productos-inactivos)
    - [`get_promos` / `GET /api/v1/products` (respaldo)](#get_promos--get-apiv1products-respaldo)
  - [6. Regla 3 — Cliente conocido y pedidos pendientes](#6-regla-3--cliente-conocido-y-pedidos-pendientes)
    - [`cliente`: no repreguntar lo que ya sabemos](#cliente-no-repreguntar-lo-que-ya-sabemos)
    - [`pedidos_pendientes`: no duplicar pedidos](#pedidos_pendientes-no-duplicar-pedidos)
  - [7. Contestar al cliente](#7-contestar-al-cliente)
    - [Respuestas](#respuestas)
    - [Botones y listas tocables](#botones-y-listas-tocables)
  - [8. Crear y actualizar pedidos](#8-crear-y-actualizar-pedidos)
    - [¿Crear (POST) o completar (PUT)?](#crear-post-o-completar-put)
    - [La ciudad y la etapa del pedido (leer antes de crear o actualizar)](#la-ciudad-y-la-etapa-del-pedido-leer-antes-de-crear-o-actualizar)
    - [Crear: `POST /api/v1/leads`](#crear-post-apiv1leads)
    - [Completar o corregir: `PUT /api/v1/leads/{id}`](#completar-o-corregir-put-apiv1leadsid)
    - [Solo atributos: `PUT /api/v1/leads/attributes/edit/{id}`](#solo-atributos-put-apiv1leadsattributeseditid)
    - [Confirmar el pedido](#confirmar-el-pedido)
    - [Etapas](#etapas)
    - [El `description`](#el-description)
  - [9. Ubicación del cliente](#9-ubicación-del-cliente)
    - [Si el cliente manda un pin 📍 o un link de Google Maps](#si-el-cliente-manda-un-pin--o-un-link-de-google-maps)
    - [Si el cliente dicta la dirección](#si-el-cliente-dicta-la-dirección)
  - [10. Derivar a una persona](#10-derivar-a-una-persona)
  - [11. Recordatorio de recompra](#11-recordatorio-de-recompra)
  - [12. Endpoints de consulta](#12-endpoints-de-consulta)
  - [13. Memoria del agente y manejo de errores](#13-memoria-del-agente-y-manejo-de-errores)
  - [14. Checklist de implementación](#14-checklist-de-implementación)
  - [15. Referencia rápida de endpoints y errores](#15-referencia-rápida-de-endpoints-y-errores)
  - [16. Glosario del negocio](#16-glosario-del-negocio)

---

## 1. Cómo funciona, en un dibujo

```
 Cliente (WhatsApp)
        │  escribe
        ▼
 Meta (WhatsApp Cloud API)
        │  webhook
        ▼
 CRM Sensia  ── guarda el mensaje, el contacto y el pedido
        │
        │  POST <webhook del agente>   (evento con TODO el contexto)
        ▼
 Agente (Python / LangChain)
        │  decide qué contestar; registra pedidos
        │
        │  POST /api/v1/whatsapp/conversations/{id}/messages   (respuesta)
        │  PUT/POST /api/v1/leads...                           (pedido)
        ▼
 CRM Sensia  ── manda la respuesta a Meta, y Meta al cliente
```

Puntos clave:

- **El agente nunca habla directo con Meta.** Toda respuesta pasa por el CRM, que la
  guarda, la muestra en la bandeja y la envía.
- **El CRM es la fuente de verdad**: productos, precios, zonas, clientes y pedidos salen
  del CRM. El agente no tiene que recordar ni inventar nada de eso: viene en cada evento.
- **Un asesor humano puede tomar la conversación en cualquier momento.** El agente tiene
  que respetarlo (ver [Regla 1](#4-regla-1--responder-cuándo-contestar-y-cuándo-no)).

---

## 2. Datos de conexión

| | |
|---|---|
| **Base URL del CRM** | `https://sensia.sofopolis.com` |
| **Autenticación del agente → CRM** | `Authorization: Bearer <token del CRM>` (token personal de Sanctum). Lo genera un administrador del CRM. **No va en el código**: variable de entorno. |
| **Webhook del agente** | La URL a la que el CRM manda los eventos. Se configura en el CRM (`WHATSAPP_AGENT_WEBHOOK_URL`). |
| **Autenticación CRM → agente** | El CRM manda `Authorization: Bearer <WHATSAPP_AGENT_TOKEN>` en cada evento. El agente debería rechazar los eventos que no lo traigan. |
| **Límite de pedidos a la API** | 600 por minuto por token. Un turno del cliente son varias llamadas; el límite está pensado para que la respuesta nunca quede afuera. |
| **Moneda** | Pesos argentinos (`$`, punto de miles, sin decimales: `$7.500`). **No** bolivianos (`Bs`): eso es herencia de otra instalación. |
| **Zona horaria** | `America/Argentina/Jujuy` (UTC-3). Las fechas de la API vienen en ISO 8601 con offset. |

---

## 3. El evento que manda el CRM

Por cada mensaje de la conversación, el CRM hace un `POST` al webhook del agente con un
JSON. **Todo lo que el agente necesita saber está acá**; las herramientas de consulta son
de respaldo.

### Qué mensajes llegan

| `event` | Cuándo | `responder` |
|---|---|---|
| `message.received` | El cliente escribió y el agente está **prendido** en esa conversación. | `true` → es tu turno |
| `message.received` | El cliente escribió y el agente está **apagado** (un asesor atiende). | `false` → solo contexto |
| `message.sent` | Escribió un **asesor** desde el CRM. | `false` → solo contexto |

No llegan las reacciones del cliente (👍): no son un turno de conversación.

### Ejemplo completo

```jsonc
{
  "event": "message.received",
  "responder": true,                    // ← mirar SIEMPRE antes de contestar (§4)
  "conversation_id": 7710,
  "gateway": "cloud_api",
  "ai_enabled": true,

  "contact": {
    "phone": "+5493875163381",
    "name": "Eze",
    "person_id": 13052,                 // null si todavía no hay ficha
    "lead_id": 10077                    // el pedido de esta conversación, o null
  },

  "message": {
    "id": 4842,
    "type": "text",                     // text | interactive | location | image | audio | ...
    "text": "Palpalá",                  // SIEMPRE el texto legible, también si tocó un botón
    "interactive": null,                // si tocó un botón o una lista: el id de la opción
    "location": null                    // si mandó un pin o un link de Maps (§9)
  },

  "history": [                          // los últimos 15 mensajes, del más viejo al más nuevo
    { "role": "user",      "sender": "contact", "type": "text", "content": "Hola" },
    { "role": "assistant", "sender": "ia",      "type": "text", "content": "¡Hola! 👋 ..." },
    { "role": "assistant", "sender": "human",   "type": "text", "content": "La bomba sale 21500" }
  ],

  "window": {                           // ventana de 24 h de WhatsApp (§7)
    "open": true,
    "expires_at": "2026-10-23T14:33:42-03:00"
  },

  "lead": {                             // el pedido de esta conversación, o null (§8)
    "id": 10077,
    "is_empty": true,                   // ← decide PUT (true) o POST (false)
    "is_open": true,
    "stage": "Prospectos",
    "lead_value": 0,
    "products": []
  },

  "cliente": {                          // lo que ya sabemos del cliente, o null (§6)
    "es_conocido": true,
    "ciudad": "Palpalá",
    "distribuidor": "Chaile",
    "domicilio": { "direccion": "Belgrano 100", "referencia": "Portón verde", "lat": -24.25, "lng": -65.20 },
    "datos_completos": true,
    "bidones_en_su_casa": 2,
    "ultimo_pedido": { "id": 9800, "fecha": "2026-10-10", "etapa": "Pedido entregado", "productos": [ ... ] }
  },

  "pedidos_pendientes": [               // pedidos de esta persona sin entregar (§6)
    { "id": 9990, "etapa": "Pedidos confirmado", "fecha": "2026-10-21", "total": 15000, "productos": [ ... ] }
  ],

  "asignacion": {
    "nota": "NO mandes user_id ni elijas distribuidor: el CRM lo asigna solo ...",
    "zonas": [ { "pipeline_id": 1, "ciudad": "Palpalá", "user_id": 1817, "distribuidor": "Chaile" } ],
    "ciudades": [                       // cada ciudad con los ids de sus etapas (§8)
      { "pipeline_id": 1, "ciudad": "Palpalá",
        "etapas": { "prospectos": 1, "pedidos-confirmado": 2, "pedido-entregado": 5, "pedidos-cancelado": 6 } }
    ]
  },

  "catalogo": {                         // productos y precios vigentes por zona (§5)
    "zonas_con_cobertura": ["Barrio Alto Comedero", "Palpalá", "San Salvador de Jujuy"],
    "zona_del_cliente": "Palpalá",
    "por_zona": {
      "Palpalá": [
        { "product_id": 37, "nombre": "Botellón de agua purificada 20 L", "precio": 7500,  "precio_promocion": null, "descripcion": "Agua pura y fresca..." },
        { "product_id": 35, "nombre": "Bomba Eléctrica y Bidón",          "precio": 21500, "precio_promocion": null, "descripcion": "La bomba eléctrica..." }
      ],
      "San Salvador de Jujuy": [
        { "product_id": 32,  "nombre": "Botellón de agua purificada 20 L", "precio": 8000,  "precio_promocion": null, "descripcion": "..." },
        { "product_id": 117, "nombre": "Bomba Eléctrica y Bidón",          "precio": 22000, "precio_promocion": null, "descripcion": "..." }
      ],
      "Barrio Alto Comedero": [ ... ]
    },
    "nota": "Productos y precios vigentes por zona. ..."
  },

  "recompra": null,                     // solo si el cliente contesta un recordatorio (§11)

  "reply": {                            // dónde contestar (§7)
    "method": "POST",
    "url": "https://sensia.sofopolis.com/api/v1/whatsapp/conversations/7710/messages"
  }
}
```

### Campos que pueden venir en `null` (y es normal)

| Campo | Viene `null` cuando |
|---|---|
| `contact.person_id`, `cliente` | La conversación todavía no está ligada a un contacto (primer mensaje de alguien nuevo). |
| `contact.lead_id`, `lead` | La conversación no tiene un pedido abierto. |
| `catalogo.zona_del_cliente` | El CRM todavía no sabe la zona. |
| `recompra` | No hay un recordatorio de recompra en curso. |
| `catalogo` | El módulo de distribución no está instalado (no pasa en Sensia). Usar `get_promos` de respaldo. |

El agente tiene que funcionar igual con cualquiera de estos en `null`.

---

## 4. Regla 1 — `responder`: cuándo contestar y cuándo no

**Antes de hacer cualquier cosa con un evento, mirar `responder`.**

- `responder: true` → es un turno del agente: contestar y, si corresponde, registrar el pedido.
- `responder: false` → **no contestar, no llamar herramientas, no registrar nada.** Solo
  guardar el mensaje como contexto de la conversación.

¿Por qué llegan mensajes que no hay que contestar? Para que el agente **no pierda el hilo**.
Cuando un asesor atiende la conversación (agente apagado), lo que hablan el cliente y el
asesor le llega igual. Así, cuando vuelven a prender el agente, sabe qué se dijo y no
repregunta ni contradice al asesor.

En el `history`, cada mensaje dice quién lo escribió en `sender`:

| `sender` | Quién |
|---|---|
| `contact` | El cliente |
| `ia` | El agente |
| `human` | Un asesor de Sensia |
| `sistema` | Un mensaje automático del CRM (recordatorios, pregunta de ciudad) |

Lo que dijo un asesor (`human`) **vale como dicho por Sensia**: si dio un precio o acordó
algo, el agente lo respeta.

> **Red de seguridad:** si el agente intenta contestar con el agente apagado, el CRM le
> devuelve `409` y no manda nada al cliente. Pero eso es una protección, no la forma de
> funcionar: el agente tiene que cortar antes, al ver `responder: false`.

---

## 5. Regla 2 — Productos y precios: el bloque `catalogo`

**Todos los productos y precios salen de `catalogo`, que viene en cada evento.**

- `zonas_con_cobertura`: las zonas donde Sensia entrega. **Una zona de esta lista siempre
  tiene cobertura y productos.** El agente nunca puede decir "no llegamos" ni "no hay
  productos" para una zona de la lista.
- `zona_del_cliente`: la zona que el CRM ya conoce (puede venir vacía).
- `por_zona["<zona>"]`: los productos **activos** de esa zona, con su precio.

### Cómo usarlo

1. **La zona del cliente** es la que dijo en la conversación; si no la dijo,
   `zona_del_cliente`; si no hay ninguna, se le pregunta.
2. **Si la zona está en `zonas_con_cobertura`**: se ofrecen SOLO los productos de
   `por_zona[<zona>]`, con ESE precio, y ese `product_id` es el que va en el pedido.
3. **Si la zona no está en la lista**: fuera de cobertura (se toma el interés, sin pedido).
4. **Nunca dar un precio sin saber la zona**: el mismo producto vale distinto según la
   zona. El botellón de 20 L vale **$7.500** en Palpalá y Barrio Alto Comedero y **$8.000**
   en San Salvador de Jujuy. Son dos productos distintos (ids 37 y 32) con el mismo nombre:
   **elegir por nombre da el precio equivocado.**
5. El total del pedido es la suma de los productos. **El envío no se cobra ni se menciona
   su costo** (ni "gratis", ni "sin cargo", ni un precio): solo se dice que hay envío a
   domicilio.

### Productos inactivos

Un producto dado de baja **no aparece** en `catalogo` ni en las listas de la API. No hay
que filtrar nada del lado del agente.

### `get_promos` / `GET /api/v1/products` (respaldo)

Solo si un evento llegara sin `catalogo`. La lista de productos de la API trae, además de
los campos estándar:

| Campo | Para qué |
|---|---|
| `ciudad_producto_sucursal` | Ids de los depósitos donde se vende |
| `ciudad_producto_sucursal_labels` | Los nombres de esos depósitos (= la zona) |
| `products` | Estado (`true` activo). Las listas ya no traen inactivos. |
| `precio_promocion` | Precio con descuento, si tiene |
| `combos_productos_cantidad` | Texto con JSON: qué incluye un combo (hay que decodificarlo dos veces) |

`price` llega como texto con 4 decimales (`"7500.0000"`). El depósito "Todas" (id 10)
significa que el producto se vende en todas las zonas.

---

## 6. Regla 3 — Cliente conocido y pedidos pendientes

### `cliente`: no repreguntar lo que ya sabemos

Si `cliente.es_conocido` es `true`, la persona ya compró antes. **No se rehace el alta.**

- No volver a pedir nombre, zona ni domicilio si vienen en el bloque.
- Confirmar el domicilio en una línea: *"¿Te llevamos a Belgrano 100 (portón verde) como
  siempre? ¿Cuántos botellones te dejamos?"*. Recién si dice que se mudó, pedir el nuevo.
- Si `cliente.datos_completos` es `false`, falta el domicilio: hay que pedirlo aunque sea
  un cliente viejo.
- `cliente.ultimo_pedido` sirve para ofrecer "lo mismo de siempre".
- `cliente.bidones_en_su_casa`: envases que tiene sin devolver; si son varios, se le puede
  recordar que los deje listos.

### `pedidos_pendientes`: no duplicar pedidos

Si la lista **no** está vacía y el cliente pide algo, **no abrir otro pedido sin
preguntar**:

> "Ya tenés un pedido pendiente de 2 botellones. ¿Querés que le sume estos, o lo dejamos
> como un pedido aparte?"

- "Sumalo" → corregir ese pedido (`PUT`) con la lista COMPLETA: lo que tenía **más** lo nuevo.
- "Aparte" → pedido nuevo (`POST`).

---

## 7. Contestar al cliente

```http
POST /api/v1/whatsapp/conversations/{conversation_id}/messages
Authorization: Bearer <token>
Content-Type: application/json

{ "text": "¡Hola! 👋 Gracias por escribir a Sensia 💧 ...", "reply_to_id": null }
```

La URL exacta viene en `reply.url` del evento.

### Respuestas

| Código | Significa | Qué hacer |
|---|---|---|
| `200` | Mensaje encolado para enviar. Devuelve `message.id`. | Nada más. |
| `409` + `handoff` | La conversación está **derivada a un asesor**. | Dejar de contestar en esa conversación. |
| `409` (agente apagado) | El agente está **apagado** en esa conversación. | No contestar (no debería pasar si se respeta `responder`). |
| `422` | **Ventana de 24 h cerrada**: WhatsApp solo deja escribir con una plantilla aprobada. | No reintentar; esperar a que el cliente escriba. |
| `401` | Token inválido. | Revisar la configuración. |
| `429` | Límite de pedidos superado. | Reintentar con espera (`Retry-After`). |

### Botones y listas tocables

Si el mensaje ofrece un conjunto **cerrado** de opciones, numeradas con el marcador `N.-)`
una por línea, el CRM las convierte en **botones** (hasta 3) o en una **lista** de WhatsApp
(hasta 10):

```
¿En qué zona te encontrás?

1.-) San Salvador de Jujuy
2.-) Barrio Alto Comedero
3.-) Palpalá
4.-) Otra zona
```

Cuando el cliente toca una opción, el siguiente evento trae `message.text` con el texto de
la opción y `message.interactive` con su id. No usar `N.-)` para enumerar información
(productos, pedidos): eso va como texto normal.

---

## 8. Crear y actualizar pedidos

Un **pedido** en el CRM es un *lead*. La API es la REST API estándar de Krayin.

### ¿Crear (POST) o completar (PUT)?

**Lo decide `lead.is_empty` del evento, no `lead.is_open`:**

| `lead` en el evento | Acción |
|---|---|
| `null` | `POST` → pedido nuevo |
| `is_empty: true` | `PUT` sobre `lead.id` → es el pedido que el CRM abrió al llegar el primer mensaje; hay que llenarlo |
| `is_empty: false` | `POST` → pedido nuevo (aunque el anterior siga abierto) |

Hacer `POST` cuando hay un pedido vacío abierto deja **dos pedidos para la misma venta**
(pasó 81 veces antes de esta regla).

### La ciudad y la etapa del pedido (leer antes de crear o actualizar)

La ciudad **es** el pipeline del pedido, y cada ciudad tiene sus propias etapas con ids
distintos. Los ids salen de `asignacion.ciudades` del evento:

```json
{ "pipeline_id": 1, "ciudad": "Palpalá",
  "etapas": { "prospectos": 1, "pedidos-confirmado": 2, "pedido-entregado": 5, "pedidos-cancelado": 6 } }
```

**No hardcodear ids**: cambian entre instalaciones.

🔴 **Trampa de la API:** si un `POST` o `PUT` de `/api/v1/leads` llega **sin**
`lead_pipeline_stage_id`, la API manda el pedido a la ciudad por defecto. Por eso:

- **Siempre mandar `lead_pipeline_stage_id`** con la etapa de la ciudad del pedido
  (`asignacion.ciudades[<ciudad>].etapas["prospectos"]` para uno nuevo, o la etapa en la
  que esté). La API deduce la ciudad de la etapa.
- **Y mandar el atributo `Ciudad`** con el `pipeline_id` (con mayúscula: así se llama el
  campo). Es lo que deja la ciudad del pedido y la del cliente sincronizadas.

Además:

- **Siempre mandar la ciudad.** Un pedido sin ciudad cae en "Sin ciudad", no tiene
  distribuidor y no lo ve nadie hasta que alguien lo corrige a mano.
- **No mandar `user_id` ni elegir distribuidor.** El CRM asigna solo: un cliente vuelve a
  su distribuidor; uno nuevo lo reparte el equipo. Un `user_id` que mande el agente se
  ignora.

### Crear: `POST /api/v1/leads`

```jsonc
{
  "title": "Pedido botellones – Eze",            // obligatorio
  "description": "2× Botellón 20 L | Envío a domicilio | zona: Palpalá",
  "lead_value": 15000,                            // obligatorio: total del pedido
  "lead_source_id": 6,                            // obligatorio: 6 = WhatsApp
  "lead_type_id": 1,                              // obligatorio: 1 = Nuevo cliente, 2 = Cliente antiguo
  "lead_pipeline_stage_id": 1,                    // etapa "prospectos" de la ciudad (asignacion.ciudades)
  "Ciudad": 1,                                    // pipeline_id de la ciudad
  "person": { "id": 13052 },                      // contact.person_id del evento
  "products": {
    "product_0": { "product_id": 37, "name": "Botellón de agua purificada 20 L", "quantity": 2, "price": 7500 }
  },
  "ubicacion_lead": "-24.255002,-65.208238 | Portón verde"   // ver §9
}
```

### Completar o corregir: `PUT /api/v1/leads/{id}`

El mismo cuerpo, **con `lead_pipeline_stage_id`** (ver la trampa de arriba).

🔴 **La lista `products` REEMPLAZA a la anterior**: lo que no se mande se borra. Para
corregir o sumar, mandar la lista **completa** (lo que ya tenía, que viene en
`lead.products` o `pedidos_pendientes`, más lo nuevo), cada fila con clave `product_N`.

### Solo atributos: `PUT /api/v1/leads/attributes/edit/{id}`

Para cambiar campos sueltos sin mandar el pedido entero y sin el riesgo de la etapa:

```json
{ "Ciudad": 1 }
{ "ubicacion_lead": "-24.193000,-65.297000 | Portón verde" }
```

Cambiar `Ciudad` por acá **mueve el pedido a esa ciudad** (y su etapa a la equivalente) y
dispara la asignación del distribuidor. Es la forma recomendada de registrar la zona
apenas el cliente la dice.

### Confirmar el pedido

Solo después de que el cliente confirme explícitamente:

```http
PUT /api/v1/leads/stage/edit/{id}
{ "lead_pipeline_stage_id": <asignacion.ciudades[<ciudad>].etapas["pedidos-confirmado"]> }
```

### Etapas

| `code` | Nombre | Significa |
|---|---|---|
| `prospectos` | Prospectos | Pedido abierto, todavía sin confirmar (= "no atendido" en el tablero) |
| `pedidos-confirmado` | Pedidos confirmado | El cliente confirmó |
| `pedido-entregado` | Pedido entregado | Entregado (cerrado) |
| `pedidos-cancelado` | Pedidos cancelado | Cancelado (cerrado) |

### El `description`

Un resumen corto del pedido: `"<cant>× <producto> | Envío a domicilio | zona: <ciudad>"`.
🔴 **No poner la ubicación en el `description`**: el distribuidor la ve como "referencia" y
recibe un muro de texto. La ubicación va en `ubicacion_lead`.

---

## 9. Ubicación del cliente

### Si el cliente manda un pin 📍 o un link de Google Maps

El evento trae `message.location`. Las coordenadas vienen en tres lugares a la vez:

```jsonc
"message": {
  "type": "location",                         // o "text" si pegó un link de Maps
  "text": "-24.255002,-65.208238",
  "location": {
    "lat": -24.2550022, "lng": -65.2082377,
    "name": "Casa", "address": "Belgrano 450",
    "url": "https://www.google.com/maps?q=-24.2550022,-65.2082377",
    "text": "-24.255002,-65.208238 | Casa, Belgrano 450"   // ← esto va a ubicacion_lead
  },
  "latitude": -24.2550022, "longitude": -65.2082377
}
```

**La regla, una sola:** si el mensaje trae `location`, guardar `location.text` en
`ubicacion_lead`. Si no lo trae, pedir la dirección por texto. **No usar `message.text`**:
en un link es la frase del cliente con la URL adentro.

El pin de WhatsApp además se guarda solo como domicilio del cliente.

### Si el cliente dicta la dirección

`ubicacion_lead` con el formato `<lat>,<lng> | <dirección y referencia>`, o solo el texto
si no hay coordenadas:

```json
{ "ubicacion_lead": "-24.193000,-65.297000 | Portón verde, timbre del fondo" }
{ "ubicacion_lead": "Av. San Martín 1234, portón verde al lado del kiosco" }
```

- Coordenadas con **al menos 3 decimales**.
- **Dentro de Jujuy** (lat entre -25.0 y -21.5, lng entre -67.5 y -62.0); el CRM descarta
  lo que caiga afuera.
- **La referencia es lo más importante**: color del portón, piso, timbre, entre qué calles.

---

## 10. Derivar a una persona

Cuando el cliente pide hablar con alguien, está molesto, hay un reclamo o la consulta
excede al agente:

1. Avisarle al cliente en un mensaje breve (*"Te comunico con un asesor del equipo 💧..."*).
2. Llamar:

```http
POST /api/v1/whatsapp/conversations/{id}/handoff
{ "reason": "Reclamo por un pedido no entregado", "ciudad": "Palpalá" }
```

`ciudad` es opcional (texto o id de pipeline): ayuda a mandarla al equipo correcto.

3. **No volver a escribir en esa conversación.** Mientras la derivación esté abierta, el
   CRM rechaza las respuestas del agente con `409`. Los mensajes siguen llegando con
   `responder: false`.

Las derivaciones se cierran solas a las 12 horas (configurable en el CRM) y la
conversación vuelve al agente. Un asesor también puede cerrarla antes.

---

## 11. Recordatorio de recompra

Después de cada entrega, el CRM le manda al cliente una plantilla preguntando si quiere
agendar el próximo pedido, con tres botones: **"Mañana"**, **"En 3 días"**, **"En 5 días"**.

Cuando el cliente contesta, el evento trae el bloque `recompra` (en cualquier otra
conversación viene `null`):

```jsonc
"recompra": {
  "esperando_respuesta": true,
  "intento": 1,                       // 1, 2 o 3: no repetir la misma frase
  "datos_completos": true,            // true = NO pedir el domicilio, confirmarlo
  "domicilio": { "direccion": "Belgrano 100", "referencia": "Portón verde", "lat": -24.18, "lng": -65.29 },
  "responder": {
    "method": "POST",
    "url": "https://sensia.sofopolis.com/api/v1/whatsapp/conversations/1/recompra",
    "respuestas": { "si": "...", "no": "...", "baja": "..." }
  }
}
```

- Tocar uno de los tres botones (o escribirlo) es un **sí**, para esa fecha: tomar el
  pedido (cuántos botellones) y poner la fecha en el `description`
  (`"... | entregar: en 3 días"`).
- **Informar siempre qué contestó**, con una sola llamada:

```http
POST /api/v1/whatsapp/conversations/{id}/recompra
{ "respuesta": "si" }      // quiere pedir ahora
{ "respuesta": "no" }      // ahora no, quizás más adelante (o no respondió sobre el recordatorio)
{ "respuesta": "baja" }    // no quiere recibir más estos mensajes
```

- 🔴 **`baja`** es cuando pide dejar de recibir estos mensajes ("no me manden más",
  "desuscribir", "basta", "sacame de la lista"). "Ahora no", "la semana que viene" o
  "todavía me queda" es **`no`**. **Ante la duda, `baja`**: seguirle escribiendo a quien
  pidió que no genera reportes de spam, y los reportes pueden hacer perder la línea de
  WhatsApp del negocio.
- Si después se arrepiente y lo pide él:
  `POST /api/v1/whatsapp/conversations/{id}/recompra/reactivar`. Nunca por cuenta propia.

---

## 12. Endpoints de consulta

Todo esto ya viene en el evento; estos endpoints son para casos puntuales.

| Endpoint | Devuelve |
|---|---|
| `GET /api/v1/whatsapp/conversations/{id}/messages` | `contact` + `history` de la conversación |
| `GET /api/personas/por-telefono?telefono=<número>` | La ficha del contacto con sus atributos (ciudad, etc.). Tolera el número con o sin `+` y código de país. |
| `GET /api/pedidos/por-telefono?telefono=<número>` | El historial de pedidos de ese teléfono. Para "¿qué pedí la otra vez?" o "repetir el pedido". |
| `GET /api/v1/products` | Productos activos (respaldo de `catalogo`, ver §5) |
| `GET /api/v1/leads/{id}` | Un pedido |

---

## 13. Memoria del agente y manejo de errores

Esta sección sale de un caso real (conversación 7710, 21/10/2026): el cliente escribió
"Soy de Palpalá" y el agente terminó contestando **"no tenemos productos en Palpalá"** y
después **"fuera de cobertura"**, aunque Palpalá tiene cobertura y productos.

Qué pasó:

1. Durante unos minutos, las respuestas del agente al CRM devolvieron `404` (un cambio de
   servidor).
2. El agente **guardó esos turnos fallidos en su memoria** del número (`thread_id` =
   teléfono).
3. En el turno siguiente contestó **desde esa memoria, sin consultar el catálogo**, y
   "dedujo" que no había servicio. Lo repitió horas después.

Lo que el agente tiene que hacer:

- **No guardar en la memoria los turnos que fallaron** (cualquier `4xx`/`5xx` del CRM al
  contestar o al registrar). Si la respuesta no le llegó al cliente, para la conversación
  ese turno no existió.
- **Ante un error de una herramienta, no concluir nada sobre el negocio** ("no hay
  productos", "no hay cobertura"). Volver a `catalogo` del evento y seguir.
- **El `history` del evento es la fuente de verdad** de lo que se habló: si la memoria
  propia del agente y el `history` no coinciden, vale el `history`.
- **Reintentos:** `429` y `5xx` se pueden reintentar con espera. `409` y `422` **no**: el
  CRM está diciendo que el agente no debe escribir ahí.
- Loguear `status` y cuerpo de cada error del CRM, con `conversation_id`.

Acción puntual: borrar la memoria del número **5493875163381**, que quedó con el
contexto equivocado.

---

## 14. Checklist de implementación

**Código del agente**

- [ ] Verificar el `Authorization: Bearer` de cada evento entrante.
- [ ] Mirar `responder` antes de todo. `false` → guardar como contexto, no contestar, no
      llamar herramientas.
- [ ] Manejar `event: "message.sent"` (mensajes del asesor) como contexto.
- [ ] Pasarle al modelo, en cada turno: `message`, `history` (con `sender`), `cliente`,
      `pedidos_pendientes`, `lead`, `catalogo` y `recompra`.
- [ ] No guardar en memoria los turnos que fallaron (§13).
- [ ] No reintentar `409`/`422`; reintentar con espera `429`/`5xx`.
- [ ] Pedido: `POST` si `lead` es `null` o `is_empty: false`; `PUT` sobre `lead.id` si
      `is_empty: true`.
- [ ] Al corregir un pedido, mandar la lista de productos **completa**.
- [ ] Siempre mandar la ciudad: `lead_pipeline_stage_id` de esa ciudad + atributo `Ciudad`
      (ids de `asignacion.ciudades`); nunca `user_id`.
- [ ] Registrar la zona apenas el cliente la dice: `PUT /leads/attributes/edit/{id}` con `Ciudad`.
- [ ] Ubicación en `ubicacion_lead` (de `message.location.text` si viene), nunca en el
      `description`.
- [ ] Recompra: informar `si` / `no` / `baja` a la URL de `recompra.responder.url`.

**Prompt** (`docs/prompt-sensia.md`, completo y listo para pegar)

- [ ] Productos y precios desde `catalogo`; `get_promos` solo de respaldo.
- [ ] Nunca negar cobertura o productos a una zona de `zonas_con_cobertura`.
- [ ] Precio: si no se sabe la zona, preguntarla primero.
- [ ] Envío: decir que hay envío a domicilio, sin hablar de su costo.
- [ ] `responder: false` y el `sender` de cada mensaje del historial.
- [ ] Cliente conocido, pedidos pendientes, recordatorio de recompra.

---

## 15. Referencia rápida de endpoints y errores

| Método | Endpoint | Para |
|---|---|---|
| `POST` | `<webhook del agente>` | **CRM → agente**: cada mensaje (§3) |
| `POST` | `/api/v1/whatsapp/conversations/{id}/messages` | Contestar al cliente (§7) |
| `GET` | `/api/v1/whatsapp/conversations/{id}/messages` | Historial de la conversación |
| `POST` | `/api/v1/whatsapp/conversations/{id}/handoff` | Derivar a una persona (§10) |
| `POST` | `/api/v1/whatsapp/conversations/{id}/recompra` | Respuesta al recordatorio (§11) |
| `POST` | `/api/v1/whatsapp/conversations/{id}/recompra/reactivar` | Volver a recibir recordatorios |
| `POST` | `/api/v1/leads` | Crear un pedido (§8) |
| `PUT` | `/api/v1/leads/{id}` | Completar o corregir un pedido (§8) |
| `PUT` | `/api/v1/leads/attributes/edit/{id}` | Cambiar campos sueltos del pedido |
| `PUT` | `/api/v1/leads/stage/edit/{id}` | Cambiar la etapa del pedido |
| `GET` | `/api/v1/leads/{id}` | Leer un pedido |
| `GET` | `/api/personas/por-telefono?telefono=` | Ficha del contacto |
| `GET` | `/api/pedidos/por-telefono?telefono=` | Pedidos de un teléfono |
| `GET` | `/api/v1/products` | Productos activos (respaldo) |

| Código | Significa |
|---|---|
| `200` | OK |
| `401` | Token inválido o ausente |
| `404` | La conversación o el pedido no existe |
| `409` | Conversación derivada a un asesor, o agente apagado: **no escribir** |
| `422` | Ventana de 24 h cerrada, o datos inválidos (el cuerpo dice cuál) |
| `429` | Demasiados pedidos: esperar y reintentar |

---

## 16. Glosario del negocio

| Término | Qué es |
|---|---|
| **Sensia** | Marca de agua purificada (Serranías del Zapla SRL), Jujuy, Argentina. |
| **Botellón / bidón** | Envase de agua de 20 L. Es el producto principal. |
| **Bomba eléctrica** | Accesorio para servir el agua del bidón. Se vende con un bidón. |
| **Zona / ciudad** | San Salvador de Jujuy, Barrio Alto Comedero o Palpalá. En el CRM, cada una es un *pipeline*. Decide el precio y quién reparte. |
| **Distribuidor** | La persona o empresa que reparte en una zona. Hay varios por zona; el CRM asigna cuál. |
| **Pedido / lead** | Una venta. Pasa por las etapas Prospectos → Confirmado → Entregado (o Cancelado). |
| **Derivación** | Cuando la conversación pasa de la IA a una persona del equipo. |
| **Ventana de 24 h** | Regla de WhatsApp: solo se puede escribir texto libre hasta 24 h después del último mensaje del cliente. |
| **Plantilla** | Mensaje pre-aprobado por Meta, el único que se puede mandar fuera de la ventana (recordatorios, bienvenida). |
| **Recompra** | El recordatorio automático para que el cliente pida de nuevo. |