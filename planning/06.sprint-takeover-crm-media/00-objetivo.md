# Sprint — Takeover CRM (Media + Endurecimiento)

> Diseño de referencia: [../07-modo-humano-takeover-crm.md](../07-modo-humano-takeover-crm.md)
> Requiere el sprint MVP ([../05.sprint-takeover-crm-mvp/](../05.sprint-takeover-crm-mvp/00-objetivo.md)) completo.

## Objetivo del sprint

Soportar **media** (audio, documentos, imágenes) en ambos sentidos y **endurecer** el
feature (idempotencia, historial, métricas, doc para el CRM). Cubre las **Fases C + D**.

## Resultado esperado (Definition of Done)

- Media entrante del paciente se **descarga, almacena y se hace push** al CRM con una URL nuestra.
- El webhook **deja de rechazar** imágenes/documentos: los guarda y reenvía (el agente sigue
  procesando solo texto/audio, pero ya no se pierden).
- El CRM puede `POST /messages` con media (`media_url`) → la subimos a Meta y la enviamos.
- `GET /media/{id}` sirve la media solo autenticada (PII).
- Idempotencia por `wa_message_id`; métricas; historial unificado `conversation_message`.
- **Retención: indefinida** (sin borrado), parámetro preparado en `0`.

## Archivos del sprint

- [01.platform-dev.md](01.platform-dev.md) — cliente de media WhatsApp, endpoints de media, idempotencia, métricas.
- [02.infra-dev.md](02.infra-dev.md) — modelos `media_asset` y `conversation_message` + migraciones + `media_store`.
- [03.qa-dev.md](03.qa-dev.md) — tests de media y endurecimiento.
