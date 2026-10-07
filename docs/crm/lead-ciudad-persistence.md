# CRM handoff — persistir el atributo `ciudad` en Leads

> **Para:** equipo sofo-crm (Krayin). **De:** agente IMPRIMIR (Valentina).
> **Estado:** bloqueado del lado del CRM. El agente ya envía el valor correcto; falta que el CRM lo
> persista. **Severidad:** alta (el atributo `ciudad` del lead queda vacío en todas las cotizaciones).

## Qué necesitamos (contrato)

`PUT /api/v1/leads/{id}` debe **persistir** el atributo personalizado **`ciudad`** (select,
`attribute_id = 98`, `entity_type = leads`) cuando llega en el body, y `GET /api/v1/leads/{id}` debe
**serializarlo** en la respuesta. Es exactamente lo que ya hace el endpoint de cotizaciones
(`/api/v1/productos/conversations/{id}/cotizacion`) — hay que replicar ese manejo en el update/create
de leads (idealmente el comportamiento estándar de Krayin para custom attributes).

Opciones del select (lo que el agente envía como valor):

| opción (id) | ciudad     |
|-------------|------------|
| 40          | Santa Cruz |
| 41          | La Paz     |
| 42          | Cochabamba |
| 44          | Sucre      |
| 45          | Oruro      |
| 46          | Potosí     |
| 47          | Tarija     |
| 48          | Trinidad   |
| 49          | Cobija     |
| 50          | Exterior   |

El agente envía el **id de opción** (ej. `42` para Cochabamba) dentro del mismo PUT que mueve el
pipeline:

```json
PUT /api/v1/leads/870
{ "lead_pipeline_id": 8, "lead_pipeline_stage_id": 31, "ciudad": 42 }
```

## Evidencia del bug (reproducible hoy)

Contra `https://imprimir.sofopolis.com`, lead de prueba `870`:

1. El PUT responde **200 `{"message":"Lead actualizado con éxito."}`** pero `GET` del lead **no
   devuelve `ciudad`** (ni como `ciudad`, ni en ningún bloque `custom_attributes` — la respuesta solo
   trae campos core: `title`, `lead_pipeline_id`, `person`, etc.).
2. Probamos **5 formatos** de `ciudad` en el body — ninguno persiste:
   - `42` (int) · `"42"` (str) · `"Cochabamba"` (label) · `{"custom_attributes": {"ciudad": 42}}` ·
     `{"ciudad": {"id": 42}}`.
3. No existe endpoint alternativo: `GET/PUT /api/v1/leads/{id}/attributes`,
   `/attribute_values`, `/api/v1/attribute_values` → **404**.

Conclusión: el update/serialización de leads del REST API **ignora por completo los custom
attributes**. No es un problema de formato del cliente.

> Nota aparte (ya resuelta del lado del agente): antes el PUT de `ciudad` daba **500 "Undefined array
> key lead_pipeline_stage_id"** (LeadController.php:125) porque el controlador exige
> `lead_pipeline_stage_id` en todo update. El agente ahora siempre lo incluye, por eso hoy da 200.

## Criterio de aceptación / cómo validamos

Hay un script en el repo del agente que da PASS/FAIL objetivo:

```bash
uv run python scripts/verify_lead_ciudad.py 870 Cochabamba
```

- **Hoy:** imprime `FAIL ❌ … la ciudad NO persiste`.
- **Con el fix del CRM:** debe imprimir `PASS ✅ — la ciudad persiste`.

Eso es "validado de verdad": el mismo PUT que ya manda el agente hace que el `GET` del lead muestre
`ciudad = Cochabamba` (opción 42).

## Qué NO hay que cambiar en el agente

Nada: el agente ya manda `ciudad` como id de opción correcto en el PUT del pipeline
(`app/core/langgraph/tools/crm.py` → `_route_lead_pipeline`, mapa `_CITY_OPTION_IDS`). Apenas el CRM
persista el atributo, se llena solo, sin redeploy del agente.
