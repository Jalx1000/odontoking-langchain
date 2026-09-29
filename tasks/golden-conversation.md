# Charla dorada — oráculo de regresión (Kohlberg / Sofía)

> Uso: correr este guion contra el agente ANTES de cualquier cambio (baseline) y DESPUÉS
> de cada fase (C1→C6, cola durable). Las respuestas no tienen que ser idénticas al texto
> (el LLM no es determinista), pero **todos los invariantes deben cumplirse**. Para reducir
> ruido, correr con `temperature=0`.

## Baseline de tokens (de `scripts/token_report.py`, 2026-09-27)

| Métrica | Baseline | Objetivo post-optimización |
|---|---|---|
| System prompt estático | 6,078 tok | ↓ (C2) |
| Tools (8 schemas) | 2,359 tok | ↓ (C3 + quitar think −175) |
| **Prefijo estático por llamada** | **8,437 tok** | ↓ y **cacheado** (C1) |
| Prefijo ensamblado | 8,485 tok | — |

Además, en Langfuse/logs (evento `kohlberg_llm_tokens`): confirmar `cached_tokens > 0`
en llamadas sucesivas dentro de una misma ventana (prueba de que C1 pega).

## Guion (happy path)

| # | Cliente dice | Invariantes que la respuesta DEBE cumplir |
|---|---|---|
| 1 | "Hola" | Saluda; si no hay ciudad, pide **la ciudad PRIMERO** con **menú `N.-)`** (7 ciudades). No pide nombre/edad antes que la ciudad. |
| 2 | (elige) "Santa Cruz" | Llama `actualizar_pedido(ciudad=...)` → el lead sale de Tarija/Daniel. Ofrece vinos/promos de esa ciudad (de `get_promos`). |
| 3 | "Me llamo Ana, tengo 34" | No re-pregunta ciudad. Toma nombre+edad (viajan con el próximo PUT; **no** un PUT suelto por c/u — cadencia híbrida). |
| 4 | "Quiero 2 Malbec" | `actualizar_pedido` con la lista COMPLETA de vinos + total del catálogo (no inventado). |
| 5 | "también 1 Cabernet" | `actualizar_pedido` re-manda los **2** vinos (nunca pierde el 1º). |
| 6 | "nada más" | **Paso 6**: confirma con **detalle + total**; una sola confirmación. Llama `registrar_pedido(es_pedido_confirmado=true)`. |
| 7 | (tras confirmar) | **Paso 7**: "lo que vas a recoger" + **sucursal** (dirección/horarios/mapa de `get_sucursales`). Pedido queda en **No atendido**. |

## Ramas de borde (verifican C4 trimming y C5 híbrido)

| Caso | Cliente | Invariante |
|---|---|---|
| Corrección de ciudad | "Tarija… no, mejor Sucre" | El lead se mueve a Sucre/asesor; no pierde vinos ya cargados. |
| Abandono a mitad | da ciudad + 1 vino y no confirma | El lead #N ya tiene ciudad + ese vino (registro en vivo). |
| Fuera de horario | pide recoger cuando la sucursal está cerrada | Responde con horarios; ofrece dentro de horario / próximo día hábil. No inventa horarios. |
| Derivación | "quiero hablar con una persona" | Mensaje breve avisando que **un asesor lo contactará dentro del horario de atención**; recién después `derivar_a_asesor`. No muestra el teléfono de la sucursal como si fuera el canal. |
| Menú tocable | toca una opción del menú `N.-)` | El tap (interactive) se procesa como texto = la etiqueta elegida. |

## Cómo capturar

1. Baseline: correr el guion contra el agente actual (rama `kohlberg-v2`), pegar las
   respuestas abajo, marcar cada invariante ✅/❌.
2. Tras cada fase: repetir; comparar invariantes (no texto). Si un invariante pasa a ❌,
   la fase regresionó → `git revert` de ese commit.

### Transcripción baseline
_(pegar aquí al correr)_

### Transcripción post-Fase-N
_(pegar aquí al correr)_
