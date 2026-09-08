# Spec: Menús tocables end-to-end (ingesta de respuestas interactivas del CRM)

> **Fase 1 (Specify) — pendiente de aprobación.** No avanzar a Plan/Tasks/Implement hasta que el
> humano revise y apruebe este documento.

## Alcance (supuesto — corregir si hace falta)
**Enabler only.** Este spec cubre SOLO hacer que el toque de un botón/lista haga el viaje de ida y
vuelta. **No** convierte los menús de texto existentes (saludo/ciudad, menú principal, categorías) a
botones — eso es un follow-up aparte. El agente seguirá usando `mostrar_opciones` de forma
oportunista, según las reglas ya añadidas al prompt.

## Objetivo
La herramienta `mostrar_opciones` ya **envía** botones/listas al cliente. Pero cuando el cliente **toca**
una opción, el CRM emite un evento `message.received` con `message.type == "interactive"` y un
`message.selection.id` (doc §2.4), y el webhook de entrada lo **descarta**:

```python
# app/api/v1/crm.py:196-199 (estado actual)
text = (event.message.text or "").strip()
if event.message.type != "text" or not text:
    logger.info("crm_unsupported_message", msg_type=event.message.type)
    return {"status": "ignored"}
```

Resultado: el menú es un callejón sin salida. El cliente toca "Ver fotos" y recibe **silencio**. Este es
el blocker conocido ("webhook discards list replies") y deja `mostrar_opciones` no funcional en el
regreso.

- **Usuario:** el cliente B2B de IMPRIMIR en WhatsApp; y Valentina, que necesita recibir la elección
  para continuar el flujo.
- **Éxito:** un cliente que toca una opción recibe, en el mismo turno, la acción correspondiente
  (p. ej. el material del producto) — no silencio. El agente rutea por `selection.id`, nunca por el
  texto del título (doc §8.4).

**Fuera de alcance:** conversión de los formatos de texto del prompt a botones; validación automática
de los POST `/media` y `/interactive` (mandan mensajes reales a clientes — verificación manual del
equipo).

## Stack técnico
Python + FastAPI, LangGraph / LangChain (langchain 1.0.5, langgraph-prebuilt 1.0.2), Pydantic v2,
structlog, pytest. Versiones exactas en `uv.lock`.

## Comandos
```
Test (foco):   uv run pytest tests/unit/test_crm_webhook.py
Test (todo):   uv run pytest
Lint:          make lint          # ruff check .
Typecheck:     make typecheck     # uv run pyright
Check:         make check         # lint + typecheck
Dev:           make dev           # hot reload, puerto 8000
```

## Estructura del proyecto (áreas relevantes)
```
app/schemas/crm.py                  → modelos Pydantic del evento message.received (agregar `selection`)
app/api/v1/crm.py                   → webhook de entrada receive_crm_event (cambiar el guard de descarte)
app/core/langgraph/tools/crm.py     → mostrar_opciones (YA implementada; no cambia)
app/core/prompts/imprimir.md        → reglas "Opciones tocables" (YA añadidas; reforzar ruteo por id si hace falta)
tests/unit/test_crm_webhook.py      → pruebas del webhook (agregar caso interactivo)
```

## Estilo de código
Un ejemplo vale más que tres párrafos. Patrón objetivo (imports arriba, tipos, early-return, structlog
en snake_case sin f-strings):

```python
# app/schemas/crm.py — nuevo modelo + campo
class CrmSelection(BaseModel):
    """La opción que el cliente tocó (botón o fila de lista)."""
    id: str
    title: Optional[str] = None


class CrmMessage(BaseModel):
    id: Optional[int] = None
    type: str = "text"
    text: Optional[str] = None
    timestamp: Optional[str] = None
    selection: Optional[CrmSelection] = None   # presente solo cuando type == "interactive"
```

