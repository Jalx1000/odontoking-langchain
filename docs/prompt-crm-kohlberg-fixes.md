# Prompt para el Claude del proyecto CRM (Krayin / Laravel) — Kohlberg

> Pegá todo el contenido de abajo en el Claude del repositorio del **CRM** (`kohlberg.sofopolis.com`).
> Son 3 arreglos del lado del CRM que el agente de IA de WhatsApp (Sofía) necesita. No cambian la
> lógica de negocio.

---

## Contexto

Esta API Krayin/Laravel es consumida por un **agente de IA de WhatsApp** (Sofía, Club del Vino
Kohlberg) que se autentica con un **token Sanctum dedicado**. En producción aparecen 3 problemas del
lado del CRM. Resolvé los tres; mostrame el diff de cada uno y confirmá con una prueba.

---

## 1) Rate limiting: HTTP 429 `Too Many Attempts`

**Síntoma (real, de producción):**

```json
{ "message": "Too Many Attempts.",
  "exception": "Illuminate\\Http\\Exceptions\\ThrottleRequestsException",
  "file": ".../Middleware/ThrottleRequests.php", "line": 232 }
```

Ocurre al **enviar la respuesta del agente**:

`POST /api/v1/whatsapp/conversations/{id}/messages`

y también en los endpoints que el agente consulta con el mismo token en cada turno:
`GET /api/v1/products`, `GET /api/productos/por-ciudad-sucursal`,
`GET /api/v1/settings/warehouses`, `GET/POST/PUT /api/v1/leads*`.

**Causa probable:** todos comparten el bucket de throttle por defecto de Laravel
(`throttle:api` ≈ 60/min) y una ráfaga de un turno lo satura. El agente ya bajó su lado (menos
llamadas por pedido, sin reintentos en 429, catálogo cacheado), pero el límite sigue siendo bajo.

**Qué necesito:**
1. Localizá dónde se aplica el throttle: `bootstrap/app.php` (Laravel 11) o
   `app/Providers/RouteServiceProvider.php` → `RateLimiter::for('api', ...)`, y los grupos
   `middleware('throttle:...')` en `routes/api.php` y en las rutas del agente WhatsApp.
2. Creá un limiter dedicado para el **usuario/token del agente** (identificado por su usuario Sanctum),
   ej. `RateLimiter::for('agent-api', ...)`, con un límite generoso (**300–600/min**) y **configurable
   por env** (`AGENT_API_RATE_LIMIT`, default alto). Aplicalo a `/api/v1/*`, `/api/productos/*` y
   especialmente a `POST /api/v1/whatsapp/conversations/{id}/messages`. Dejá el resto de la API con su
   límite normal.
3. Asegurá que las respuestas 429 devuelvan el header **`Retry-After`** correcto.

---

## 2) Deduplicación de persona por teléfono

**Síntoma (real):** un MISMO número (`+59176616013`, `conversation_id` 1) generó **dos** `person_id`
distintos — `669` y luego `691`. Los pedidos (leads) quedaron repartidos entre ambas personas, así que
el historial del cliente **se parte** y `get_pedidos` nunca ve todos sus pedidos.

**Qué necesito:** al recibir un mensaje entrante / al abrir la conversación, si ya existe un `person`
con ese `contact_number` (teléfono), **reutilizarlo** en lugar de crear uno nuevo. Buscá dónde el flujo
de WhatsApp resuelve/crea el contacto y hacelo idempotente por teléfono (normalizando el número —
sin `+`, sin espacios — antes de comparar).

---

## 3) Mensajes `interactive` (botones / listas)

**Síntoma (real):** cuando el cliente toca un botón, el evento llega como
`message.type = "interactive"` con `text = null`, y el agente lo descarta
(`crm_unsupported_message`).

**Qué necesito:** al construir el evento `message.received`, si el mensaje entrante es `interactive`
(button reply / list reply), **extraer el título/texto de la opción seleccionada** y ponerlo en el
campo `text` del evento, para que el agente lo procese como texto normal. (Alternativa: mapear el
`id`/`payload` del botón a su etiqueta.)

---

## Cómo verificar

- **#1:** simulá ~12 requests seguidas con el token del agente y confirmá que ya no devuelve 429;
  indicá qué env var controla el límite y su default.
- **#2:** mandá dos mensajes desde el mismo teléfono y confirmá que se resuelve **un solo** `person_id`.
- **#3:** mandá un `interactive` (button reply) y confirmá que el evento `message.received` sale con
  `text` = etiqueta del botón.
