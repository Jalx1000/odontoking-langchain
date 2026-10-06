# Para el CRM — el webhook `location` de Sensia no incluye las coordenadas

> Para el equipo del CRM (Sensia). Verificado con tráfico real de producción el 2026-10-06.
> **Hay un cambio que les pedimos (§3).** De nuestro lado ya dejamos el agente listo para usar las
> coordenadas apenas las manden, y mientras tanto no se queda mudo (§2).

---

## 1. Qué está pasando

Cuando un cliente comparte su ubicación por WhatsApp (el pin 📍), el evento que nos llega a
`POST /api/v1/crm/webhook` viene así (dos casos reales, conversaciones 5876 y 1):

```json
{
  "event": "message.received",
  "conversation_id": 5876,
  "gateway": "cloud_api",
  "contact": { "phone": "+549388...", "name": "Rodrigo", "person_id": 8971, "lead_id": null },
  "message": { "id": 3240, "type": "location", "text": null, "selection": null,
               "timestamp": "2026-10-06T16:34:38-04:00" }
}
```

Llega `message.type = "location"`, pero **sin latitud ni longitud en ningún campo** (`text` es
`null`, no hay `location`, ni `coordinates`, ni `latitude`/`longitude`). Sin coordenadas, no hay
ubicación que podamos registrar.

Antes esto además dejaba al agente **sin responder** (descartábamos todo lo que no fuera texto). Eso
ya lo arreglamos (ver §2), pero el pin sigue sin servir para geolocalizar hasta que lleguen las
coordenadas.

---

## 2. Qué hicimos de nuestro lado (ya desplegado)

- **El agente ya no se queda mudo ante un pin.**
  - Si el evento `location` trae coordenadas → las tomamos y las guardamos como `lat,lng` en el
    atributo `ubicacion_lead` del lead.
  - Si NO trae coordenadas (caso de hoy) → le pedimos al cliente que **escriba la dirección en texto**
    (barrio, calle, número y referencia), y esa dirección se guarda igual en `ubicacion_lead`.
- **Ya aceptamos las coordenadas en varios formatos**, así que cuando las empiecen a mandar funciona
  sin otro cambio de nuestra parte. Soportamos cualquiera de estos:
  - `message.latitude` + `message.longitude` (números, top-level), **o**
  - `message.location: { "latitude": ..., "longitude": ... }` (objeto anidado).

---

## 3. Lo que les pedimos

**Incluir las coordenadas del pin en el `message` del evento `message.received` cuando
`type = "location"`.** Con cualquiera de estas dos formas nos alcanza:

```jsonc
// Opción A — campos planos
"message": { "id": 3240, "type": "location",
             "latitude": -24.193, "longitude": -65.297, "timestamp": "..." }

// Opción B — objeto anidado
"message": { "id": 3240, "type": "location",
             "location": { "latitude": -24.193, "longitude": -65.297,
                           "name": "Casa", "address": "..." },
             "timestamp": "..." }
```

- Las coordenadas con **al menos 3 decimales** (menos precisión deja el pin a >100 m).
- Si WhatsApp provee nombre/dirección del lugar (`name` / `address`), incluirlos también ayuda, pero
  lo imprescindible es `latitude` + `longitude`.

Con eso guardamos el pin exacto del cliente. Hasta entonces seguimos pidiendo la dirección por texto.

---

## Resumen

| | Estado |
|---|---|
| El pin de WhatsApp llega como `type:location` **sin coordenadas** | 🔴 a corregir en el CRM |
| El agente responde igual (pide dirección por texto) | ✅ ya desplegado |
| El agente usa las coordenadas apenas el CRM las mande | ✅ ya listo, sin más cambios de nuestro lado |
