# Guía de integración — Agente IA / Disponibilidad y agendamiento

Cómo debe consumir el agente IA el CRM Odontoking para **consultar horarios disponibles**
y **agendar citas**. La disponibilidad ya viene calculada por el CRM
(`SMD ∩ jornada local − citas`); el agente **no** debe hablar con ShareMeData directamente.

---

## 1. Configuración

| Variable | Valor |
|----------|-------|
| `ODONTOKING_API_URL`   | `https://odontoking.sofopolis.com` |
| `ODONTOKING_API_TOKEN` | token Sanctum |

Headers en **todas** las llamadas:

```http
Authorization: Bearer {ODONTOKING_API_TOKEN}
Accept: application/json
```

> ❌ No uses `/api/horarios` ni `/api/disponibilidad` (deprecados) ni
> `/api/doctors/{id}/slots` (solo datos locales, sin SMD). Para disponibilidad usá
> **únicamente** `/api/doctors/{id}/available-slots`.

---

## 2. Flujo recomendado

```
1) GET /api/doctors                      → encontrar al doctor (id) y sus especialidades
2) GET /api/doctors/{id}/available-slots → horarios reales para ofrecer al paciente
3) (resolver paciente: ya lo hace crm.py — GET/POST /api/v1/persons|contacts)
4) POST /api/v1/activities               → agendar la cita elegida
```

---

## 3. Consultar disponibilidad

```
GET /api/doctors/{id}/available-slots
```

### Parámetros (query)

| Param              | Req | Tipo    | Default | Descripción |
|--------------------|-----|---------|---------|-------------|
| `date`             | sí  | `Y-m-d` | —       | Fecha de inicio (entre hoy y hoy+6 meses) |
| `days`             | no  | int     | 7       | Días a consultar desde `date` (1–30) |
| `duration_minutes` | no  | int     | —       | **Duración real de la consulta.** Corta los bloques libres en slots de esa medida (15–480) |
| `specialty`        | no  | string  | todas   | Filtra por una especialidad puntual |
| `subsidiary`       | no  | string  | `Santa Cruz` | Sucursal |

> ⚠️ **`duration_minutes` es clave.** Pasá la duración real del servicio a agendar.
> Si la consulta es de 30 min usá `duration_minutes=30`; si es de 60, `=60`.
> Con 60 min no se ofrecen tramos donde no caben 60 min seguidos (correcto: ahí no
> se puede agendar). Sin el parámetro, devuelve los bloques contiguos completos sin cortar.

### Ejemplo

```bash
curl -H "Authorization: Bearer $TOKEN" -H "Accept: application/json" \
  "$ODONTOKING_API_URL/api/doctors/12/available-slots?date=2026-06-29&days=1&duration_minutes=30"
```

### Respuesta

```json
{
  "doctor_id": 12,
  "from": "2026-06-29",
  "days": 1,
  "source": "smd",
  "degraded": false,
  "reason": null,
  "schedule": [
    {
      "date": "2026-06-29",
      "slots": [
        { "start_time": "08:00", "end_time": "08:30", "status": "available" },
        { "start_time": "14:30", "end_time": "15:00", "status": "available" }
      ]
    }
  ]
}
```

### Cómo interpretar la respuesta

| Campo | Qué significa para el agente |
|-------|------------------------------|
| `schedule[].slots` | Horarios **ofrecibles** ese día (`start_time`/`end_time` en `H:i`, hora local). |
| `slots: []` | Ese día no hay disponibilidad (o la clínica no atiende). Ofrecer otra fecha. |
| `source: "smd"` | Disponibilidad real (SMD + jornada local + citas). Confiable para agendar. |
| `source: "local"` + `degraded: true` | SMD no disponible; datos solo locales. Ver `reason`. |

**Valores de `reason` (solo cuando `degraded=true`):**

| `reason` | Significado | Acción sugerida del agente |
|----------|-------------|-----------------------------|
| `doctor_unlinked` | El doctor no está vinculado a SMD | Ofrecer con cautela; avisar a soporte |
| `smd_unavailable` | SMD caído / timeout | Reintentar más tarde; los slots son aproximados |
| `smd_disabled` | Integración SMD apagada | Operación 100% local |

> Si `degraded=true`, los horarios provienen solo de la jornada local y pueden no
> reflejar ocupación real en SMD. Igual se puede agendar (el POST revalida), pero
> conviene loguearlo.

---

## 4. Agendar la cita

```
POST /api/v1/activities
```

