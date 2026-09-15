# Round robin de asesores — a quién derivar (15/09/2026)

## Qué cambia para el agente

El CRM ahora sabe **quién atiende cada producto en cada ciudad** y reparte las
consultas por turno. El agente no elige al asesor: manda producto y ciudad, y
el CRM decide. Reglas (las decidió Imprimir):

1. **Local primero.** Si en la ciudad del cliente hay asesores para ese
   producto, se reparte entre ellos. Los de cobertura nacional («Bolivia»)
   solo entran cuando en esa ciudad no hay nadie para ese producto.
2. **Turnos.** El siguiente es el que hace más tiempo no recibe una consulta.
3. **Un cliente no cambia de asesor a cada pedido.** Si el contacto ya tiene
   dueño y ese dueño atiende ese producto en esa ciudad, se queda con él.
4. **Fuera de horario (08:00–19:00) se asigna igual.** El asesor la ve al día
   siguiente; el agente le avisa al cliente el horario.

Equipo cargado hoy:

| Producto (SKU) | Santa Cruz | La Paz | Cochabamba | Bolivia (resto) |
|---|---|---|---|---|
| Stretch Film (CM_00001) | — | Gabriela Marconi | Mauricio Garces | Bulmaro Vaca · Mariano Pinell |
| Bolsas Magia Verde (CM_00002) | — | Gabriela Marconi | Mauricio Garces | Eduardo Lujan · Mariano Pinell |
| Hules (CM_00003) | Fabio Sandoval | Gabriela Marconi | Mauricio Garces | Mariano Pinell |
| Tapas (CM_00004) | — | Gabriela Marconi | Mauricio Garces | Mariano Pinell |

(«—» = nadie local → cae en Bolivia. Se edita en la ficha de cada usuario —*Configuración → Usuarios*, sección «Atiende»— y se ve completa en *Configuración → Equipo de ventas*.)

## 1. Mandar la ciudad — `POST …/conversations/{id}/cotizacion`

Campo nuevo, opcional: **`ciudad`** (texto libre: «Santa Cruz», «la paz»,
«Cochabamba», «Tarija»…). **Mándenla siempre**: la `Zona` de los criterios no
sirve porque colapsa La Paz y Cochabamba en «Interior», y ahí cada una tiene
su asesor. Si no viene `ciudad`, se usa `Zona` solo cuando es una ciudad.

```json
{ "sku": "CM_00003", "criterios": {"Color": "Negro", "Zona": "Interior"},
  "cantidad": 5, "ciudad": "La Paz" }
```

La respuesta 201 trae ahora **`asesor`** (o `null` si nadie atiende esa
combinación — la cotización queda con el dueño que tenía):

```json
"asesor": { "id": 1053, "nombre": "Gabriela Marconi",
            "email": "gabriela-marconi@imprimir.com.bo", "telefono": null,
            "horario": "de 08:00 a 19:00" }
```

Úsenlo en el texto del PASO 5: *«Gabriela Marconi, nuestra asesora en La Paz,
le va a escribir en horario de 08:00 a 19:00»*. No inventen teléfono cuando
viene `null`.

## 2. Derivar — `POST /api/v1/whatsapp/conversations/{id}/handoff`

Campos nuevos, opcionales: **`sku`** y **`ciudad`**. Con los dos, la
conversación cae directo en la bandeja del asesor del turno (o del que ya
tiene el contacto). Sin ellos se enruta como antes: dueño del lead → dueño
del contacto → pozo compartido.

```json
{ "reason": "Pide hablar con un asesor por hules", "sku": "CM_00003", "ciudad": "Santa Cruz" }
```

Si ya crearon la cotización con `ciudad`, el contacto ya tiene dueño y el
handoff sin `sku` cae en la misma persona. Mandar `sku`+`ciudad` igual no
hace daño (es idempotente: no cambia de asesor a un contacto que ya tiene
uno que atiende).

## 3. Consultar sin asignar — `GET /api/v1/productos/responsables?sku=…&ciudad=…`

Para contestar «¿con quién hablo por hules en La Paz?» o presentar al asesor
**antes** de derivar. No mueve el turno.

```json
{ "producto": {"sku": "CM_00003", "nombre": "Hules"}, "ciudad": "La Paz",
  "cobertura": "local",                       // "local" | "nacional" | null (nadie)
  "horario": {"desde": "08:00", "hasta": "19:00"},
  "siguiente": { "id": 1053, "nombre": "Gabriela Marconi", "email": "…", "telefono": null, "horario": "de 08:00 a 19:00" },
  "responsables": [ { "...igual que siguiente...", "cobertura": "La Paz" } ] }
```

