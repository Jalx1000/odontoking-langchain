# IMPRIMIR — Mapeo catálogo ↔ agente ↔ CRM

> Documento vivo. Reconcilia la documentación de negocio que envió IMPRIMIR
> (carpeta `02.agentes/Imprimir/`, correo de Marcelo Barea del 5-sep-2026) con la
> configuración del **agente** (`app/core/prompts/imprimir.md`, `app/core/langgraph/tools/crm.py`)
> y el **CRM** (productos, pipelines, usuarios, tags).
>
> **Estado:** análisis/mapeo. La implementación está **bloqueada** por las decisiones de §0.
> Última actualización: 2026-09-08.

---

## 0. Decisiones pendientes (BLOQUEANTES — nada de código hasta resolverlas)

1. **Catálogo: ¿reemplazo o extensión?** El catálogo actual del agente (Bolsa Pouch,
   Flow Pack, Sachet, Almohada, Wicket, Sello Lateral · Etiquetas · Tapas 1881) **no
   coincide** con los productos que IMPRIMIR documentó (Magia Verde, Stretch Film, Hules).
   ¿Se reemplaza, se extiende, o conviven? → define todo lo demás.
2. **`product_id` en el CRM.** ¿Los productos de las fichas ya están cargados en Krayin?
   Se necesitan sus ids reales para `_PRODUCT_IDS` y para que `crear_quote` linkee líneas.
3. **Ruteo por asesor.** Hoy el owner de lead/quote está hardcodeado a `user_id = 1`.
   Los documentos definen una **matriz asesor × (categoría + ciudad)**. ¿Se implementa
   ruteo? Si sí, hace falta el `user_id` de cada asesor.
4. **Faltantes de negocio:** ficha de **Hules** (mencionado, sin ficha) y las **2 bolsas**
   que Marcelo dijo enviar en un correo posterior. Confirmar si ya llegaron.

---

## 1. Inventario de la carpeta `02.agentes/Imprimir/`

| Archivo | Tipo | Alimenta |
|---|---|---|
| `Informacion para CRM - Sep 2026.docx` / `(1)` / `(2)` + `(2).md` | Intake de negocio | CRM (usuarios, pipelines, tags) + agente (catálogo, ruteo, MOQ) |
| `FT BOLSA_90X110 IMPRIMIR v01.pdf` | Ficha técnica Magia Verde | catálogo agente + producto CRM |
| `Ficha_Tecnica_Bolsa_60x63_35L_IMPRIMIR V01.pdf` | Ficha técnica Magia Verde | ídem |
| `FT BOLSA_78x95_75L IMPRIMIR v01.pdf` | Ficha técnica Magia Verde | ídem |
| `FT BOLSA_65X80_50L IMPRIMIR v01.pdf` | Ficha técnica Magia Verde | ídem |
| `Ficha_Tecnica_Bolsa_100x120_200L_IMPRIMIR V01.pdf` | Ficha técnica Magia Verde | ídem |
| `FT STRETCH FILM AUTOMATICO de 23mm-15Kg _ IMPRIMIR.pdf` | Ficha técnica Stretch Film | ídem |
| `FT STRETCH FILM MANUAL de 23mm-4Kg _ IMPRIMIR.pdf` | Ficha técnica Stretch Film | ídem |

> Hay **3 versiones** del docx; el correo del 5-sep dice "descarta el anterior".
> Se tomó `(2).md` como vigente. Si difieren, convertir y comparar las otras dos.

---

## 2. Verdad de negocio (según los documentos)

### 2.1 Categorías reales (10)

Extrusión · Impresión · Clichés · Laminación · Corte · Tapas · Inyección · Reciclaje ·
**Masivos → Magia Verde** (bolsas basureras) · **Embalajes → Stretch Film + Hules**.

> El agente comercial hoy solo cotiza sobre productos concretos; las categorías con
> ficha y volumen conocido son **Magia Verde** y **Stretch Film** (Hules pendiente).

