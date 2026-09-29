  # Reporte al equipo CRM — IMPRIMIR (agente WhatsApp)

  **Fecha:** 2026-09-14
  **De:** equipo del agente de IA (Valentina)
  **Para:** equipo CRM `imprimir.sofopolis.com`
  **Base:** `https://imprimir.sofopolis.com`
  **Auth:** `Authorization: Bearer <TOKEN del usuario Agente>` (el mismo de siempre)

  > Resumen: el agente ya envía todo correctamente, pero hay 3 bugs del lado del CRM
  > que bloquean el flujo (el #1 deja las cotizaciones vacías), 1 tema de ruteo de
  > Messenger, y 3 confirmaciones de negocio. Prioridad: **#1 > #2 > #3**.

  ---

  ## 🔴 1. Las cotizaciones se crean SIN líneas de producto  (BLOQUEANTE)

  **Endpoint:** `POST /api/v1/productos/conversations/{id}/cotizacion`

  **Síntoma:** el quote se crea con el encabezado (subject) correcto pero con **0 líneas
  de producto** (`items: []`). El asesor recibe una cotización sin el producto.

  **Evidencia (verificado en prod):**

  ```bash
  curl -sS "https://imprimir.sofopolis.com/api/v1/quotes/437" \
    -H "Authorization: Bearer $TOKEN" -H 'Accept: application/json' | jq '{subject, items}'
  ```

  Devuelve:

  ```json
  {
    "subject": "Bolsas Magia Verde — 50 L · HORECA · Interior",
    "items": []
  }
  ```

  El `subject` demuestra que **el CRM SÍ recibió** sku + Tamaño + Canal + Zona que mandó el
  agente (por eso pudo armar ese título). El body que envía el agente es:

  ```json
  { "sku": "CM_00002",
    "cantidad": 20,
    "marca": "Magia Verde",
    "criterios": { "Tamaño": "50 L", "Canal": "HORECA", "Zona": "Interior" },
    "nit": "…", "empresa": "…" }
  ```

  **Qué necesitamos:** que el endpoint, además de crear el subject, **inserte la línea en
  `quote_items`** (product_id resuelto por sku + variante, quantity, y el price desde la
  misma lista que usa `precio_producto`). Sin eso, cada cotización llega vacía.

  ---

  ## 🔴 2. Cotizaciones que no se pueden leer  (HTTP 500)

  **Evidencia:**

  ```bash
  curl -sS "https://imprimir.sofopolis.com/api/v1/quotes/137" \
    -H "Authorization: Bearer $TOKEN" -H 'Accept: application/json'
  ```

  Devuelve:

  ```json
  {
    "message": "Attempt to read property \"id\" on null",
    "exception": "ErrorException",
    "file": ".../Illuminate/Http/Resources/DelegatesToResource.php"
  }
  ```

  **Qué necesitamos:** revisar por qué algunas quotes revientan al leerse — probablemente
  una relación nula (persona / producto / lead sin resolver) que el Resource intenta leer.

  ---

  ## 🔴 3. El handoff "requested/pooled" NO apaga la IA

  **Síntoma:** cuando el agente deriva a un asesor, el handoff queda en `requested` / `pooled`
  pero **`ai_enabled` sigue en `true`** en los eventos siguientes. El CRM le **sigue enviando
  mensajes al agente**, que entonces arranca un segundo flujo (visto en prod: derivó por
  "sin_datos" y siguió cotizando otra cosa hasta crear un segundo quote).

  **Qué necesitamos:** que un handoff **`requested`** (no solo `assigned`) ponga
  **`ai_enabled = false`** o **`handoff.open = true`** en el `message.received`, para que el
  CRM deje de rutear al agente apenas se pide la derivación.

  > Del lado del agente ya pusimos un guard defensivo (si el hilo ya derivó, no responde),
  > pero lo correcto es que el CRM corte el ruteo en origen.

  ---

  ## 🟡 4. Conversaciones de Messenger caen en el id-space de WhatsApp (404)

  **Síntoma:** un mensaje entrante de **Messenger** (ej. `conversation_id: 2263`) hace que el
  agente falle al responder:

  ```
  POST /api/v1/whatsapp/conversations/2263/messages  → 404
  { "message": "No query results for model [Webkul\\Whatsapp\\Models\\Conversation] 2263" }
  ```

  Lo mismo con `/api/v1/whatsapp/conversations/2263/interactive` (los menús de botones).

  **Contexto:** este agente es **por botones → WhatsApp-only**. En Messenger, `mostrar_opciones`
  404ea y además `contact.person_id` / `contact.lead_id` vienen en `null`.

  **Qué necesitamos (decisión de producto):** o el CRM **no rutea Messenger** a este agente,
  o envía un `reply.url` válido para el canal Messenger.

  > El agente ya fue endurecido para que un 404 no tumbe el turno ni dispare alertas por
  > mail, pero la conversación de Messenger igualmente no se puede atender con este flujo.

  ---

  ## 🟡 5. Confirmaciones de negocio (para validar la implementación del agente)

  1. **Canal HOGAR = "góndola" (`cotiza: false`).** Para cumplir la regla de negocio
    "bolsas para la casa con ≥10 paquetes SÍ se cotizan", el agente pide el precio y crea
    la cotización con **`Canal = TRADICIONAL`** (HOGAR no es cotizable).
    **¿Confirman que ese es el precio correcto** para venta directa a domicilio por volumen,
    o debe usarse otro canal / cargar un precio propio para HOGAR≥10?

  2. **Custom attribute `marca`.** El agente ya envía `"marca"` en `crear_cotizacion` con
    valores **`"Magia Verde"`** (bolsas CM_00002) o **`"Imprimir"`** (resto del catálogo).
    **¿El endpoint lo acepta y lo mapea al custom attribute de la cotización? ¿El nombre del
    campo y esos valores exactos son los correctos?**

  3. **Mínimos y máximos.** Los mínimos vienen bien en `precio_producto`
    (`minimo_texto`: "10 packs", "30 cajas", etc.). **No existe campo de máximo** en la API.
    **¿Confirman que no hay tope de pedido**, o hay que cargar máximos por producto?

  ---

  ## Cómo reproducir (precio + cotización)

  ```bash
  TOKEN='...'
  BASE='https://imprimir.sofopolis.com/api/v1'

  # Precio con cantidad (esto ya funciona OK, es la referencia)
  curl -sS -X POST "$BASE/productos/catalogo/CM_00002/precio" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -H 'Accept: application/json' \
    -d '{"criterios":{"Tamaño":"50 L","Canal":"TRADICIONAL","Zona":"Santa Cruz"},"cantidad":20}' | jq

  # Crear cotización (BUG #1: crea el quote pero items queda vacío)
  curl -sS -X POST "$BASE/productos/conversations/123/cotizacion" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -H 'Accept: application/json' \
    -d '{"sku":"CM_00002","cantidad":20,"marca":"Magia Verde",
        "criterios":{"Tamaño":"50 L","Canal":"TRADICIONAL","Zona":"Santa Cruz"},
        "nit":"1234567019","empresa":"Prueba SRL"}' | jq

  # Verificar que la cotización quedó con items (hoy da items: [])
  curl -sS "$BASE/quotes/<quote_id>" \
    -H "Authorization: Bearer $TOKEN" -H 'Accept: application/json' | jq '{subject, items}'
  ```
