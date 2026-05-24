# E2E Tests — Conversación completa de agendamiento (Railway)

**Fecha:** 2026-05-24
**Branch:** `master`
**Objetivo:** Validar de extremo a extremo que el agente Odontoking puede agendar citas via WhatsApp, tanto para el propio paciente como para un tercero, corriendo contra el entorno de producción en Railway.

---

## Contexto

El agente responde mensajes de WhatsApp de forma asíncrona: el webhook devuelve 200 inmediatamente y el procesamiento ocurre en background (buffer + LLM + tools). Las respuestas del agente se envían via WhatsApp Cloud API (Meta), no al caller del webhook.

Para testear esto sin interceptar Meta, el harness:
1. POSTea payloads sintéticos al webhook en Railway (mismo formato que Meta envía)
2. Espera el tiempo de buffer + procesamiento
3. Lee la respuesta del agente via Admin API (`GET /api/v1/admin/tenants/odontoking/conversations/{wa_id}`)
4. Envía el siguiente mensaje basándose en lo que respondió el agente
5. Repite hasta que se complete el flujo de agendamiento

---

## Escenarios

### Escenario A — Paciente agenda para sí mismo

El paciente contacta directamente, proporciona sus datos, elige servicio, doctor, fecha/hora y el agente confirma.

**Flujo esperado:**

```
Paciente: "Hola, quiero agendar una cita para una limpieza dental"
Agente:   [pide nombre y datos]
Paciente: "Soy Juan Pérez, 32 años, tengo seguro Alianza"
Agente:   [muestra doctores disponibles]
Paciente: [elige doctor]
Agente:   [muestra horarios disponibles]
Paciente: [elige horario]
Agente:   "✅ Tu cita ha sido agendada para el [fecha] con el Dr. [nombre]. ..."
```

### Escenario B — Tercero agenda para otra persona

El que escribe no es quien asistirá a la cita. Nombre y datos del paciente son distintos al wa_id del contacto.

**Flujo esperado:**

```
Tercero:  "Quiero agendar una cita para mi amigo"
Agente:   [pide nombre del paciente y datos]
Tercero:  "Es para Carlos Mamani, 45 años"
Agente:   [muestra servicios disponibles]
Tercero:  [elige servicio, doctor, horario]
Agente:   "✅ La cita para Carlos Mamani ha sido agendada para el [fecha] con el Dr. [nombre]. ..."
```

---

## Arquitectura del harness

```
tests/e2e_railway/
  __init__.py
  conftest.py          # Railway URL, Admin key, wa_id de prueba, timeouts
  harness.py           # send_message(), wait_for_reply(), get_history(), cleanup()
  test_self_booking.py # Escenario A
  test_third_party.py  # Escenario B
```

### Estrategia de espera por turno

```python
# Después de cada POST al webhook:
await asyncio.sleep(BUFFER_WINDOW_SECONDS + 1.5)  # esperar que el buffer drene

# Luego poll hasta recibir respuesta del agente:
deadline = time.time() + 90  # 90s máximo por turno
while time.time() < deadline:
    history = GET /api/v1/admin/tenants/odontoking/conversations/{wa_id}
    last_msg = history[-1]
    if last_msg["role"] == "assistant" and last_msg["created_at"] > t_send:
        break
    await asyncio.sleep(2)
```

### Payload sintético (formato Meta WhatsApp)

```python
def build_webhook_payload(wa_id: str, text: str, msg_id: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "id": msg_id,          # único por turno para evitar dedup
                        "from": wa_id,
                        "type": "text",
                        "text": {"body": text},
                        "timestamp": str(int(time.time()))
                    }],
                    "contacts": [{"wa_id": wa_id, "profile": {"name": "Test"}}]
                },
                "field": "messages"
            }]
        }]
    }
```

---

## Criterios de éxito (los 3 deben cumplirse)

| # | Criterio | Cómo validarlo |
|---|---|---|
| 1 | `update_crm` tool fue llamado | Extender `_parse_message` en `conversations.py` para exponer `tool_names: list[str]` en el historial |
| 2 | CRM respondió exitosamente | Respuesta real de Odontoking CRM sin error |
| 3 | Agente confirmó la cita en el mensaje final | Último mensaje del agente contiene keyword de confirmación + fecha + nombre del paciente |

---

## Tareas de implementación

### T-1: Extender Admin API para exponer tool_names

**Archivo:** `app/api/admin/conversations.py`