404 si el SKU no existe. `ciudad` es opcional: sin ella devuelve la cobertura
nacional.

## 4. Qué NO hacer

- No manden `user_id` ni el nombre del asesor a ningún endpoint: el CRM lo
  decide y lo ignora.
- No deriven «a Mariano» por prompt: si el equipo cambia en el CRM, el prompt
  queda viejo. Pregunten al endpoint.

## 5. Probar con curl antes de conectar el agente

```bash
TOKEN='...'                                   # el mismo sanctum de siempre
BASE='https://imprimir.sofopolis.com/api/v1'
H=(-H "Authorization: Bearer $TOKEN" -H 'Accept: application/json' -H 'Content-Type: application/json')

# 1) ¿Quién atiende hules en La Paz?  → cobertura "local", siguiente = Gabriela
curl -sS -G "$BASE/productos/responsables" \
  --data-urlencode 'sku=CM_00003' --data-urlencode 'ciudad=La Paz' "${H[@]}" | jq

# 2) Stretch film en Santa Cruz: nadie local → cobertura "nacional" (Bulmaro / Mariano por turno)
curl -sS -G "$BASE/productos/responsables" \
  --data-urlencode 'sku=CM_00001' --data-urlencode 'ciudad=Santa Cruz' "${H[@]}" | jq

# 3) Ciudad sin cobertura ni nacional (no debería pasar hoy) → cobertura null, siguiente null
curl -sS -G "$BASE/productos/responsables" \
  --data-urlencode 'sku=CM_00003' --data-urlencode 'ciudad=Tarija' "${H[@]}" | jq '.cobertura, .siguiente'

# 4) SKU inexistente → 404
curl -sS -G "$BASE/productos/responsables" --data-urlencode 'sku=NADA' "${H[@]}" -w '\nHTTP %{http_code}\n'

# 5) Crear la cotización CON ciudad (cambiá 123 por un conversation_id real con contacto asociado).
#    Mirá `asesor` en la respuesta: es quien quedó dueño del contacto y de la cotización.
curl -sS -X POST "$BASE/productos/conversations/123/cotizacion" "${H[@]}" \
  -d '{"sku":"CM_00003","criterios":{"Color":"Negro","Zona":"Interior"},"cantidad":5,"ciudad":"La Paz"}' | jq '.quote_id, .asesor'

# 6) Repetir el 5 con otro producto para el MISMO contacto: si su asesor también atiende
#    ese producto en esa ciudad, `asesor` es el mismo (no cambia de asesor a cada pedido).
curl -sS -X POST "$BASE/productos/conversations/123/cotizacion" "${H[@]}" \
  -d '{"sku":"CM_00004","criterios":{"Impresión":"Con impresión"},"cantidad":30,"ciudad":"La Paz"}' | jq '.asesor'

# 7) Derivar con producto y ciudad → handoff.assigned_user es el del turno (o el que ya tenía el contacto)
curl -sS -X POST "$BASE/whatsapp/conversations/123/handoff" "${H[@]}" \
  -d '{"reason":"Pide hablar con un asesor por hules","sku":"CM_00003","ciudad":"Santa Cruz"}' | jq '.handoff'

# 8) Derivar SIN producto (cliente que no quiso decir qué busca) → dueño del lead/contacto, o pozo
curl -sS -X POST "$BASE/whatsapp/conversations/124/handoff" "${H[@]}" \
  -d '{"reason":"Quiere hablar con una persona"}' | jq '.handoff.state, .handoff.assigned_user'

# 9) Ver el turno avanzar: correr el 1 dos veces NO cambia `siguiente` (solo consulta);
#    correr el 5 con dos contactos nuevos de La Paz para Stretch Film (CM_00001) sí:
#    con un solo asesor local (Gabriela) siempre es ella; en Bolivia alternan Bulmaro y Mariano.
for c in 201 202; do
  curl -sS -X POST "$BASE/productos/conversations/$c/cotizacion" "${H[@]}" \
    -d '{"sku":"CM_00001","criterios":{"Zona":"Santa Cruz"},"cantidad":1,"ciudad":"Santa Cruz"}' | jq -c '.asesor.nombre'
done
```

Qué esperar en `.handoff` tras el 7:

```json
{ "state": "assigned", "open": true, "pooled": false,
  "assigned_user": { "id": 1052, "name": "Fabio Sandoval" }, ... }
```

Si en vez de eso ven `"state": "requested", "pooled": true`, es que nadie
atiende ese SKU en esa ciudad ni en Bolivia: revisar *Configuración → Equipo
de ventas*.
