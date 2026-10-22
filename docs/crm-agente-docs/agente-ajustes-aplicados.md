# Agente de IA de Sensia — cómo se aplicaron los ajustes del CRM

> Para el equipo del CRM. Resume **qué hace ahora el agente** por cada punto del documento
> `agente-ia-integracion-crm.md` (22/10/2026), qué **espera del evento** y qué queda **pendiente**.
> Fecha de este reporte: 22/10/2026.

**Regla general de robustez:** todo se implementó con **fallback seguro**. Si el CRM todavía no
manda alguno de los bloques nuevos (`responder`, `catalogo`, `asignacion.ciudades`, `lead`,
`history`, `message.location.text`), el agente sigue funcionando con su comportamiento anterior y
**no se rompe**. A medida que el CRM empiece a mandar cada bloque, el agente adopta el nuevo
comportamiento automáticamente.

---

## Resumen por sección

| § | Tema | Estado del agente | Qué espera del CRM |
|---|---|---|---|
| §4 | `responder` / `message.sent` / `sender` | ✅ Aplicado | `responder` (bool) + `message.sent` + `sender` en `history` |
| §5 | Catálogo y precios | ✅ Aplicado | `catalogo.por_zona` con `product_id`/`nombre`/`precio` |
| §6 | Cliente conocido y pedidos pendientes | ✅ Ya estaba | `cliente` y `pedidos_pendientes` |
| §8 | Crear/actualizar pedido, ciudad, asignación | ✅ Aplicado | `lead.is_empty`, `contact.lead_id`, `asignacion.ciudades` |
| §9 | Ubicación del cliente | ✅ Aplicado | `message.location.text` (`"<lat>,<lng> \| <ref>"`) |
| §11 | Recordatorio de recompra | ✅ Aplicado | `recompra` + botones Mañana / En 3 días / En 5 días |
| §13 | Memoria y manejo de errores | ✅ Aplicado | `history` (fuente de verdad) + códigos HTTP correctos |

---

## §4 — `responder`, `message.sent` y `sender`

**Qué hace el agente ahora:**

- Antes de cualquier cosa mira **`responder`**: `true` → contesta; `false` → **no contesta, no llama
  herramientas, no registra**, solo lo toma como contexto.
- Los eventos **`message.sent`** (mensajes del asesor) se procesan como **contexto** y nunca se
  contestan.
- Lee el campo **`sender`** de cada mensaje del `history` (`contact` / `ia` / `human` / `sistema`) para
  reconstruir la conversación. Un mensaje de un **asesor (`human`) vale como dicho por Sensia**.

**Fallback:** si el evento no trae `responder`, el agente decide con el `handoff.open` de antes.

**Qué necesitamos del CRM:** mandar `responder` en cada `message.received`, y `sender` en cada ítem de
`history`.

---

## §5 — Catálogo y precios (`catalogo`)

**Qué hace el agente ahora:**

- Toma **productos y precios del bloque `catalogo`** del evento como **fuente de verdad**: ofrece solo
  lo que figura en `por_zona[<zona del cliente>]`, con ese precio y ese `product_id`.
- Precios mostrados en **pesos argentinos** con formato `$7.500`.
- **Nunca niega cobertura ni productos** a una zona de `zonas_con_cobertura` (San Salvador de Jujuy,
  Barrio Alto Comedero, Palpalá).
- No muestra precios sin conocer la zona.

**Fallback:** si no viene `catalogo`, usa la herramienta `get_promos` (que ya filtra por zona).

**Qué necesitamos del CRM:** mandar `catalogo` con `zonas_con_cobertura`, `zona_del_cliente` y
`por_zona` (cada producto con `product_id`, `nombre`, `precio` y, si aplica, `precio_promocion`).

---

## §6 — Cliente conocido y pedidos pendientes

**Qué hace el agente ahora (ya estaba y sigue vigente):**

- Si `cliente.es_conocido` es `true`: **no rehace el alta**, confirma el domicilio en una línea y pide
  solo la cantidad. Usa `cliente.ultimo_pedido` para ofrecer "lo de siempre".
- Si `pedidos_pendientes` no está vacío: **pregunta** si suma al pedido pendiente (`PUT`, lista
  completa) o lo deja aparte (`POST`), en vez de duplicar.

**Qué necesitamos del CRM:** seguir mandando `cliente` y `pedidos_pendientes` (ya llegan).

---

## §8 — Crear/actualizar pedido, ciudad y asignación

**Qué hace el agente ahora:**

- **POST vs PUT por `lead.is_empty`:** `lead` ausente o `is_empty:false` → **POST** (pedido nuevo);
  `is_empty:true` → **PUT** sobre `lead.id` (llena el lead vacío que abrió el CRM). Esto evita los
  pedidos duplicados por la misma venta.
- **Ciudad y etapas desde `asignacion.ciudades`:** toma el `pipeline_id` y los ids de etapa
  (`prospectos` / `pedidos-confirmado` / `pedido-entregado` / `pedidos-cancelado`) **del evento**, no
  hardcodeados. Siempre manda `lead_pipeline_stage_id` + el atributo `Ciudad`.
