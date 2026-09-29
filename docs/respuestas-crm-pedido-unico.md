# Respuestas del equipo del agente — plan pedido único y derivación

> Responde a `preguntas-para-el-equipo-del-agente.md`. Estado del lado del agente
> al 2026-09-22 (branch `kohlberg-v2`, pendiente de redeploy).

## Resumen en una línea

El duplicado (lead fantasma vacío + lead de venta) **ya lo resolvimos**, pero por
un mecanismo distinto al que asume el plan: **no leemos `lead.is_open`**; hacemos
nuestro propio GET del lead y **rellenamos el lead vacío del CRM en el primer
pedido**. Hay una **divergencia de diseño a acordar** (pedidos separados) y **dos
huecos reales** (422 sin reintento, corrección post-registro).

---

## 1. ¿Leen `lead.is_open`? — HOY NO, pero el duplicado igual se corrige

- **No parseamos el bloque `lead`** del payload todavía. En su lugar hacemos
  `GET /api/v1/leads/{contact.lead_id}` y decidimos con dos señales: la etapa y
  si el lead **ya tiene productos**.
- **Primer pedido:** el lead que el CRM abre está vacío → lo **rellenamos con PUT**
  (lo movemos a la ciudad real + productos + valor). Así **no queda fantasma** →
  ataca los 81 duplicados.
- Podemos migrar a leer `lead.is_open`/`lead.products` del payload (más confiable
  que nuestro GET, y nos ahorra una llamada). **Lo haremos** — pero con la
  salvedad del punto siguiente.

**🔴 Divergencia de diseño a acordar:** nuestro producto pide **pedidos
separados**. Un segundo pedido en la misma conversación **abre un lead nuevo
(POST)**, aunque el primero siga `is_open=true` (Confirmado). El plan del CRM
(`is_open=true → PUT`) **fusionaría** los dos en un solo lead. No podemos seguir
`is_open` a ciegas para la decisión PUT/POST sin romper esto. Pregunta: **¿aceptan
que un contacto tenga varios leads (uno por pedido) en la misma conversación?**
Si sí, `is_open` nos sirve solo para "rellenar el lead vacío", no para fusionar.

## 2. ¿El PUT manda la lista COMPLETA? — SÍ, porque solo hacemos PUT sobre leads VACÍOS

- Hoy **solo hacemos PUT cuando el lead no tiene productos** (el vacío del CRM).
  No hay lista previa que perder → la trampa de "PUT reemplaza" **no nos afecta**.
- Si un lead ya tiene productos, **no lo tocamos con PUT**: va por POST (pedido
  nuevo). Nunca mandamos "solo lo nuevo" sobre un lead con contenido.
- El día que adoptemos "PUT sobre un lead con productos" (p. ej. para corrección,
  ver Q6), mandaremos `lead.products` + lo nuevo, lista completa. Por eso el
  `lead.products` del payload nos sirve.

## 3. ¿Resuelven `product_id` contra el catálogo? — SÍ (por `get_promos`)

- El agente elige `product_id` y `name` **del catálogo real** (`get_promos`, que
  cachea `GET /api/v1/products`), no de texto libre. El **precio sale del catálogo**
  (`_product_price`), nunca de un cálculo del LLM. `lead_value = Σ price×qty` con
  esos precios.
- No usamos `/api/public/products?q=`; usamos el catálogo completo cacheado, pero
  el efecto es el mismo: id real + precio de catálogo.
- Riesgo residual: si el LLM eligiera un id que no está en el catálogo cacheado,
  el precio saldría 0. Lo mitiga que el catálogo es la única fuente que ve.

## 4. ¿Qué hacen con un 422? — HOY NO reintentamos (hueco a cerrar)

- `registrar_pedido` captura el error HTTP y devuelve `{"lead_id": null,
  "error": "api_422"}`. **No reintenta.** El agente le diría al cliente que hubo
  un problema; el pedido de ese turno se pierde si no vuelve a intentar.
- Con su nueva validación (422 si `lead_value>0` sin `products[]`), esto casi no
  debería dispararse porque **siempre mandamos `products`** (ver Q2/Q3). Aun así,
  vamos a manejar el 422 para reintentar/derivar en vez de perder el pedido.

## 5. ¿Mandan `lead_pipeline_stage_id` siempre? — SÍ

- `_build_lead_body` **siempre** setea `lead_pipeline_stage_id` (de `_city_stages`,
  con default a la etapa "No atendido" de la ciudad). Nunca va ausente.

## 6. ¿Cómo corrigen un pedido? — HOY genera duplicado si la corrección es DESPUÉS de registrar (hueco a cerrar)

- **Dentro del flujo de confirmación** (antes de registrar): el cliente corrige en
  el paso "¿confirmás?" → se registra una sola vez. Sin duplicado.
- **Después de registrar** (el caso #1772/#1773, 31 s después): hoy el segundo
  `registrar_pedido` ve el lead con productos → **POST** → **duplicado**. Este es
  el hueco. Para cerrarlo vamos a **recordar el `lead_id` recién creado en la
  conversación** y, si el cliente corrige, hacer **PUT sobre ese mismo id** con la
  lista completa — distinguiendo "corrección" de "pedido nuevo" por lo que dice el
  cliente (eso el CRM no puede saberlo, nosotros sí).

---

## Sobre el cambio ya activo (derivación)

Perfecto. Que una conversación derivada **ya no nos llegue** es lo que queríamos —
elimina el `409` con el que chocábamos al responder por encima del asesor.
Confirmamos que del lado del agente **no hay que hacer nada** para eso, y que la
conversación nos vuelve sola al resolverse/expirar. (Igual dejaremos el manejo de
`409` como red de seguridad por si llega un evento en carrera.)

---

## Lo que vamos a implementar de nuestro lado (derivado de estas preguntas)

1. Leer `lead.is_open`/`lead.products`/`lead.lead_value` del payload (reemplaza
   nuestro GET) — **tras acordar la Q1** (pedidos separados vs. fusión).
2. Manejar `422` en `registrar_pedido`: reintentar/derivar, no perder el pedido.
3. Corrección post-registro = PUT sobre el `lead_id` de la conversación (lista
   completa), no POST.
4. (Interno) revisar el colapso de stage ids del commit `d6a61a8`: hoy todas las
   etapas de una ciudad comparten id, así que la distinción entregado/cancelado
   se apoya solo en "tiene productos". Confirmar que es intencional.
