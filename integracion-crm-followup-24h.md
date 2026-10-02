# CRM (Krayin) — Follow-up automático antes de cerrarse la ventana de 24 h

Documento para el equipo del CRM. Pedido de una automatización de **recontacto** para no perder
leads que quedaron sin responder.

Base URL (despliegue Ponce): `https://poncedeleon.sofopolis.com`
Zona horaria de referencia: **America/La_Paz**.

> **Decisión acordada:** se implementa **íntegramente en el CRM**, versión **MVP §4.1** (mensaje de
> texto fijo en voz de Sofía). **No requiere ningún cambio en el agente.** La Fase 2 (§4.2, recordatorio
> contextual generado por el agente) queda **para más adelante**, no es parte de esta entrega.

---

## 1. Objetivo

Cuando un cliente deja de responder tras una conversación con el agente, la **ventana de 24 h de
WhatsApp** (texto libre, sin plantilla) se va cerrando. Queremos enviarle **un recordatorio en voz de
Sofía ~1 h antes de que la ventana expire**, para reenganchar mientras todavía se puede mandar texto
libre (sin costo de plantilla).

Por qué en el CRM y no en el agente: el CRM ya es dueño de `window.expires_at`, de si el cliente
respondió, del estado de la conversación (IA activa / tomada por asesor) y de la entrega. El agente es
reactivo (solo responde a eventos entrantes) y no tiene un planificador de tareas.

---

## 2. Regla de disparo

Programar un envío para el momento **`window.expires_at − 1 hora`**.

Enviar **solo si se cumplen TODAS** estas condiciones en ese momento:

- `ai_enabled == true` (la IA sigue a cargo; el asesor no tomó la conversación).
- La conversación **no fue derivada** a un asesor ni cerrada.
- El **cliente no respondió** desde el último mensaje del negocio (sigue "la pelota del lado del
  cliente").
- `window.open == true` (todavía dentro de las 24 h).
- **No se envió ya un follow-up en esta ventana** (máx. **1 por ventana**).

Si en el momento del disparo no se cumple alguna, **no enviar** (y descartar el disparo).

---

## 3. Reprogramación y cancelación

- **Cada mensaje nuevo del cliente reinicia la ventana de 24 h** → recalcular `expires_at` y
  **reprogramar** el follow-up al nuevo `expires_at − 1 h`. (El follow-up siempre apunta a la última
  ventana vigente.)
- **Cancelar** el follow-up pendiente si: la conversación se **deriva** a un asesor, el asesor **toma**
  la conversación (IA off), o la conversación se **cierra**.
- Tras enviarse una vez, no repetir en la misma ventana.

---

## 4. Envío — texto libre, sin plantilla

El follow-up se manda como **mensaje de texto normal** (estamos dentro de la ventana, no hace falta
plantilla). Queda marcado como `ia` en el inbox, igual que las demás respuestas del agente.

### 4.1 MVP — mensaje fijo (sin cambios en el agente)

Copy sugerido, en voz de Sofía (elegir uno; usar el nombre del contacto si está):

> «Hola {nombre} 👋 Quedé a la orden para ayudarle con su departamento en Ponce de León. ¿Seguimos?
> Si quiere, coordinamos una visita para que lo conozca en persona.»

Variante más corta:

> «Hola {nombre} 👋 ¿Le gustaría coordinar una visita para conocer el departamento? Quedo atenta.»

Reglas de copy: cordial, trato de «usted», una sola pregunta, sin presionar, sin mencionar sistema ni
plantillas. Si no hay nombre, omitirlo («Hola 👋 …»).

### 4.2 Fase 2 (opcional) — recordatorio con contexto, generado por el agente

Si quieren que el recordatorio sea **contextual** (que mencione el departamento que vio o dónde quedó
la charla), el CRM nos dispara un evento y el agente responde el texto por la `reply.url` de siempre:

```jsonc
POST <WHATSAPP_AGENT_WEBHOOK_URL>
Authorization: Bearer <WHATSAPP_AGENT_TOKEN>

{
  "event": "reengage",                // ← nuevo tipo de evento
  "conversation_id": 5,
  "gateway": "cloud_api",
  "ai_enabled": true,
  "contact": { "phone": "+59176616013", "name": "Alejandro", "person_id": 7, "lead_id": null },
  "history": [ /* últimos N mensajes, igual que en message.received */ ],
  "window": { "open": true, "expires_at": "…" },
  "reply": { "method": "POST", "url": "https://poncedeleon.sofopolis.com/api/v1/whatsapp/conversations/5/messages" }
}
```

El agente genera un recordatorio corto a partir del `history` y responde por la `reply.url`. Las mismas
condiciones de §2 las sigue validando el CRM **antes** de disparar el evento (el agente confía en que,
si llegó el `reengage`, corresponde enviar). Si les sirve este camino, lo coordinamos y yo implemento
el handler del lado del agente.

> Recomendación: arrancar con el **MVP §4.1** (cero cambios en el agente) y, si el recontacto funciona,
> evaluar la Fase 2 para hacerlo contextual.

---

## 5. Checklist para el CRM

- [ ] Programar tarea a `expires_at − 1 h` por conversación.
- [ ] Validar las condiciones de §2 en el momento del disparo (IA activa, no derivada, cliente sin
      responder, ventana abierta, no enviado aún).
- [ ] Reprogramar al recibir mensaje del cliente; cancelar al derivar / tomar / cerrar.
- [ ] Enviar el texto (MVP §4.1) marcado como `ia`.
- [ ] Idempotencia: máximo **1 follow-up por ventana**.
- [ ] (Opcional Fase 2) disparar `event: "reengage"` al agente en vez del texto fijo.
- [ ] Probar: conversación que queda sin respuesta → llega el recordatorio ~1 h antes del cierre; si el
      cliente responde o se deriva, **no** llega.