### 2.2 Productos con ficha técnica

| Producto | Categoría | Variante (medida / capacidad) | Unidad de venta | MOQ | `product_id` CRM | Ficha |
|---|---|---|---|---|---|---|
| Magia Verde (bolsa de desechos, 100% reciclado, gris) | Masivos | 60×63 cm / 35 L | paquete | **10 paquetes** | ❓ pendiente | `Ficha_Tecnica_Bolsa_60x63_35L` |
| Magia Verde | Masivos | 65×80 cm / 50 L | paquete | 10 paquetes | ❓ | `FT BOLSA_65X80_50L` |
| Magia Verde | Masivos | 78×95 cm / 75 L | paquete | 10 paquetes | ❓ | `FT BOLSA_78x95_75L` |
| Magia Verde | Masivos | 90×110 cm / 140 L (espesor 35 µm) | paquete | 10 paquetes | ❓ | `FT BOLSA_90X110` |
| Magia Verde | Masivos | 100×120 cm / 200 L | paquete | 10 paquetes | ❓ | `Ficha_Tecnica_Bolsa_100x120_200L` |
| Stretch Film Automático | Embalajes | rollo 500 mm, 0,023 mm, 15,8 Kg, aplicación máquina | Kg | ❓ | ❓ | `FT STRETCH FILM AUTOMATICO 23mm-15Kg` |
| Stretch Film Manual | Embalajes | rollo 23 mm, 4 Kg, aplicación manual | Kg | ❓ | ❓ | `FT STRETCH FILM MANUAL 23mm-4Kg` |
| Hules | Embalajes | *(sin ficha aún)* | ❓ | ❓ | ❓ | — |

**Presentación Magia Verde (de la ficha 90×110):** envase primario = 10 bolsas;
paquete = 4 jabas = **400 bolsas**. → MOQ 10 paquetes ≈ 4.000 bolsas.
*(Presentación y espesor exactos por variante: extraer de cada ficha en la fase de carga.)*

### 2.3 Temperatura del lead

frío · tibio · caliente → **coincide** con el agente (`_TAG_IDS`).

### 2.4 Asesores (ruteo por categoría + ciudad)

| Asesor | Categoría(s) | Ciudad | `user_id` CRM |
|---|---|---|---|
| Bulmaro Vaca | Stretch Film | Bolivia (todo) | ❓ |
| Eduardo Lujan | Magia Verde | Bolivia (todo) | ❓ |
| Fabio Sandoval | Hules | Santa Cruz | ❓ |
| Mauricio Garces | Stretch Film / Magia Verde / Hules | Cochabamba | ❓ |
| Gabriela Marconi | Stretch Film / Magia Verde / Hules | La Paz | ❓ |
| Mariano Pinell | Stretch Film / Magia Verde / Hules | Santa Cruz | ❓ |

Contactos de referencia (recepción/visualización de prospectos): Marcelo Barea,
Bulmaro Vaca (692-26171), Eduardo Lujan (787-40409), Mauricio Garces (799-68441),
Gabriela Marconi, Mariano Pinell (75599756). Horario general 08:00–19:00.

---

## 3. Estado actual del agente (para contraste)

- **Catálogo** (`imprimir.md`): Envases Flexibles (Bolsa Pouch, Flow Pack, Sachet,
  Almohada, Wicket, Sello Lateral) · Etiquetas (Sleeve, Roll Feed) · Tapas Plásticas
  (1881 Short Finish) · Películas y Films (sin productos) · Productos Publicitarios (sin productos).
- **`_PRODUCT_IDS`** (`tools/crm.py`): pouch=12, flow pack=6, sachet=8, almohada=9,
  wicket=11, sello lateral=7, etiqueta sleeve=15, roll feed=16, tapa 1881=14.
