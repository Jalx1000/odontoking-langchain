# Sprint — Takeover CRM (MVP: switch + mensajería humana de texto)

> Diseño completo de referencia: [../07-modo-humano-takeover-crm.md](../07-modo-humano-takeover-crm.md)
> Alcance: solo **odontoking** (single-tenant). Arquitectura actual intacta.

## ⚠️ Prioridad / orden

Este sprint va **DESPUÉS** de estabilizar el agente actual (bugs en `todo/` + los que el
usuario reporte). Mientras tanto queda como plan listo para arrancar.

## Objetivo del sprint

Permitir que el CRM **apague/encienda el agente por chat** y que un asesor **converse por
texto** con el paciente (takeover humano), respetando la ventana de 24 h de Meta. Sin media
todavía (eso es el sprint siguiente).

Cubre las **Fases A + B** del diseño.

## Resultado esperado (Definition of Done)

- El CRM puede `POST /agent` (on/off) y `GET /status` (incluye ventana 24 h).
- Con agente **OFF**, un mensaje entrante del paciente **NO** invoca al agente y se hace
  **push** al webhook del CRM.
- El CRM puede `POST /messages` (texto) → se envía al paciente y **auto-apaga** el agente.
- Fuera de la ventana de 24 h, `POST /messages` devuelve `service_window_closed`.
- Espejo (`message_in` / `message_out`) al CRM también con el agente ON (`CRM_MIRROR_AGENT_MODE`).
- El flujo actual del agente **no cambia** cuando el CRM no está configurado.

## Decisiones aplicadas (ronda 2)

- Estado on/off: **nuestra DB es la fuente de verdad** vía endpoints REST.
- Entrada al CRM: **push** a `CRM_WEBHOOK_URL`.
- Reactivación: **auto-OFF al escribir** el humano + reactivación manual.
- `advisor`: **solo auditoría** (no se muestra al paciente).
- Espejo: **siempre** (configurable).

## Archivos del sprint

- [01.platform-dev.md](01.platform-dev.md) — endpoints, hook del webhook, services, config.
- [02.infra-dev.md](02.infra-dev.md) — modelo `conversation_control` + migración.
- [03.security-dev.md](03.security-dev.md) — auth `X-CRM-Key`, HMAC del push, firma de Meta.
- [04.qa-dev.md](04.qa-dev.md) — tests del sprint.

## Fuera de alcance (va al sprint Media)

- Audio, documentos, imágenes (entrada y salida).
- `media_store` y `GET /media/{id}`.
- `conversation_message` (historial unificado) y `GET /messages`.
