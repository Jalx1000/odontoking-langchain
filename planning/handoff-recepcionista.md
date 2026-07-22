# Derivación agente → recepcionista (CONTRATO B)

**Estado:** diseño acordado · **Estimado:** ~1 h (solo lado agente) · Última actualización: 2026-07-22

## 1. Objetivo

Cuando el agente IA decide pasar la conversación a una **recepcionista humana**, debe: (a) avisar al
paciente y (b) **marcar la conversación como "en recepción"**. A partir de ahí el humano atiende y la IA
queda fuera hasta que la conversación vuelva a una etapa normal.

## 2. Decisión de arquitectura (acordada)

**El agente NO crea endpoints ni captura nada especial.** El único trabajo del agente es **mover el
lead-conversación a la etapa Recepcionista (`9`)**. La pausa la maneja el CRM solo:

> **Mientras el lead está en etapa 9, el CRM NO reenvía los `message.received` al agente.**

Por lo tanto el agente no necesita auto-silenciarse, ni chequear estado, ni recibir señales de vuelta.

- **Etapa Recepcionista = pipeline stage `9`.**
- Token para mutar el CRM: **`ODONTOKING_API_TOKEN`** (el mismo de las tools de leads/citas).
- Endpoint (ya en uso): `PUT /api/v1/leads/stage/edit/{lead_id}` con `{"lead_pipeline_stage_id":[9]}`.

## 3. Flujo

```text
Paciente ──▶ webhook (message.received)
                 │
                 ▼
   Agente decide handoff  (el LLM emite {action:"handoff", motivo, fuera_de_horario})
                 │
     ┌───────────┴───────────────┐
     ▼                           ▼
1) Envía al paciente        2) Mueve el LEAD-CONVERSACIÓN a etapa 9
   el mensaje (texto)          PUT /api/v1/leads/stage/edit/{lead_id}
                               {"lead_pipeline_stage_id":[9]}
                               Bearer ODONTOKING_API_TOKEN
                 │
                 ▼
   CRM deja de reenviar eventos (lead en etapa 9) → recepción atiende manual
```

### Reactivación

La recepcionista **saca el lead de la etapa 9** (lo devuelve a una etapa normal) desde el CRM. El CRM
vuelve a reenviar los `message.received` y el agente **reanuda** solo, sin nada extra.

## 4. Qué necesita el CRM

| Ítem | ¿Requerido? |
| --- | --- |
| Etapa Recepcionista (id `9`) | ✅ Ya existe |
| `ODONTOKING_API_TOKEN` puede `PUT /leads/stage/edit/{id}` | ✅ Ya (mismo token de las tools) |
| No reenviar `message.received` mientras el lead está en etapa 9 | ✅ Ya es el comportamiento del CRM |
| Recepción ve/atiende el lead en etapa 9 | ✅ Comportamiento normal del pipeline/inbox |

**Nada nuevo por implementar del lado CRM.**

## 5. Cambios del lado AGENTE (nuestro, pendiente)

Hoy `CrmGateway.send_handoff` postea `{action:"handoff"}` a la conversación. Se reemplaza por:

1. **Mover etapa:** `PUT {ODONTOKING_API_URL}/api/v1/leads/stage/edit/{lead_id}` con
   `{"lead_pipeline_stage_id":[9]}` y `Authorization: Bearer {ODONTOKING_API_TOKEN}`.
2. **Resolver el `lead_id`** del lead-conversación (`find_person_by_wa_id` → lead Consulta), igual que
   `cancel_appointment`.
3. Enviar primero el mensaje al paciente, luego mover la etapa.
4. El LLM ya emite `{action:"handoff", motivo, fuera_de_horario}` y `_extract_handoff` ya lo parsea; solo
   cambia lo que se hace con la señal (mover etapa en vez de postear).

> Ya NO hace falta: auto-silencio, chequeo de etapa por mensaje, ni endpoint/handoff-receiver.

## 6. Criterios de aceptación

- [ ] El agente pide recepcionista → el paciente recibe el mensaje **y** el lead-conversación queda en
      etapa 9.
- [ ] Tras el handoff, el agente **deja de recibir** los `message.received` de esa conversación (el CRM
      no los reenvía).
- [ ] Recepción atiende el lead en etapa 9 manualmente.
- [ ] Al sacar el lead de la etapa 9, un nuevo mensaje del paciente **vuelve** a llegar al agente y se
      responde normal.

## 7. Datos confirmados

- Etapa Recepcionista: **`9`**
- Token de mutación CRM: **`ODONTOKING_API_TOKEN`**
- Endpoint de etapa (ya en uso por las tools): `PUT /api/v1/leads/stage/edit/{lead_id}` con
  `{"lead_pipeline_stage_id":[<stage>]}`
- Sin endpoints nuevos del lado CRM; la pausa la garantiza el CRM al no reenviar eventos en etapa 9.