El doctor y el paciente van dentro de `participants`. El backend revalida jornada,
disponibilidad SMD y conflictos antes de crear.

### Body (JSON)

```json
{
  "type": "meeting",
  "schedule_from": "2026-06-29 08:00:00",
  "schedule_to":   "2026-06-29 08:30:00",
  "title": "Consulta Ortodoncia - Juan Pérez",
  "comment": "Primera consulta",
  "product_id": 15,
  "participants": {
    "doctors": [12],
    "persons": [3456]
  }
}
```

| Campo | Req | Notas |
|-------|-----|-------|
| `type` | sí | Siempre `"meeting"` para citas |
| `schedule_from` / `schedule_to` | sí | `Y-m-d H:i:s` hora local. Deben caer dentro de un slot disponible |
| `participants.doctors[0]` | sí | `id` del doctor |
| `participants.persons[0]` | sí | `id` del paciente (person) |
| `product_id` | no | Servicio/procedimiento; si se envía debe existir |
| `title` | no | Título de la cita |
| `comment` | no | Motivo/nota |
| `lead_id` | no | Reutiliza un lead existente |

### Respuestas

| HTTP | Caso | Body |
|------|------|------|
| 200 | Cita creada | `{ "data": {...}, "message": "..." }` |
| 422 | No se pudo agendar | `{ "message": "...", "details": {...} }` |

Mensajes `422` típicos (mostrar/reaccionar):
- `El doctor ya tiene una cita programada en este horario...` → ofrecer otro slot
- `El horario seleccionado está fuera de la jornada laboral...` → elegir dentro de la jornada
- `El doctor no tiene disponibilidad en SHAREMEDATA...` → refrescar disponibilidad y reintentar

> El POST **revalida** todo: aunque el slot venía de `available-slots`, si entre la
> consulta y el agendamiento alguien tomó el horario, devuelve `422`. El agente debe
> manejar ese caso volviendo a pedir disponibilidad.

---

## 5. Ejemplo en Python (`requests`)

```python
import os, requests

BASE  = os.environ["ODONTOKING_API_URL"]
TOKEN = os.environ["ODONTOKING_API_TOKEN"]
H = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}

def get_available_slots(doctor_id: int, date: str, *, days: int = 7,
                        duration_minutes: int | None = None, specialty: str | None = None):
    params = {"date": date, "days": days}
    if duration_minutes:
        params["duration_minutes"] = duration_minutes
    if specialty:
        params["specialty"] = specialty
    r = requests.get(f"{BASE}/api/doctors/{doctor_id}/available-slots",
                     headers=H, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def book_appointment(doctor_id: int, person_id: int, start: str, end: str,
                     *, title=None, comment=None, product_id=None):
    payload = {
        "type": "meeting",
        "schedule_from": start,            # "Y-m-d H:i:s"
        "schedule_to":   end,
        "participants":  {"doctors": [doctor_id], "persons": [person_id]},
    }
    if title:      payload["title"]      = title
    if comment:    payload["comment"]    = comment
    if product_id: payload["product_id"] = product_id

    r = requests.post(f"{BASE}/api/v1/activities", headers=H, json=payload, timeout=30)
    if r.status_code == 422:
        return {"ok": False, "error": r.json().get("message"), "details": r.json().get("details")}
    r.raise_for_status()
    return {"ok": True, "data": r.json()}

# --- Flujo típico ---
avail = get_available_slots(12, "2026-06-29", days=1, duration_minutes=30)
dia   = avail["schedule"][0]
if not dia["slots"]:
    print("Sin disponibilidad ese día")
else:
    slot = dia["slots"][0]                                   # primer horario libre
    start = f'{dia["date"]} {slot["start_time"]}:00'
    end   = f'{dia["date"]} {slot["end_time"]}:00'
    res = book_appointment(12, 3456, start, end, title="Consulta", product_id=15)
    if not res["ok"]:
        # el horario se ocupó: refrescar disponibilidad y reintentar con otro slot
        print("No se pudo agendar:", res["error"])
```

---

## 6. Reglas para el agente (resumen)

1. **Disponibilidad** → solo `GET /api/doctors/{id}/available-slots`.
2. Pasar **`duration_minutes`** = duración real del servicio.
3. Ofrecer únicamente los `slots` devueltos; `slots: []` ⇒ proponer otra fecha.
4. **Agendar** con `POST /api/v1/activities`, doctor y paciente en `participants`.
5. Manejar `422` al agendar: re-consultar disponibilidad y reintentar.
6. Loguear `degraded=true` / `reason` para visibilidad operativa.
