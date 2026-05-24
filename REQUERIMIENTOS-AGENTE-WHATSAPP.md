# REQUERIMIENTOS — Plataforma Agente WhatsApp → CRM Odontoking

**De:** Plataforma Agente WhatsApp — Lead Dev
**Para:** Team Lead CRM Odontoking
**Fecha:** 2026-05-24
**Prioridad:** 2 requerimientos bloqueantes en producción

---

## Estado actual

El agente conversacional WhatsApp de Odontoking está en producción. Se detectaron errores críticos en el flujo de agendamiento de citas causados por datos insuficientes en la API del CRM. Necesitamos que el equipo de **backend**, **arquitecto**, **qa** y **ciberseguridad** del CRM revisen y resuelvan los siguientes requerimientos.

---

## REQ-1 — Agregar campos faltantes en `GET /api/doctors` → `availability` · **BLOQUEANTE**

### Problema en producción

El endpoint devuelve `start_time` por slot pero no `end_time`. El agente asume que cada cita dura 60 minutos e inventa la hora de fin. Esto ya está generando confirmaciones incorrectas en producción:

```
API devuelve:  start_time: 16:00:00
Agente muestra: "16:00 - 17:00"   ← hora de fin fabricada
```

Si los slots reales duran 30 o 45 minutos, el agente está mostrando y confirmando horarios falsos.

### Contrato requerido por slot en `availability`

```json
{
  "slot_id": "19-20260525-1600",
  "date": "2026-05-25",
  "start_time": "16:00:00",
  "end_time": "16:30:00",
  "duration_minutes": 30,
  "is_available": true
}
```

### Campos adicionales requeridos a nivel doctor

```json
{
  "timezone": "America/Lima",
  "working_hours": {
    "lunes":  { "open": "14:00:00", "close": "18:00:00" },
    "martes": { "open": "14:00:00", "close": "18:00:00" }
  },
  "default_slot_duration_minutes": 30
}
```

### Criterio de aceptación

- `end_time` presente en todos los slots de `availability`
- `duration_minutes` coincide con `end_time - start_time`
- `is_available: false` en slots ya reservados o bloqueados
- `working_hours` presente por día de la semana activo

---

## REQ-2 — Confirmar endpoint canónico de disponibilidad en tiempo real · **BLOQUEANTE**

### Problema

El agente actualmente usa el campo `availability` de `GET /api/doctors` para mostrar horarios. No está claro si ese campo ya excluye las citas reservadas o si es el horario base semanal (plantilla estática).

### Preguntas que necesitamos responder

1. ¿`availability` en `GET /api/doctors` excluye citas ya agendadas, o es el horario base?
2. ¿`GET /api/disponibilidad?doctorId=X&date=Y` es el endpoint canónico para disponibilidad real?
3. ¿Hay algún mecanismo de lock/reserva optimista para evitar doble booking (dos pacientes confirmando el mismo slot simultáneamente)?

### Riesgo actual sin esta aclaración

Si `availability` es el horario base, dos pacientes pueden agendar el mismo slot — uno por WhatsApp y otro por la app web — resultando en doble cita en el mismo sillón.

---

## REQ-3 — `duration_minutes` en endpoint de servicios/productos · **ALTO**

### Problema

El agente no puede validar si un slot de 30 minutos alcanza para el servicio solicitado (ej. Implante puede durar 90 min, Limpieza 45 min).

### Campo requerido en `GET /api/v1/products` o equivalente

```json
{
  "id": 5,
  "name": "Limpieza",
  "duration_minutes": 45
}
```

### Criterio de aceptación

- Todos los servicios/productos tienen `duration_minutes` definido
- El campo es mayor a 0 y múltiplo de 15 (o la unidad de slot que use el CRM)

---

## REQ-4 — NUEVO ENDPOINT: Verificación de seguro dental · **NUEVO REQUERIMIENTO**

### Descripción

Necesitamos un endpoint que verifique si un paciente tiene seguro dental activo **sin requerir `person_id`**. Los pacientes de WhatsApp no conocen su ID interno en el CRM; nos identifican por teléfono o cédula.

### Endpoint propuesto

```
GET /api/v1/insurance/verify
```

**Parámetros (al menos uno requerido):**

