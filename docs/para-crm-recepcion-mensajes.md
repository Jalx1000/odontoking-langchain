# Para el equipo del CRM — recepción de mensajes (agente Kohlberg)

> De: equipo del agente IA (Sofía / Kohlberg).
> Objetivo: que el agente pueda **recordar al cliente** y procesar bien lo que llega.
> Base: `https://kohlberg.sofopolis.com` · Auth: `Bearer <API_KEY>` (Sanctum).

Dividimos lo que **les corresponde a ustedes (CRM)** de lo que **hacemos nosotros (agente)**, para no pisarnos.

---

## 1. Lo que necesitamos del CRM (recepción)

### 1.1 🔴 Identidad estable del contacto por teléfono — lo más importante
Necesitamos que **un mismo número de teléfono sea SIEMPRE el mismo `person_id`** entre conversaciones.

- Si el CRM crea una persona nueva por cada conversación, los datos que el agente guarda (edad, ciudad) quedan en una persona, y la próxima vez llega otro `person_id` → el agente **nunca recuerda al cliente** y le vuelve a pedir todo.
- **Pregunta concreta:** ¿el CRM deduplica la persona por teléfono (mismo número → misma ficha), o crea una nueva por conversación? Si crea duplicados, ese es el bug de recepción a corregir de su lado.

### 1.2 Campos obligatorios en cada `message.received`
Que siempre vengan (hoy vienen — solo confirmar que no falten nunca):
`conversation_id`, `contact.phone`, `contact.person_id`, `contact.lead_id`.

### 1.3 Taps de botón/lista con `text` poblado
Según su contrato del 10-sep, el tap llega como:

```json
{ "message": { "type": "interactive", "text": "Santa Cruz", "selection": { "id": "opt:1", "title": "Santa Cruz" } } }
```

El agente rutea leyendo **`message.text`**. Necesitamos que en los interactivos `text` **venga siempre poblado** con el título. Si algún tap llega sin `text`, el agente lo descarta. Confirmar que siempre lo mandan.

### 1.4 Guardar la ciudad en la ficha
Su doc de derivación dice que, al derivar, guardan la `ciudad` que mandamos en la ficha del cliente. Confirmar:
- ¿También la guardan si la mandamos por el **flujo normal** (no solo en handoff)?
- Si no, la escribimos nosotros por API (`PUT /contacts/persons/{id}` con el atributo `cliente_ciudad`). Solo necesitamos que **acepten** ese atributo. → ver §2.

### 1.5 Throttle del token del agente (429)
Compartimos un solo bucket de Sanctum para todas las llamadas del agente → nos devuelve `429 Too Many Attempts` y se degrada la respuesta. El fix de fondo (subir o segmentar el límite para el token del agente) es de su lado. Nosotros ya mitigamos por el nuestro (cache de catálogo + no reintentar 429).

---

## 2. Lo que hacemos nosotros (agente) — NO es del CRM

- Leer el evento y extraer el texto (incluido el `text` del interactivo).
- Llamar a un endpoint de lectura al inicio para no re-preguntar datos ya conocidos.
- **Escribir** los custom attributes del contacto (`edad`, `cliente_ciudad`) vía `PUT /api/v1/contacts/persons/{id}`. El CRM solo tiene que **aceptarlos**; escribirlos es nuestro.
- Rutear por ciudad (pipeline / stage / asesor) y llenar `lead_value` y los productos del pedido.
- Armar los menús con el marcador `N.-)` en el texto.
- Manejar el `409` posterior a una derivación (soltar el turno, no reintentar).

---

## 3. Lo que NO nos corresponde (aunque ocurra en recepción)

| Tema | Responsable |
|---|---|
| Deduplicar personas / `person_id` estable por teléfono | **CRM** |
| Convertir el marcador `N.-)` en botones reales y mandar el `selection` | **CRM** |
| Resolver el asesor por ciudad y apagar el agente al derivar | **CRM** |
| Subir/segmentar el límite de throttle del token del agente | **CRM** |
| Escribir edad/ciudad en la ficha vía API, rutear, armar menús | Agente |

---

## 4. Resumen de una línea

> Confírmennos que **un mismo teléfono es siempre el mismo `person_id`** (no crear persona nueva por conversación), que los taps llegan como `interactive` con **`text` poblado**, y evalúen **subir el throttle del token del agente**. Lo demás (guardar edad/ciudad en la ficha vía API, rutear, armar menús) lo hacemos nosotros.
