# Consigna para el agente — cotizaciones y derivación por ciudad

> **Para**: quien mantiene el servicio Python del agente.
> **Autenticación**: el mismo token Sanctum que ya usa para responder mensajes
> (`Authorization: Bearer 12|AbCdEf...`). No hace falta credencial nueva.
> **Base URL**: `https://imprimir.sofopolis.com`

---

## Lo que hay que entender antes de escribir código

**Las validaciones de `POST /api/v1/quotes` no están en el controlador.** El
endpoint usa `AttributeForm`, que arma las reglas leyendo la tabla `attributes`
para `entity_type = quotes`. Los campos obligatorios se configuran desde el CRM y
pueden cambiar sin que cambie la API. Lo de abajo es la foto de hoy, verificada
contra el código.

**El agente no crea el lead: lo mueve.** El CRM abre un lead automáticamente con
el primer mensaje del cliente, antes de que el agente pregunte nada. Ese lead
viene en `contact.lead_id` del evento de mensajería.

---

## 1. Derivación por ciudad

### El mapa

| Ciudad | `lead_pipeline_id` |
|---|---|
| Santa Cruz | `1` ← **por defecto** |
| Potosí | `4` |
| Oruro | `6` |
| La Paz | `7` |
| Cochabamba | `8` |
| Sucre | `9` |
| **Sin ciudad** | `10` ← fallback |

> ⚠️ **Los ids no son correlativos** (faltan 2, 3 y 5). No los generes ni los
> asumas: usá exactamente estos, o resolvelos por nombre con
> `GET /api/v1/settings/pipelines`.

### Por qué hay que mover el lead

`ConversationResolver::createLead()` usa el pipeline marcado como `is_default`,
que hoy es **Santa Cruz**. Es decir: **todo lead que entra por WhatsApp o
Messenger nace en Santa Cruz**, venga de donde venga el cliente.

El trabajo del agente es corregirlo en cuanto sepa la ciudad.

### Preguntar la ciudad en el saludo

En el primer mensaje de una conversación nueva, el agente pregunta la ciudad
antes de avanzar con la consulta. Las opciones son las seis de la tabla.

Si el cliente no contesta la ciudad, o dice una que no está en la lista, el lead
va a **`10` (Sin ciudad)**. No lo dejes en Santa Cruz por omisión: eso ensucia
las métricas de la sucursal más grande con demanda de todo el país.

### Mover el lead

Primero resolvé la etapa inicial del pipeline destino — **no hardcodees ids de
etapa**, cada pipeline tiene los suyos:

```bash
GET /api/v1/settings/pipelines/8
```

Devuelve el pipeline con su colección `stages`. Tomá la de menor `sort_order`.

Después actualizá el lead:

```bash
curl -X PUT https://imprimir.sofopolis.com/api/v1/leads/86 \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer 12|AbCdEf...' \
  -d '{
    "lead_pipeline_id": 8,
    "lead_pipeline_stage_id": 41
  }'
```

Movelo **una sola vez**, cuando el cliente confirma la ciudad. Si después
menciona otra, avisale a un asesor en vez de reasignar solo: puede ser un
segundo trabajo, no una corrección.

---

## 2. Crear la cotización

### `POST /api/v1/quotes` — campos obligatorios

| Campo | Tipo | Nota |
|---|---|---|
| `subject` | string | Título de la cotización |
| `person_id` | int | Viene en `contact.person_id` del evento |
| `user_id` | int | Asesor dueño |
| `expired_at` | `YYYY-MM-DD` | Hay que calcularlo, ver abajo |
| `sub_total` | decimal | |
| `grand_total` | decimal | |
| `items` | array | **Nunca lo omitas**, ver la advertencia |

### Opcionales

`description`, `billing_address`, `shipping_address`, `discount_percent`,
`discount_amount`, `tax_amount`, `adjustment_amount`, `lead_id`, `marca`.

- **`lead_id`** — mandalo siempre. Es lo que vincula la cotización con la
  oportunidad y, por lo tanto, con el pipeline de la ciudad.
- **`marca`** — id de opción del atributo *Marca* (Imprimir / Magia Verde).
  Define con qué logo y color se imprime el PDF. Si no sabés cuál corresponde,
  omitilo: el PDF sale con el diseño por defecto en vez de con la marca
  equivocada.
- **`entity_type`** — no lo mandes, el controlador lo agrega solo.

### ⚠️ `items` es obligatorio aunque la validación no lo diga

`QuoteRepository::create()` hace `foreach ($data['items'] as ...)` **sin
comprobar que exista**. Sin `items` no recibís un 422 de validación: recibís un
**500**. Mandá siempre al menos un ítem.