```python
# app/api/v1/crm.py — reemplaza el guard de solo-texto
sel = event.message.selection
if event.message.type == "interactive" and sel and sel.id:
    # Ruteo por id, NO por el título: los títulos se recortan (20/24) y se repiten entre menús.
    text = f"[selección: {sel.id}] {sel.title or ''}".strip()
elif event.message.type == "text":
    text = (event.message.text or "").strip()
else:
    logger.info("crm_unsupported_message", msg_type=event.message.type)
    return {"status": "ignored"}

if not text:
    logger.info("crm_empty_message", msg_type=event.message.type)
    return {"status": "ignored"}
```

Convenciones: eventos structlog en `snake_case` (kwargs, nunca f-strings en el evento); imports al tope;
`async` en I/O; `HTTPException` para errores esperados; type hints en todas las firmas; Pydantic sobre
dicts crudos.

## Estrategia de pruebas
- **Framework:** pytest. **Ubicación:** `tests/unit/test_crm_webhook.py` (ya existe).
- **Caso nuevo (feliz):** POST de un `message.received` con `message.type = "interactive"` y
  `selection.id = "fotos:CM_00015"`, con `imprimir_agent.get_response` y el gateway mockeados. Aserta:
  1. NO se ignora (no cae en `crm_unsupported_message`).
  2. El agente se invoca con un contenido que **contiene** el `selection.id`.
  3. El `title` NO se usa como clave de ruteo (cambiar el title no cambia lo que rutea el agente).
- **Regresión:** un `type` desconocido **sin** `selection` sigue devolviendo `{"status": "ignored"}`.
- **Dedupe:** un interactive con `id == null` usa el fallback `conversation_id:timestamp` y no rompe.
- **Nivel:** unitario (webhook), con agente + gateway mockeados. No se tocan endpoints reales.
- **Gate:** `make check` limpio antes de dar por terminado.

## Límites
- **Always:** rutear por `selection.id`, nunca por el texto; correr `uv run pytest
  tests/unit/test_crm_webhook.py` + `make check` antes de cerrar; imports arriba; logs en snake_case;
  mantener el guard de handoff (`event.handoff.open` → silencio).
- **Ask first:** confirmar la forma exacta del payload `selection` con el equipo del CRM; decidir si el
  `selection.id` llega al agente vía `content` (propuesta) o vía `metadata`; cualquier cambio a los
  formatos de menú del prompt (eso es el follow-up de "menu conversion").
- **Never:** POSTear a `/media` o `/interactive` contra una conversación real en pruebas automáticas
  (manda material a un cliente); commitear tokens; devolver 200 ante payload no parseable (rompe la
  visibilidad — ver el comentario existente en `receive_crm_event`); quitar tests que fallan sin
  aprobación.

## Criterios de éxito (específicos y testables)
1. Un `message.received` con `type == "interactive"` y `selection.id` **NO** se descarta: el agente se
   invoca. *(test unitario)*
2. El contenido que recibe el agente **contiene** el `selection.id` textual (p. ej. `fotos:CM_00015`),
   de modo que el modelo pueda rutear por id. *(test unitario)*
3. Eventos sin `type == "text"` y sin `selection` válido siguen devolviendo `{"status":"ignored"}`.
   *(test unitario)*
4. `make check` (ruff + pyright) pasa limpio.
5. **Verificación manual (equipo):** en una conversación real de WhatsApp, tocar un botón enviado por
   `mostrar_opciones` produce respuesta del agente en el mismo turno (no silencio).

## Preguntas abiertas
1. **Forma del payload `selection`.** ¿Es `message.selection.id` / `.title` como en el doc §2.4?
   Confirmar contra un evento real — el log `crm_raw_payload` ya vuelca el body entrante.
2. **¿`content` o `metadata`?** La propuesta mete el `selection.id` en el `content` del mensaje (lo más
   simple, y coincide con "llega como un mensaje entrante normal"). Alternativa: pasarlo por `metadata`
   para no ensuciar el historial. Decisión pendiente.
3. **Dedupe en interactivos.** ¿El CRM manda `timestamp` en los eventos interactivos? Si `message.id`
   viene null, el fallback `conversation_id:timestamp` debe seguir siendo único por toque.
4. **Follow-up.** ¿Se hará luego la conversión de los menús de texto a botones (la opción "Enabler +
   menu conversion")? Fuera de alcance en este spec.
