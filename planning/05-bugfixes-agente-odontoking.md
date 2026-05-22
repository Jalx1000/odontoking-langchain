# Bugfixes — Agente Odontoking (WhatsApp)

**Fecha:** 2026-05-22  
**Branch:** `master`  
**Detectado vía:** conversación real con paciente (Gustavo Adomeit)

---

## Bugs corregidos

### Bug 1 — Día de semana incorrecto al mostrar fechas disponibles

**Síntoma:** El agente mostró "Sábado 22/05" cuando el 22 de mayo de 2026 es viernes.
El paciente tuvo que corregir: "Mañana sábado 23 de mayo".

**Causa raíz (dos puntos):**

1. `datetime.now()` sin timezone — el servidor corre en UTC; Bolivia es UTC-4.
   El prompt recibía la hora del servidor, no la hora local de La Paz.
2. `strftime("%A")` devuelve el día en **inglés** (`"Friday"`). El LLM debe
   traducirlo y simultáneamente usarlo como referencia → cometía errores.
3. `get_doctor_schedule` retornaba `{"date": "2026-05-22"}` sin el nombre del día.
   El LLM calculaba el día de la semana desde la fecha, tarea débil para LLMs.

**Fix aplicado:**

| Archivo | Cambio |
|---------|--------|
| `app/core/langgraph/odontoking_graph.py` | `datetime.now()` → `datetime.now(ZoneInfo("America/La_Paz"))` |
| `app/core/langgraph/odontoking_graph.py` | Formato de fecha en español: `"viernes 22 mayo 2026 16:24"` (sin `strftime("%A/%B")`) |
| `app/core/langgraph/tools/odontoking.py` | Añadido `day_label` a cada slot de `get_doctor_schedule`: `{"date": "2026-05-22", "day_label": "viernes 22/05", ...}` |

---

### Bug 2 — Mensajes cortados en list/button messages de WhatsApp

**Síntoma:** Al mostrar listas de doctores u horarios (>3 opciones), el cuerpo
del mensaje se cortaba antes de terminar la pregunta al paciente.

**Causa raíz:** `body_text[:1024]` en `build_interactive_payload`. La API de
WhatsApp Cloud permite hasta **4096 caracteres** en el body de mensajes
interactivos; el código estaba usando 1024.

**Fix aplicado:**

| Archivo | Cambio |
|---------|--------|
| `app/services/whatsapp_client.py` | `body_text[:1024]` → `body_text[:4096]` (tanto en `button` como en `list`) |

> Nota: los límites de títulos de botones (20 chars) y filas de lista (24 chars)
> son límites reales de la API de WhatsApp y no se pueden cambiar.

---

### Bug 3 — `max_tokens=2000` podía truncar el JSON de respuesta del agente

**Síntoma:** Con listas largas de horarios o doctores, el JSON `{"mensaje": "..."}` 
podía quedar incompleto, causando error de parseo y respuesta genérica de error.

**Fix aplicado:**

| Archivo | Cambio |
|---------|--------|
| `app/core/langgraph/odontoking_graph.py` | `max_tokens=2000` → `max_tokens=4096` en `ChatOpenAI` |

---

---

### Tests corregidos (Railway CI fallaba con 3 tests)

| Test | Motivo del fallo | Fix |
|------|-----------------|-----|
| `test_broker.py::test_publish_calls_xadd` | Esperaba campos `entry["wa_id"]` y `entry["payload"]` separados; el broker usa `{"data": json}` flat | Actualizado a `json.loads(entry["data"])["wa_id"]` |
| `test_broker.py::test_successful_message_is_acked` | Mock de `xreadgroup` usaba formato `{"wa_id":..., "payload":...}` antiguo | Mock actualizado a `{"data": '{"wa_id":...}'}` |
| `test_whatsapp_client.py::test_body_text_truncated_to_1024` | Nuestro cambio 1024→4096 rompió el assert | Test renombrado a `test_body_text_truncated_to_4096`, body de prueba subido a 5000 chars |
| `test_broker.py::test_failed_message_increments_retry_counter` | Mock con formato viejo (no fallaba pero era inconsistente) | Actualizado a formato `{"data": ...}` |
| `test_broker.py::test_message_moves_to_dlq_after_max_retries` | Ídem | Ídem |

---

## Archivos modificados

```
app/core/langgraph/odontoking_graph.py        — timezone Bolivia + formato fecha ES + max_tokens
app/core/langgraph/tools/odontoking.py       — day_label en get_doctor_schedule
app/services/whatsapp_client.py              — body_text limit 1024 → 4096
tests/unit/test_broker.py                   — mocks actualizados al wire format actual
tests/unit/test_whatsapp_client.py          — límite actualizado a 4096
```