```jsonc
{
  "sku": "TARJ-500",
  "name": "Tarjetas de presentación 500u",
  "quantity": 1,
  "price": 350.00,
  "total": 350.00,
  // opcionales
  "product_id": 12,
  "discount_percent": 0,
  "discount_amount": 0,
  "tax_percent": 0,
  "tax_amount": 0
}
```

### ⚠️ `expired_at`: los 7 días son del formulario, no de la API

La política es que las cotizaciones vencen a los 7 días, pero ese default vive en
el formulario web del CRM, **no en el repositorio**. La API no lo aplica: si no
mandás `expired_at`, es 422.

```python
expired_at = (date.today() + timedelta(days=7)).isoformat()
```

### Ejemplo completo

```bash
curl -X POST https://imprimir.sofopolis.com/api/v1/quotes \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer 12|AbCdEf...' \
  -d '{
    "subject": "Cotización tarjetas de presentación",
    "person_id": 239,
    "user_id": 1,
    "lead_id": 86,
    "expired_at": "2026-08-19",
    "sub_total": 350.00,
    "grand_total": 350.00,
    "items": [
      {
        "sku": "TARJ-500",
        "name": "Tarjetas de presentación 500u",
        "quantity": 1,
        "price": 350.00,
        "total": 350.00
      }
    ]
  }'
```

---

## 3. Los demás endpoints

| Método | Ruta | Uso desde el agente |
|---|---|---|
| `GET` | `/api/v1/quotes` | Listar |
| `GET` | `/api/v1/quotes/search` | Buscar por `subject`, `description`, `person.name`, `user.name` |
| `GET` | `/api/v1/quotes/{id}` | Leer una |
| `PUT` | `/api/v1/quotes/{id}` | Actualizar — **ver la trampa** |
| `DELETE` | `/api/v1/quotes/{id}` | **No usar** |
| `POST` | `/api/v1/quotes/mass-destroy` | **No usar** |

> ⚠️ **Trampa en `PUT`**: hace `leads()->detach()` y vuelve a vincular *solo si*
> mandás `lead_id`. Si actualizás una cotización sin incluirlo, **la desvinculás
> de su oportunidad**. Incluí siempre `lead_id` en los PUT.

**El agente no debe borrar cotizaciones.** Es destructivo e irreversible, y
corresponde a una persona. Si una salió mal, que cree una nueva o avise al
asesor.

---

## 4. Validaciones que el agente debe hacer ANTES de llamar

El CRM valida tipos, no criterio comercial. Estas son del agente:

1. **No cotizar sin precio confirmado.** Si no tenés precio de lista para lo que
   pide el cliente, derivá a un asesor. Una cotización con precio inventado es
   peor que ninguna: es un documento comercial que el cliente va a tomar como
   compromiso.
2. **`sub_total` y `grand_total` tienen que cerrar con los ítems.** El CRM no lo
   verifica, guarda lo que le mandes. Un total que no coincide con las líneas
   sale impreso así en el PDF que ve el cliente.
3. **`person_id` tiene que existir.** Usá el de `contact.person_id`. Si viene
   `null`, el contacto todavía no está registrado: no inventes un id.
4. **Confirmá con el cliente antes de crear.** Resumí lo cotizado y esperá el
   sí antes del POST.
5. **Una cotización por pedido.** Revisá con `GET /api/v1/quotes/search` que no
   exista ya una para ese contacto y ese trabajo.
6. **Ciudad antes de cotizar.** El lead tiene que estar en el pipeline correcto
   antes de que exista la cotización, así la oportunidad se contabiliza en la
   sucursal que corresponde.

---

## 5. Errores a manejar

| Código | Significado | Qué hacer |
|---|---|---|
| `422` | Falta un obligatorio o el tipo es inválido | Corregir el body; el cuerpo trae `errors` por campo |
| `500` sin `items` | El bug descrito arriba | Mandar siempre `items` |
| `404` | No existe | Revisar el id |
| `401` / `500` en auth | Token inválido o ausente | Revisar el Bearer. *La REST API de Krayin devuelve `500` en vez de `401` ante token inválido — es comportamiento global de la plataforma* |

---

## Anexo — dos cosas para decidir del lado del CRM

No bloquean al agente, pero conviene resolverlas:

1. **El pipeline por defecto es Santa Cruz.** Todo lead de mensajería nace ahí y
   depende del agente para salir. Si el agente falla, se cae o el cliente no
   contesta la ciudad, el lead queda contado como Santa Cruz. Mover el
   `is_default` a **Sin ciudad (10)** haría que el estado inicial sea neutro y
   que un fallo del agente no distorsione las métricas de una sucursal real.

2. **La regla de los 7 días vive solo en el formulario web.** Cualquier
   cotización creada por API tiene que calcular `expired_at` por su cuenta. Si se
   moviera el default a `QuoteRepository::create()`, la política valdría para
   todos los caminos y el agente podría omitir el campo.
