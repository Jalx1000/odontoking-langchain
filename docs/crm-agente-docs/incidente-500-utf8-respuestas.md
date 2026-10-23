# Aviso al equipo del CRM — error 500 al guardar respuestas del agente (UTF-8)

> Para el equipo del CRM (Sensia). Incidente detectado el **22/10/2026** en producción, con
> tráfico real. **Ya lo mitigamos del lado del agente**; abajo explicamos qué pasó y qué conviene
> reforzar del lado del CRM para que no vuelva a cortar mensajes.

---

## 1. Qué se vio

Cuando el agente contestaba, en varios turnos el envío de la respuesta al CRM fallaba con **HTTP 500**:

```
POST https://sensia.sofopolis.com/api/v1/whatsapp/conversations/<id>/messages → 500

{
  "message": "Unable to encode attribute [payload] for model [Webkul\\Whatsapp\\Models\\Message]
              to JSON: Malformed UTF-8 characters, possibly incorrectly encoded.",
  "exception": "Illuminate\\Database\\Eloquent\\JsonEncodingException"
}
```

**Efecto para el cliente:** esa respuesta **no se guardaba ni se enviaba** → el cliente quedaba sin
contestación en ese turno (aunque el agente sí la había generado).

Conversaciones afectadas en las pruebas: 8284, 8285, 8286 (entre las 17:14 y 17:16 hora Jujuy).

---

## 2. Por qué pasaba

- La respuesta del modelo de IA, en algunos casos, incluía un **carácter Unicode mal formado**
  (un "surrogate suelto": típicamente un **emoji que quedó cortado**, p. ej. `\uD83D` sin su par).
- Ese carácter viaja en el JSON que le mandamos al CRM. Al CRM (Laravel/Webkul), al intentar
  **re-serializar** el mensaje a JSON para guardarlo (`json_encode` del atributo `payload`), ese
  byte inválido le hace lanzar `JsonEncodingException` → **500**.
- Por eso unos mensajes pasaban (emojis bien formados como 💧 👋) y otros no (cuando venía uno
  cortado).

**En una línea:** el agente mandaba texto con un emoji mal cerrado; el CRM no lo pudo serializar y
devolvió 500.

---

## 3. Qué hicimos de nuestro lado (ya desplegado)

- **Saneamos la respuesta a UTF-8 válido antes de enviarla al CRM.** Descartamos únicamente los
  code points inválidos (surrogates sueltos); los emojis bien formados y los acentos quedan intactos.
  Con esto, el agente ya no manda texto que rompa el `json_encode` del CRM.
- Como parte del mismo trabajo, ante **cualquier** error al contestar el agente **no concluye nada
  del negocio** (no dice "no hay productos" ni "no hay cobertura"): se disculpa y sigue. Eso ya evitó
  que este 500 derivara en un mensaje equivocado al cliente.

Commit del agente: `3b6aeab` (desplegado en el servicio Sensia).

---

## 4. Qué pedimos reforzar del lado del CRM

El fix del agente ya evita el caso conocido, pero **el endpoint no debería responder 500 por el
contenido de un mensaje entrante**. Un texto "raro" de un canal externo no tendría que tumbar el
guardado. Sugerencias (elijan la que les quede mejor):

1. **Sanear el texto al recibirlo**, igual que hacemos nosotros: descartar/normalizar UTF-8 inválido
   antes de persistir (`mb_convert_encoding($text, 'UTF-8', 'UTF-8')` o equivalente).
2. **Guardar el `payload` como UTF-8 robusto**: usar el flag `JSON_INVALID_UTF8_SUBSTITUTE` (o
   `JSON_INVALID_UTF8_IGNORE`) en el `json_encode` del modelo `Message`, para que un carácter inválido
   no lance excepción.
3. **Responder 422 en vez de 500** cuando el cuerpo es inválido. Nos sirve porque el agente trata el
   `422` como terminal (no reintenta) y el `5xx` como transitorio (reintenta): hoy, al venir 500,
   lo interpretamos como un fallo temporal del servidor, no como "este texto no se puede guardar".

Con cualquiera de las tres, un carácter inválido deja de poder cortar el guardado de un mensaje.

---

## 5. Cómo se interpreta un 500 vs 422 (contexto para el CRM)

Para que quede claro cómo reacciona el agente a los códigos del endpoint de respuesta:

| Código del CRM | Qué entiende el agente | Acción |
|---|---|---|
| `200` | Mensaje guardado/encolado | Sigue normal |
| `404` / `409` / `422` | **Terminal**: no tiene sentido reintentar | Loguea y corta (no reintenta) |
| `5xx` (incl. este 500) | **Transitorio**: el servidor falló | Puede reintentar con espera |

Por eso, si el problema es el **contenido** (UTF-8), el código correcto sería **422**, no 500: así
nadie reintentaría en vano.

---

## Resumen

| | Estado |
|---|---|
| Respuestas con UTF-8 inválido cortaban el guardado (500) | 🟠 pasó el 22/10 |
| Agente sanea el texto antes de enviar | ✅ desplegado (`3b6aeab`) |
| Agente no afirma cosas del negocio ante un error | ✅ desplegado |
| CRM tolera/normaliza UTF-8 inválido al guardar | 🔴 recomendado (ver §4) |
| CRM responde 422 (no 500) ante cuerpo inválido | 🔵 opcional, nos ayuda (ver §4/§5) |
