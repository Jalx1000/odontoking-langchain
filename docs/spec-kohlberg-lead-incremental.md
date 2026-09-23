# Spec: el lead-pedido se llena en vivo con cada dato (Kohlberg)

> Estado: **aprobado para implementar** (decisiones del usuario 2026-09-23).
> Reemplaza el modelo "escribir todo al confirmar".

## Objetivo

Un **lead = un pedido** que evoluciona en vivo. El CRM crea un lead vacío al primer
mensaje (hoy nace en **Tarija / Daniel Escalante**, el default). Apenas el cliente
menciona **cada dato**, se **PUT** ese mismo lead para que quede registrado al
instante — no al final. Si el cliente abandona a mitad, el lead ya tiene lo dicho.

## Decisiones del usuario

- **Cadencia:** incremental total — PUT apenas llega CADA dato (ciudad, nombre, edad,
  cada producto). Acepta ~5-6 PUT/charla y el mayor riesgo de 429.
- **Un lead abierto por cliente:** se trabaja siempre sobre `contact.lead_id` (el #N
  que el CRM manda en cada evento). No se crea otro mientras ese siga abierto.
- **Ciudad:** al conocerla, mover el lead a la ciudad real (pipeline + asesor) y
  escribir el custom `Ciudad` (lead) + `cliente_ciudad` (persona). Esto saca el lead
  de Tarija/Daniel.
- **Persona + lead:** edad en `edad` (persona) y `edad_lead` (lead); nombre en la
  persona.

## Flujo

```
CRM crea lead vacío #N (Tarija/Daniel, No atendido) · metadata.lead_id=#N
  cliente dice CIUDAD  → PUT #N: pipeline+asesor=ciudad, Ciudad + cliente_ciudad
  cliente dice NOMBRE  → PUT #N: person.name
  cliente dice EDAD    → PUT #N: edad + edad_lead
  cliente elige VINO   → PUT #N: products (lista COMPLETA acumulada) + lead_value
  confirma             → PUT #N: lista final (queda en No atendido)
  cancela              → PUT #N: estado cancelado
```

## Mecanismo

- **Tool nuevo `actualizar_pedido(ciudad?, nombre?, edad?, product_id[]?, product_name[]?,
  cantidad_product[]?, es_cancelado?)`** que el agente llama en cada paso con lo que
  acaba de captar. Mantiene un **borrador por wa_id** (`_DRAFT_BY_WA`) y hace **1 PUT**
  sobre `contact.lead_id` con el body completo armado del borrador.
- **Productos:** el agente manda SIEMPRE la lista completa conocida (no solo lo nuevo),
  porque el PUT del CRM reemplaza. El borrador guarda la última lista.
- **Precios:** del catálogo cacheado (`_product_price`), nunca del LLM. `lead_value = Σ`.
- **Cold-seed:** si el borrador está vacío en esta réplica pero el lead ya existe, se
  hace 1 GET del lead para sembrar productos/ciudad y no pisar lo previo.
- **`registrar_pedido`** queda como el paso de **confirmar/cancelar** final, apuntando
  al mismo `contact.lead_id`; comparte `_build_lead_body` + `_upsert_lead`.
- Stage siempre "No atendido" (tabla `_CITY_STAGES` colapsada, intencional).

## Boundaries

- Siempre: 1 PUT por dato (no GET+PUT salvo cold-seed); lista de productos completa;
  precios del catálogo; best-effort (un fallo de PUT no corta la conversación).
- Nunca: mandar solo el producto nuevo (borra los previos); inventar precio/ciudad;
  reintentar un 429.

## Éxito

- Al decir "Tarija→no, Sucre", el lead pasa de Tarija/Daniel a Sucre/Silvia en el
  momento, con `Ciudad`+`cliente_ciudad` escritos.
- Si el cliente da ciudad+nombre+edad y se va sin comprar, el lead #N los tiene.
- Agregar un 2º vino re-PUT con los 2; el lead nunca pierde el 1º.
- Cancelar mueve el lead a cancelado.

## Riesgo

429 por más llamadas. Mitigación: solo PUT cuando de verdad llega un dato nuevo
(no en cada turno), y no reintentar 429. Si aparece, evaluamos bajar a "por hito".
