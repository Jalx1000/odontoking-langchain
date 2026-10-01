# Spec: Flujo de venta — Bolsas Magia Verde (Valentina / IMPRIMIR)

> Estado: **Fase 1 — Specify, APROBADA (2026-10-01)**. Decisiones abiertas cerradas abajo.
> Como las tres confirman el comportamiento vigente, NO hay fase de implementación: esta
> spec documenta el flujo ya implementado y queda como contrato para futuras ediciones.
> Fuente de verdad del flujo conversacional de bolsas Magia Verde. Si el prompt y esta
> spec divergen, se arregla el que esté mal — no se deja la divergencia.

## Objective

Documentar, como fuente de verdad única y verificable, el flujo completo de venta de
**Bolsas Magia Verde (SKU `CM_00002`)** del agente de WhatsApp *Valentina* (IMPRIMIR),
de modo que:

- Cada cambio al prompt (`app/core/prompts/imprimir.md`) se pueda contrastar contra un
  contrato explícito en vez de parchearse por síntoma.
- Las regresiones recurrentes (doble saludo, imagen mal ubicada, menú con cuerpo
  incorrecto, mención de precio) tengan criterios de aceptación concretos.

**Usuario final:** cliente B2B/consumidor que escribe por WhatsApp pidiendo bolsas.
**Usuario del spec:** quien edita el prompt o escribe tests del flujo.
**Éxito:** un cliente puede ir de "hola" a "cotización registrada + derivado a asesor"
sin que el agente diga un precio, salude dos veces, ni pida datos que ya dio.

## Tech Stack

- **Prompt:** `app/core/prompts/imprimir.md`, renderizado con
  `str.format(current_datetime=...)`. **No puede haber llaves `{}` literales** salvo
  `{current_datetime}` (cualquier otra `{...}` rompe el render con `KeyError`).
- **Grafo:** LangGraph `StateGraph` (nodos `chat` / `tool_call`), checkpointer
  `AsyncPostgresSaver` keyed por `thread_id = wa_id`.
- **CRM:** Krayin en `https://imprimir.sofopolis.com`, vía `app/core/langgraph/tools/crm.py`.
- **Tools del flujo:** `mostrar_opciones`, `enviar_material`, `precio_producto`,
  `register_cotizacion`, `crear_cotizacion`, `derivar_a_asesor`, `quien_atiende`,
  `buscar_productos`, `ficha_producto`.

## Commands

```bash
# Render del prompt + chequeo de llaves sueltas (solo {current_datetime} permitido)
uv run python -c "import re; raw=open('app/core/prompts/imprimir.md').read(); p=raw.format(current_datetime='2026-10-01 12:00'); print('stray:', re.findall(r'{[^}]*}', p)); print('len:', len(p))"

# Tests del flujo (grafo + quotes/tools)
uv run pytest tests/unit/test_imprimir_graph.py tests/unit/tools/test_imprimir_quotes.py -q

# Lint / typecheck
make check
```

## Project Structure

```
app/core/prompts/imprimir.md              → prompt del agente (el flujo vive acá)
app/core/langgraph/tools/crm.py           → tools CRM (enviar_material, crear_cotizacion,
                                            register_cotizacion, derivar_a_asesor, ruteo
                                            ciudad→pipeline, ciudad→zona)
tests/unit/test_imprimir_graph.py         → tests del grafo del agente
tests/unit/tools/test_imprimir_quotes.py  → tests de cotización/ruteo/enviar_material
docs/specs/bolsas-magia-verde-flow.md     → esta spec
```

## Code Style (convenciones del prompt)

- Español, trato de **usted**, breve, sin emojis fuera de los que ya trae un menú.
- Toda elección concreta va por `mostrar_opciones` (botones), **nunca** como texto
  numerado. **Excepción:** la CANTIDAD es texto libre.
- En menús de variante, el `id` del botón = valor EXACTO del eje que espera
  `precio_producto`; el `title` es corto (≤ 24 chars) y lindo. Se pasa el `id`, no el title.
- Placeholders en el prompt con `<...>`, nunca con `{...}`.

Ejemplo de un paso bien formado (menú de tamaño con imagen previa):

```text
# turno del agente, en una sola tanda:
enviar_material("CM_00002", "imagen", 1)            # imagen primero, out-of-band
mostrar_opciones(
  cuerpo="Nuestras bolsas Magia Verde están fabricadas con pellets reciclados, "
         "con menor impacto ambiental 🌱. Multiuso. ¿Qué tamaño o medida necesita?",
  opciones=[
    {"id": "35 L",  "title": "35 L (60x63)"},
    {"id": "50 L",  "title": "50 L (65x80)"},
    {"id": "75 L",  "title": "75 L (78x95)"},
    {"id": "140 L", "title": "140 L (90x110)"},
    {"id": "200 L", "title": "200 L (100x120) XXL"},
  ],
)
```