- **Asignación de distribuidor:** el agente **ya NO manda `user_id`** ni elige distribuidor. La
  asignación la hace el CRM (cliente conocido → su distribuidor; nuevo/sin zona → el equipo /
  Huascar). *(Esto revierte el pedido previo de hardcodear `user_id=1596`.)*

**Fallback:** si no viene `lead`, usa su heurística anterior para no duplicar; si no viene
`asignacion.ciudades`, usa el mapa de etapas interno.

**Qué necesitamos del CRM:** mandar `lead` (con `is_empty`) y `contact.lead_id`, y `asignacion.ciudades`
con sus `etapas`. Confirmado que el CRM **ignora** cualquier `user_id` que mandara el agente.

---

## §9 — Ubicación del cliente

**Qué hace el agente ahora:**

- Si el mensaje trae `message.location`, usa **`message.location.text`** (`"<lat>,<lng> | <ref>"`) y lo
  guarda tal cual en `ubicacion_lead`. Funciona tanto con el **pin 📍** (`type:location`) como con un
  **link de Google Maps** (`type:text`): en el link **no** usa `message.text` (es la frase del cliente
  con la URL adentro).
- La ubicación **nunca** va en el `description`.

**Fallback:** si `location` trae coordenadas sueltas pero no `text`, arma `"lat,lng"`. Si no hay
coordenadas, le pide al cliente la dirección por texto (no se queda mudo).

**Qué necesitamos del CRM:** incluir `message.location.text` cuando el cliente comparte ubicación.

---

## §11 — Recordatorio de recompra

**Qué hace el agente ahora:**

- Cuando llega el bloque `recompra`, entiende que el cliente responde al recordatorio. Las tres
  opciones **"Mañana" / "En 3 días" / "En 5 días"** (botón o texto) se interpretan como **"sí" para esa
  fecha**: toma el pedido y **agrega la fecha a la descripción** (`"… | entregar: en 3 días"`).
- Informa siempre la decisión con una sola llamada: `si` (quiere pedir), `no` (más adelante / no
  responde), `baja` (no quiere más recordatorios). Ante la duda entre `no` y `baja`, elige `baja`.
- Si el cliente pide volver a recibirlos, reactiva (solo si lo pide él).

**Qué necesitamos del CRM:** seguir mandando `recompra` con su `responder.url` (ya llega).

---

## §13 — Memoria y manejo de errores *(del incidente real conv 7710)*

**Qué hace el agente ahora:**

- **El `history` del evento es la fuente de verdad del contexto.** En cada turno el agente reconstruye
  la conversación desde el `history` (mapeando por `sender`) y **resetea su memoria interna**; así un
  turno que el cliente **nunca recibió** (p. ej. un `404` al contestar) **ya no puede envenenar** los
  turnos siguientes. La respuesta del turno sale solo de lo generado en ese turno, nunca de un mensaje
  viejo del historial.
- **Nunca concluye cosas del negocio por un error.** Si una herramienta falla o no trae datos, el
  agente **no** dice "no hay productos" ni "no hay cobertura": se disculpa, reintenta o deriva.
- **Códigos HTTP al contestar:** `404` / `409` / `422` son **terminales** → se loguean (con
  `conversation_id`) y **no se reintentan** (evita apilar reintentos y respuestas repetidas). `5xx` y
  `429` sí se reintentan con espera.

**Pendiente operativo (requiere el servicio desplegado):** **borrar la memoria del número
`5493875163381`**, que quedó con el contexto equivocado del incidente, vía
`DELETE /api/v1/<slug>/conversations/<wa_id>` (header `X-Admin-Key`).

---

## Qué necesitamos del CRM para activar todo al 100%

El agente ya consume estos bloques; a medida que el CRM los mande, se activa el comportamiento nuevo
(hasta entonces, fallback):

- [ ] `responder` (bool) en cada `message.received`, y eventos `message.sent` del asesor.
- [ ] `sender` en cada ítem de `history` (`contact` / `ia` / `human` / `sistema`).
- [ ] `catalogo` con `por_zona` (`product_id`, `nombre`, `precio`, `precio_promocion`).
- [ ] `asignacion.ciudades` con `pipeline_id` y `etapas`.
- [ ] `lead` con `is_empty`, y `contact.lead_id` en cada evento.
- [ ] `message.location.text` en pines 📍 y links de Maps.
- [ ] Confirmar que el CRM **ignora** el `user_id` (el agente ya no lo manda).

---

## Notas

- **No se probó contra el CRM en vivo** desde nuestro lado (no teníamos un evento real a mano);
  conviene una conversación de prueba después de desplegar.
- Todos los cambios son del **agente de Sensia** (rama `sensia`). No afectan a los otros tenants
  (Kohlberg, Imprimir, etc.), salvo una mejora compartida del gateway (los errores terminales
  `404`/`409`/`422` dejan de reintentarse), que es correcta para todos.
