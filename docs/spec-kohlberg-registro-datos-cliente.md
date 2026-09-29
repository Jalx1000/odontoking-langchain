# Spec: Persistir datos y custom attributes del cliente en el contacto (Kohlberg)

> Estado: **borrador para revisión** (Fase 1 · Specify). No implementar hasta aprobación.

## Contexto (qué revela el log `1790093939360`)

Deploy `6de24d26`, 7 tools, corriendo `85bb533`. Verificado contra los 4 puntos pedidos:

| Punto | Estado | Evidencia en el log |
|---|---|---|
| Ruteo ciudad/dirección | ✅ funciona | SC→`pipeline=3 stage=11 user_id=9`; Sucre→`pipeline=9 stage=35 user_id=7` |
| Info sucursales/ciudades | ✅ funciona | `get_promos` city-id SC=1/Sucre=12; `get_sucursales encontrados=1` |
| `lead_value` del pedido | ✅ se llena | `build_lead_body lead_value=75/135/75`, ningún 0 |
| Registro de datos + custom attributes del cliente | ⚠️ parcial | `edad` sí se guarda (`edad_set 30/57/36`); **`cliente_ciudad` y `sucursal` nunca se escriben** |

**Gap único:** hoy `_set_person_edad` escribe SOLO `edad`. `get_persona` lee `cliente_ciudad` (select) y `sucursal` (text) pero siempre vuelven `valor: null` → un cliente recurrente jamás tiene la ciudad guardada → se le vuelve a pedir la ciudad cada vez.

## Objetivo

Que, al registrar un pedido, el contacto (persona Krayin) quede con sus custom attributes rellenos —`cliente_ciudad` y `sucursal`— además de `edad` (ya hecho), para que un cliente que vuelve no reingrese la ciudad y el CRM tenga el dato estructurado.

## Alcance

- **Incluye:** escribir `cliente_ciudad` y (opcional) `sucursal` en `PUT /api/v1/contacts/persons/{id}`, en el mismo punto donde hoy se escribe `edad` (dentro de `registrar_pedido`, best-effort).
- **No incluye:** cambiar el ruteo (ya funciona), el `lead_value` (ya funciona), ni el flujo conversacional del prompt.

## Supuestos (corregime si alguno está mal)

1. Se persiste en el momento de `registrar_pedido` (ahí tenemos `person_id` + ciudad confiable), igual patrón que `edad`. No se agrega una tool nueva.
2. `cliente_ciudad` es tipo **select** → Krayin espera el **id de la opción**, no el texto libre. Necesito el mapa opción→id (ver Preguntas abiertas).
3. `sucursal` es tipo **text** → se puede guardar el nombre de la sucursal de la ciudad (de `get_sucursales`), o dejarse fuera de este alcance.
4. El `PUT` reemplaza la persona → se hace `GET` primero y se re-envían `name`/`contact_numbers`/`emails`/`organization_id` (ya es el patrón de `_set_person_edad`).
5. Best-effort: si el update falla, NO rompe el registro del pedido (se loguea `warning`).
6. Escribir varios atributos en el mismo `PUT` (un solo request), no uno por atributo.

## Decisiones (respondidas por el usuario)

1. **Formato de `cliente_ciudad` (select):** RESUELTO — se manda tal cual (el CRM ya lo resuelve al enviarlo). No hace falta mapa de option-ids.
2. **`sucursal`:** NO se guarda. Fuera de alcance.
3. **Momento de persistencia:** apenas el cliente diga el dato — **ciudad, nombre y edad** se guardan en el contacto en cuanto se conocen, no solo al registrar el pedido.

## Mecanismo elegido

"Apenas lo dice" requiere un write disparado en el momento en que el agente capta el dato → **tool nuevo `guardar_datos_cliente(nombre?, edad?, ciudad?)`** que hace upsert al contacto (persona) con lo que reciba. El prompt instruye a llamarlo apenas se capten esos datos (ciudad en el paso 1, nombre/edad en el paso 2), antes de seguir.

- 1 `GET` + 1 `PUT` por llamada (patrón de `_set_person_edad`, ahora generalizado a `_set_person_attrs`).
- Idempotente y best-effort: si el CRM ya tiene el dato o el update falla, no rompe el flujo.
- `registrar_pedido` sigue guardando edad/ciudad como red de seguridad (por si el agente no llamó el tool).
- Mitigación 429: el tool solo escribe si hay al menos un dato nuevo; no se llama en cada turno.

## Éxito (condiciones testables)

- Tras registrar un pedido en ciudad X, un `GET /api/personas/por-telefono` del mismo número devuelve `cliente_ciudad.valor` == X (no null).
- En el siguiente mensaje de ese cliente, `get_persona` trae la ciudad y el agente **no** vuelve a pedirla (va directo a nombre/edad o promos).
- El registro del pedido sigue creando su lead con `lead_value` y ruteo correctos aunque el update del atributo falle (best-effort).
- Log nuevo `kohlberg_person_attrs_set` con `person_id`, `cliente_ciudad`, `sucursal`.

## Boundaries

- **Siempre:** best-effort (no romper el pedido); `GET`+merge antes del `PUT` para no borrar campos; `ruff`/`pyright` limpios.
- **Preguntar antes:** cambiar el mecanismo (tool nueva, o persistir en otro punto del flujo); tocar el ruteo o el prompt.
- **Nunca:** hardcodear un token; mandar el select en un formato no confirmado a producción sin probar; agregar llamadas extra que disparen el throttle 429 sin necesidad (mantener 1 GET + 1 PUT por pedido, como `edad`).

## Notas de implementación (previo a aprobación)

- Renombrar/extender `_set_person_edad` → `_set_person_attrs(client, person_id, *, edad, ciudad, sucursal, nombre, wa_id)` que arma el `body` con los atributos disponibles y hace 1 `GET` + 1 `PUT`. Mantiene el comportamiento actual de `edad`.
- Reemplazar la llamada en `registrar_pedido` (donde hoy se llama `_set_person_edad`) por la versión extendida, pasando `ciudad_del_cliente`.
- El mapa ciudad→id de opción del select va como constante (`_CITY_ATTR_OPTION_IDS`) una vez confirmado el formato (Pregunta 1).