## Especificación del flujo (contrato / máquina de estados)

### Datos maestros

- **SKU:** `CM_00002`.
- **Precio = Tamaño × Canal × Zona** (interno; nunca se dice al cliente).
- **Tamaños** (eje `Tamaño`, id → title): `35 L`→"35 L (60x63)", `50 L`→"50 L (65x80)",
  `75 L`→"75 L (78x95)", `140 L`→"140 L (90x110)", `200 L`→"200 L (100x120) XXL".
- **Canales** (eje `Canal`, id): `HOGAR`, `TRADICIONAL`, `HORECA`, `EMPRESARIAL`,
  `DISTRIBUIDOR`.
- **Zona** (derivada de la ciudad): Santa Cruz → `Santa Cruz`; La Paz / Cochabamba /
  otra ciudad → `Interior`.
- **Ciudad → pipeline** (`_CITY_PIPELINE_IDS`, fuente `crm.py`): Santa Cruz 1, Potosí 4,
  Oruro 6, La Paz 7, Cochabamba 8, Sucre 9; ciudad desconocida/vacía → **10 (Sin ciudad)**.
- **Unidad de cantidad:** PAQUETES (1 paquete = 10 bolsas).
- **Mínimo:** lo manda `precio_producto` (`cumple_minimo` / `minimo_texto`). El "10
  paquetes" del prompt es solo referencia interna.

### Pasos