- **Tags temperatura:** caliente=1, tibio=2, frio=3.
- **Owner de lead/quote:** `_OWNER_USER_ID = 1` (único, sin ruteo).
- **Pipelines por ciudad:** SC=1, Potosí=4, Oruro=6, La Paz=7, Cbba=8, Sucre=9, Sin ciudad=10.

---

## 4. Gap analysis — AGENTE

| Área | Actual | Documentos | Gap |
|---|---|---|---|
| Catálogo | Pouch/Flow Pack/… + Etiquetas + Tapas | Magia Verde, Stretch Film, Hules | 🔴 No coinciden (§0.1) |
| `_PRODUCT_IDS` | ids 6–16 de la línea vieja | 7 fichas nuevas + Hules | 🔴 Sin mapear → `crear_quote` no linkea `product_id` |
| MOQ | sección genérica | Magia Verde = 10 paquetes | 🟠 Falta regla dura por producto |
| Preguntas de calificación | por categorías viejas | bolsa: litros/medida + cantidad en paquetes; film: auto vs manual + Kg | 🟠 Faltan |
| Unidad de venta | implícita (unidades) | paquete (bolsa) / Kg (film) | 🟠 El agente debe hablar en la unidad correcta |
| Temperaturas | caliente/tibio/frio | igual | ✅ |

## 5. Gap analysis — CRM

| Pieza | Actual | Documentos | Gap |
|---|---|---|---|
| Productos (`product_id`) | ids de línea vieja | 7 fichas (+Hules) | 🔴 Crear/confirmar productos y sus ids, luego remapear |
| Pipelines/ciudad | SC, Potosí, Oruro, La Paz, Cbba, Sucre, Sin ciudad | asesores solo en Bolivia, SC, Cbba, La Paz | 🟠 Potosí/Oruro/Sucre sin asesor en el intake |
| Usuarios/asesores (`user_id`) | hardcodeado = 1 | matriz asesor × (categoría+ciudad) | 🔴 Falta mapear cada asesor a su `user_id`; decidir ruteo |
| Tags (temperatura) | caliente=1/tibio=2/frio=3 | frío/tibio/caliente | ✅ |

---

## 6. Plan de reconciliación (condicionado a §0)

**Fase 0 — Decisiones (§0).** Resolver reemplazo vs. extensión, ids de producto, ruteo, faltantes.

**Fase 1 — CRM como fuente de verdad.**
1. Cargar/confirmar los productos (Magia Verde ×5, Stretch Film ×2, Hules) con `product_id`.
2. Confirmar `user_id` de cada asesor y su matriz categoría×ciudad.
3. Confirmar que Potosí/Oruro/Sucre tengan responsable (o definir fallback).

**Fase 2 — Agente (código + prompt).**
1. Actualizar el catálogo de `imprimir.md` (según decisión §0.1) con Magia Verde / Stretch Film / Hules, sus variantes y unidades de venta.
2. Remapear `_PRODUCT_IDS` a los ids reales del CRM.
3. Codificar MOQ Magia Verde (10 paquetes) como regla dura.
4. Preguntas de calificación nuevas: bolsa (litros + cantidad en paquetes), film (auto/manual + Kg).
5. (Si aplica §0.3) Ruteo de owner por categoría+ciudad en `_create_lead`/`crear_quote` en vez de `_OWNER_USER_ID` fijo.

**Fase 3 — Verificación.** Tests de unidad para el nuevo catálogo/MOQ/mapa de productos; prueba end-to-end de cotización de Magia Verde y Stretch Film contra el CRM.

---

## Fuentes

- `02.agentes/Imprimir/Informacion para CRM - Sep 2026 (2).md` (intake).
- Fichas técnicas PDF en `02.agentes/Imprimir/`.
- Correo Marcelo Barea (imprimir.com.bo) ↔ Danitza Cuellar / Andrés González (sofopolis.com), 17-ago a 5-sep-2026.
- Config del agente: `app/core/prompts/imprimir.md`, `app/core/langgraph/tools/crm.py`.