Extender `_parse_message` (o el schema de respuesta) para que cada mensaje del historial incluya:
```json
{
  "role": "assistant",
  "content": "Tu cita ha sido agendada...",
  "tool_names": ["get_services", "get_doctors", "update_crm"],
  "created_at": "2026-05-24T20:00:00Z"
}
```
Esto permite al harness verificar el criterio 1 sin parsear el contenido del mensaje.

### T-2: Escribir el harness base

**Archivo:** `tests/e2e_railway/harness.py`

```python
class ConversationHarness:
    def __init__(self, railway_url: str, admin_key: str, wa_id: str): ...
    async def send(self, text: str) -> None: ...          # POST webhook
    async def wait_reply(self, after: float) -> dict: ... # poll admin API
    async def history(self) -> list[dict]: ...            # GET conversations
    async def cleanup(self) -> None: ...                  # DELETE history
```

### T-3: Escribir conftest.py con fixtures

**Archivo:** `tests/e2e_railway/conftest.py`

Variables requeridas (via env vars):
- `E2E_RAILWAY_URL` — URL base del proyecto en Railway (ej: `https://odontoking-prod.up.railway.app`)
- `E2E_ADMIN_KEY` — valor del header `X-Admin-Key`
- `E2E_TEST_WA_ID` — número de WhatsApp de prueba (ej: `5959100000000`)
- `E2E_VERIFY_TOKEN` — valor de `WHATSAPP_VERIFY_TOKEN` para el tenant

Los tests solo corren cuando `RUN_E2E_RAILWAY=1` está seteado:
```python
pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_E2E_RAILWAY"),
    reason="E2E Railway tests disabled — set RUN_E2E_RAILWAY=1 to run"
)
```

### T-4: Escribir test_self_booking.py (Escenario A)

**Archivo:** `tests/e2e_railway/test_self_booking.py`

El test dirige activamente la conversación leyendo la respuesta del agente para decidir el siguiente mensaje (no es un script fijo). Verifica al final:
- `"update_crm"` en `tool_names` de algún mensaje del historial
- El último mensaje del agente contiene "agendada" o "confirmada" + nombre del paciente de prueba

### T-5: Escribir test_third_party.py (Escenario B)

**Archivo:** `tests/e2e_railway/test_third_party.py`

Igual que T-4 pero inicia con "quiero agendar para otra persona" y usa nombre de paciente diferente al wa_id.

### T-6: Limpieza pre/post test

Usar el endpoint existente:
```
DELETE /api/v1/whatsapp/odontoking/history/{wa_id}
```
Limpia `ChatHistory` + checkpoints de LangGraph del `wa_id` de prueba. Correr en `setup` y `teardown` de cada test.

---

## Requisitos previos (blockers)

| # | Blocker | Responsable |
|---|---|---|
| B-1 | `_parse_message` en `conversations.py` debe exponer `tool_names` | platform-dev |
| B-2 | Definir si usar tenant `odontoking` prod o crear `odontoking-test` — el webhook no valida `X-Hub-Signature-256`, riesgo de seguridad en prod | Decisión del usuario |
| B-3 | `wa_id` de prueba que no interfiera con pacientes reales | Usuario provee |
| B-4 | `E2E_RAILWAY_URL` + `E2E_ADMIN_KEY` disponibles como secrets en CI | DevOps |

---

## Ejecución

```bash
# Correr localmente contra Railway:
E2E_RAILWAY_URL=https://... \
E2E_ADMIN_KEY=... \
E2E_TEST_WA_ID=5959100000000 \
RUN_E2E_RAILWAY=1 \
uv run pytest tests/e2e_railway/ -v

# NO corren con el suite normal:
uv run pytest  # los e2e quedan skipped
```

---

## Definition of Done

- [ ] `T-1`: Admin API expone `tool_names` por mensaje
- [ ] `T-2`: `ConversationHarness` implementado con send/wait/cleanup
- [ ] `T-3`: `conftest.py` con fixtures y gate `RUN_E2E_RAILWAY`
- [ ] `T-4`: Escenario A pasa los 3 criterios de éxito contra Railway
- [ ] `T-5`: Escenario B pasa los 3 criterios de éxito contra Railway
- [ ] `T-6`: Cleanup pre/post en ambos tests
- [ ] Los tests NO corren en `uv run pytest` sin `RUN_E2E_RAILWAY=1`
- [ ] `make lint && make typecheck` pasan

---

## Out of scope

- Mockear la API de Odontoking CRM (se usa real)
- Tests de carga o concurrencia
- Testear otros tenants (solo `odontoking` por ahora)
- Validar entrega real de mensajes por Meta (solo validamos via Admin API)