**PASO 1 — Saludo + producto + ciudad.** El saludo `"¡Hola! Gracias por escribirnos 👋"`
se dice **exactamente una vez** en toda la conversación.
- *CAMINO A* (el primer mensaje ya menciona bolsas): el primer mensaje = saludo +
  LÍNEA de bolsas ("Nuestras bolsas Magia Verde son de material reciclado, resistente y
  multiuso.") + pregunta de ciudad, en el cuerpo de un `mostrar_opciones` de ciudad.
- *CAMINO B* (saludo suelto): primer mensaje = saludo + menú de producto. Al elegir
  bolsas, el siguiente mensaje usa la LÍNEA de bolsas **sola** (sin saludo, sin "Somos
  Imprimir…") + pregunta de ciudad. **Nunca re-saludar.**
- Opciones de ciudad: Santa Cruz, La Paz, Cochabamba, Otra ciudad. "Otra ciudad" →
  preguntar cuál y guardar el texto tal cual.

**PASO 2 — Ciudad.** Para **bolsas no se manda nada acá** (ni imagen ni línea de info):
se pasa directo al menú de canal del PASO 3. (La imagen y la línea ambiental van con el
menú de tamaño.) La ciudad define Zona (precio) y asesor.

**PASO 3 — Canal → (tamaño) → cantidad.**
- **Menú de canal:** `mostrar_opciones` con cuerpo **solo** `"¿Para qué uso la necesita?"`
  (nada de línea de producto ni "en 5 tamaños"). Botones:
  "Para mi casa"→`HOGAR`, "Tienda / mercado"→`TRADICIONAL`,
  "Restaurante / hotel"→`HORECA`, "Empresa / oficina"→`EMPRESARIAL`,
  "Quiero revender"→`DISTRIBUIDOR`.
- **Menú de tamaño:** justo antes de mostrarlo, `enviar_material("CM_00002","imagen",1)`
  (una sola vez). Cuerpo = línea ambiental + "¿Qué tamaño o medida necesita?". Sin imagen
  disponible → seguir sin ella, sin comentarlo.
- **Cantidad:** texto libre.
- Ramas por canal:
  - `HOGAR` → preguntar **cantidad primero**. `<10` paquetes → **no se cotiza**: derivar al
    supermercado de su ciudad (Santa Cruz: Hipermaxi/Amarket/IC Norte/Fidalga/Tía/Makro;
    La Paz: Hipermaxi/Fidalga; Cochabamba: Hipermaxi; otra: venta directa por volumen).
    `≥10` → cotizar como `TRADICIONAL` (HOGAR no es cotizable): mostrar menú de tamaño,
    confirmar cantidad, seguir a PASO 4.
  - `DISTRIBUIDOR` → no se cotiza: pedir nombre completo + Carnet de Identidad y derivar.
  - `TRADICIONAL` / `HORECA` / `EMPRESARIAL` → menú de tamaño + cantidad, validar con
    `precio_producto` (Tamaño + Canal + Zona).

**PASO 4 — Datos.** Pedir **solo identidad**: obligatorios `Nombre/Razón Social` (un nombre
de persona basta) y `NIT/CI`; `Correo` y `Dirección` **opcionales** (no re-preguntar).
Nunca re-preguntar producto/tamaño/cantidad/ciudad (ya están).

**PASO 5 — Cierre. Cuatro acciones, EN ORDEN:**
1. `register_cotizacion(...)` con `ciudad` real → además rutea el lead al pipeline de la
   ciudad y llena el atributo `ciudad`.
2. `crear_cotizacion(sku, cantidad, criterios COMPLETO, ciudad)` — `criterios` = todos los
   ejes validados (Tamaño, Canal, Zona). HOGAR≥10 va con `Canal=TRADICIONAL`.
3. Texto de cierre con el asesor (`nombre` + `horario` de la respuesta). **Nunca** el
   teléfono del asesor, aunque venga con valor y aunque el cliente lo pida.
4. `derivar_a_asesor(sku, ciudad)`. Es el último mensaje del turno.

## Testing Strategy

- **Framework:** pytest (`uv run pytest`).
- **Ubicación:** `tests/unit/test_imprimir_graph.py` (comportamiento del grafo),
  `tests/unit/tools/test_imprimir_quotes.py` (ruteo ciudad→pipeline, ciudad→zona,
  `enviar_material` con/ sin `criterios`, `register_cotizacion`).
- **Gate de render:** el chequeo de llaves sueltas debe devolver `[]` (solo
  `{current_datetime}` permitido) — correrlo ante cualquier edición del prompt.
- **Cobertura mínima esperada:** cada criterio de éxito de abajo tiene al menos un test o
  un chequeo de render asociado. Hoy: 52 tests en verde.

## Boundaries

- **Always:** correr el gate de render + los 52 tests antes de commitear; mantener el
  `id` de los menús = valor exacto del eje; mandar la imagen de bolsas solo junto al menú
  de tamaño; commit messages terminando en `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Ask first:** cambiar los obligatorios del PASO 4 (p. ej. volver Mail/Dirección
  obligatorios); cambiar mínimos; agregar/quitar un canal; mover la imagen a otro paso;
  tocar `_CITY_PIPELINE_IDS` o el mapeo de zonas.
- **Never:** decir un precio/monto al cliente (REGLA 1); compartir el teléfono del asesor;
  usar "góndola" / "no se cotiza por chat" / "bolsa virgen"; saludar dos veces; meter
  llaves `{}` literales en el prompt; reintroducir fichas técnicas.

## Success Criteria (testable)

1. En toda la conversación, `"¡Hola! Gracias por escribirnos"` aparece **como apertura de
   mensaje una sola vez** (ni CAMINO A ni CAMINO B lo repiten).
2. El cuerpo del menú de canal es exactamente `"¿Para qué uso la necesita?"` (no incluye
   la línea de producto ni "en 5 tamaños").
3. La imagen de bolsas se envía **solo** inmediatamente antes del menú de tamaño, y una
   sola vez; no se envía en el PASO 2 ni en HOGAR<10 ni en DISTRIBUIDOR.
4. El agente nunca emite un monto/precio para bolsas.
5. En HOGAR con `<10` paquetes el agente deriva al supermercado correcto por ciudad y no
   pide tamaño ni datos; con `≥10` cotiza como TRADICIONAL.
6. DISTRIBUIDOR pide nombre + CI y deriva, sin tamaño ni cantidad.
7. El PASO 4 pide solo Nombre/Razón Social + NIT/CI como obligatorios; no re-pregunta
   datos ya dados.
8. El cierre ejecuta register_cotizacion → crear_cotizacion → texto → derivar_a_asesor, en
   ese orden, con `ciudad` real; nunca comparte el teléfono del asesor.
9. La ciudad rutea al pipeline correcto (SC 1 / Potosí 4 / Oruro 6 / LP 7 / Cbba 8 /
   Sucre 9 / desconocida 10) y a la Zona correcta (SC → Santa Cruz; resto → Interior).
10. El render del prompt no deja llaves sueltas (`[]`), y los 52 tests pasan.

## Decisiones resueltas (2026-10-01)

1. **Mail + Dirección** en el PASO 4 → **OPCIONALES** (se mantiene lo actual). Obligatorios
   solo Nombre/Razón Social + NIT/CI; Correo y Dirección se muestran como "(opcional)" y no
   se re-preguntan.
2. **Tarija** → **"Otra ciudad" → pipeline 10 (Sin ciudad)**. No se agrega a
   `_CITY_PIPELINE_IDS` ni al menú de ciudad. Sin cambios de código.
3. **Entrega de bolsas** → **según cantidad y producto; sin cambios**. Se mantiene la
   lógica actual de "ENTREGA Y ENVÍOS" del prompt. No se modifica.

> Las tres decisiones confirman el comportamiento ya implementado: esta spec documenta el
> flujo vigente, no requiere cambios de código. Queda como contrato para futuras ediciones.
```
