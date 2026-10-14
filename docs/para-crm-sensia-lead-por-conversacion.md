# Para el CRM — crear el lead por conversación y mandarlo en `contact.lead_id`

> Para el equipo del CRM (Sensia). Pedido chico pero importante para captar prospectos.

## El problema

Cuando llega el PRIMER mensaje de una conversación nueva, **todavía no existe un lead** del cliente
en el CRM. El agente recién crea el lead cuando el cliente da su zona (varios mensajes después). Si
la persona escribe "Hola, quiero agua" y abandona antes de dar la zona, **no queda registrada** y el
equipo no ve que hubo un contacto.

Además, hoy el evento `message.received` llega con:

```json
"contact": { "phone": "...", "name": "...", "person_id": 12, "lead_id": null }
```

`contact.lead_id` viene **null**. En los otros tenants (Kohlberg, etc.) el CRM auto-crea UN lead por
conversación y lo manda en `contact.lead_id`; el agente lo reutiliza en vez de crear duplicados.

## Lo que pedimos

**Que el CRM cree el lead de la conversación en el primer contacto y lo envíe en
`contact.lead_id`** en todos los eventos `message.received` (igual que en los demás tenants).

Con eso:
- Todo cliente que escribe queda registrado como lead desde el primer mensaje, aunque no complete el
  pedido.
- El agente **reutiliza ese `lead_id`** para armar/confirmar el pedido sobre el mismo lead (ya está
  implementado: si `contact.lead_id` viene, el agente hace PUT sobre él; no crea uno nuevo).

## Lo que NO queremos que haga el agente

Crear el lead desde el agente en el primer mensaje significaría meterlo sin zona todavía conocida, en
el pipeline "Sin ciudad" (que no tiene distribuidor) o en una zona equivocada. Por eso la creación
inicial corresponde al CRM, que es dueño del ciclo de vida del lead/conversación.

## Reparto de responsabilidades

| | |
|---|---|
| Crear el lead de la conversación en el primer contacto | **CRM** |
| Mandarlo en `contact.lead_id` en cada evento | **CRM** |
| Reutilizar ese lead para armar/confirmar el pedido | **Agente (ya hecho)** |
| Mover el lead a la zona/distribuidor cuando el cliente la da | **Agente (ya hecho)** |

## Estado

- Agente: ✅ ya reutiliza `contact.lead_id` apenas el CRM lo mande (sin más cambios).
- CRM: 🔴 pendiente — hoy manda `contact.lead_id: null`.
