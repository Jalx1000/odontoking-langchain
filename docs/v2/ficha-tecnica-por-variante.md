# Ficha técnica por variante — qué cambia para el agente (21/09/2026)

## Resumen

`POST /api/v1/productos/conversations/{id}/media` acepta ahora **`criterios`**,
los mismos que ya mandan a `precio`. Con ellos, el CRM manda **solo el adjunto
de esa variante**: la ficha de 50 L cuando el cliente pidió 50 L, no las cinco
fichas de Magia Verde.

Sin `criterios` el endpoint hace exactamente lo de siempre (todo lo del
producto), así que nada de lo que ya tienen se rompe.

## El pedido

```json
POST /api/v1/productos/conversations/123/media
{
  "sku": "CM_00002",
  "tipo": "documento",
  "criterios": { "Tamaño": "50 L" }
}
```

`cantidad` sigue siendo opcional. Con `criterios` casi nunca hace falta: una
variante tiene una ficha.

## La respuesta

```json
{
  "message": "Enviado.",
  "enviados": 1,
  "sku": "CM_00002",
  "variante": "50 L",
  "alcance": "variante"
}
```

| Campo | Qué dice |
|---|---|
| `variante` | Cómo resolvió el CRM los criterios (`"50 L"`), o `null` si no mandaron criterios |
| `alcance` | `"variante"`: salió el adjunto propio de esa variante · `"producto"`: esa variante no tiene adjunto propio y salieron los generales del producto |

Usen `variante` para el texto: *«Te mandé la ficha técnica de la bolsa de 50 L»*
y no *«te mandé la ficha»*.

## Qué se manda, en orden de preferencia

1. Los adjuntos **de esa variante** (la ficha de 50 L).
2. Si esa variante no tiene ninguno: los **generales** del producto (los que no
   son de ninguna variante: la foto del producto, un catálogo).
3. **Nunca** los de otra variante. Mandar la ficha de 140 L cuando pidieron
   50 L es peor que no mandar nada.

## Los 404

| Mensaje | Qué pasó |
|---|---|
| `El producto CM_00002 no tiene documentos para la variante «50 L» cargados en el catálogo.` | Ni esa variante ni el producto tienen documentos |
| `El producto … no tiene documentos cargados en el catálogo.` | Sin criterios, y el producto no tiene ninguno |

Como siempre: 404 y no 200 con cero enviados, para que puedan corregirse en el
mismo turno si ya le dijeron al cliente «te mando la ficha».

## Qué hay cargado hoy

| Producto | Fichas por variante |
|---|---|
| Bolsas Magia Verde (CM_00002) | **5**: 35 L · 50 L · 75 L · 140 L · 200 L, un PDF cada una |
| Stretch Film (CM_00001) | ninguna por variante; 2 documentos generales |
| Hules (CM_00003) | ninguna por variante; 1 documento general |
| Tapas (CM_00004) | ninguna por variante; 1 documento general |

Para los tres últimos, pedir con `criterios` hoy devuelve los generales
(`alcance: "producto"`). Cuando se carguen fichas por color o presentación,
empieza a devolver la específica sin que ustedes cambien nada.

## Criterios que resuelven

Los criterios tienen que identificar **una** variante. Para Magia Verde alcanza
con `Tamaño`; `Canal` y `Zona` son ejes de precio, no de variante, y se ignoran
si vienen. Igual que en `precio`, el valor es tolerante con mayúsculas y
acentos: `"50 l"` resuelve a `50 L`.

Si los criterios no casan con ninguna variante, se trata como si no vinieran:
salen todos los adjuntos del producto y `variante` viene `null`. Miren ese
campo antes de decirle al cliente qué le mandaron.

## Ejemplo con curl

```bash
TOKEN=$(grep -E '^WHATSAPP_AGENT_TOKEN=' /etc/easypanel/projects/heaven/imprimir_laravel/code/.env | cut -d= -f2- | tr -d '"')

curl -sS -X POST 'https://imprimir.sofopolis.com/api/v1/productos/conversations/123/media' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -d '{"sku":"CM_00002","tipo":"documento","criterios":{"Tamaño":"50 L"}}' | jq
```

(Reemplacen 123 por una conversación de prueba con la IA encendida: el PDF le
llega de verdad al cliente.)