| Parámetro | Tipo | Descripción |
|---|---|---|
| `phone` | string | Número en formato E.164 (ej. `+593987654321`) |
| `cedula` | string | Número de cédula/documento de identidad |
| `email` | string | Correo electrónico registrado en el CRM |

**Respuesta cuando tiene seguro activo:**

```json
{
  "has_insurance": true,
  "insurance_name": "Seguros Equinoccial",
  "policy_number": "POL-2025-00123",
  "coverage_type": "dental",
  "valid_until": "2026-12-31",
  "covered_services": ["limpieza", "radiografia", "extraccion"],
  "patient_name": "Adenilza Flores",
  "patient_id": 55
}
```

**Respuesta cuando no tiene seguro o no se encuentra:**

```json
{
  "has_insurance": false,
  "patient_found": false
}
```

> **Nota importante:** Usar la misma respuesta tanto para "paciente no encontrado" como para "paciente sin seguro" evita enumeración de pacientes por diferencia de respuesta.

### Requerimientos de seguridad (para equipo `ciberseguridad`)

- Autenticación: mismo `ODONTOKING_API_TOKEN` que ya usamos en los demás endpoints
- Rate limiting: **máx 10 req/min por token** para evitar enumeración masiva de pacientes
- Audit log: registrar cada consulta con `{ token_hash, search_field, search_value_hash, timestamp, result: bool }`
  - Guardar hash del valor buscado (no el valor en claro) para cumplir con protección de PII
- No retornar `patient_id` si `has_insurance: false` (evita confirmación de existencia del paciente)
- HTTPS obligatorio (ya debería estar garantizado en producción)

### Criterio de aceptación

- Busca por `phone`, `cedula` o `email` indistintamente
- Devuelve la misma estructura independientemente del resultado
- Pasa auditoría de rate limiting (test: 11 requests en 1 minuto devuelve 429 en la #11)
- Audit log registra cada consulta

---

## REQ-5 — Endpoint directo por doctor ID · **MEJORA**

`GET /api/doctors/{id}` — para evitar cargar los 100 doctores cada vez que el agente necesita los slots de uno solo. Actualmente cargamos toda la lista y filtramos en Python.

**Beneficio:** Reduce payload ~99%, permite caché granular por doctor, soporta `ETag` para validación.

---

## Preguntas abiertas — Responder antes de implementar

El `team-lead` debe distribuir estas preguntas a `backend` y `arquitecto`:

| # | Pregunta | Para |
|---|---|---|
| P1 | ¿`availability` en `GET /api/doctors` excluye reservas o es horario base? | backend |
| P2 | ¿Los slots tienen ID propio en BD o se identifican por `(doctor_id, date, start_time)`? | backend |
| P3 | ¿La duración de un slot varía por servicio/especialidad o es fija para el doctor? | arquitecto |
| P4 | ¿Existe mecanismo de lock optimista para evitar doble booking? | arquitecto |
| P5 | Para REQ-4: ¿ya existe tabla de seguros en el CRM o es funcionalidad completamente nueva? | backend |

---

## Tabla de prioridades

| REQ | Descripción | Prioridad | Bloqueante |
|---|---|---|---|
| REQ-1 | `end_time` + `duration_minutes` + `is_available` en slots | Crítica | Sí — falla en producción hoy |
| REQ-2 | Confirmar endpoint canónico + aclarar doble booking | Crítica | Sí — riesgo de reservas duplicadas |
| REQ-3 | `duration_minutes` en servicios | Alta | No |
| REQ-4 | Nuevo endpoint verificación de seguro | Alta | No (nuevo feature) |
| REQ-5 | `GET /api/doctors/{id}` | Baja | No |

---

## Acción solicitada al Team Lead CRM

1. **Distribuir** REQ-1 y REQ-2 a `backend` para priorización inmediata
2. **Responder** las preguntas P1-P5 y enviárnoslas para terminar de diseñar el fix de la plataforma
3. **Coordinar** REQ-4 con `ciberseguridad` antes de implementar
4. **Avisarnos** cuando REQ-1 esté disponible en staging para hacer la integración

---

*Plataforma Agente WhatsApp — Lead Dev*
*Contacto: proyecto `03.agent-production` — endpoint producción `https://odontoking.wappy.dev`*
