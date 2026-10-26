# Requerimiento al CRM — Pre‑llenado del formulario de agendar (Odontoking)

- **Fecha:** 2026-10-26
- **De:** Equipo del agente IA (Odontoking)
- **Para:** Equipo CRM (WhatsApp Flows)

## Contexto

El agente ya detecta en el saludo **para quién** es la cita (el paciente o un tercero, con su nombre) y **qué** necesita (molestia/servicio → especialidad). Queremos que el formulario de agendar llegue **pre‑llenado** con esos datos, para que el paciente no los reescriba.

Hoy el agente envía `POST {CRM_BASE_URL}/api/v1/whatsapp/conversations/{conversation_id}/flows` con `{ "flow": "paciente_tercero" }`, y el formulario vuelve en un `flow_reply` con las claves `flow, status, patient_name, relation, specialty, doctor_name, date, time`. Falta el canal para **mandar valores iniciales** a esos campos — eso depende del CRM.

---

## Requerimientos

### R1 — Que el endpoint de flows acepte un objeto `prefill`
- **Qué necesito:** que `POST …/conversations/{id}/flows` reciba un campo opcional `prefill`:
  ```json
  { "flow": "paciente_tercero",
    "prefill": { "relation": "tercero", "patient_name": "María Pérez", "specialty": "limpieza" } }
  ```
- **Para qué:** para que el agente pueda pasarle al formulario lo que el paciente ya dijo en el chat.

### R2 — Que el Flow `paciente_tercero` pinte datos iniciales
- **Qué necesito:** que al enviar el flow se incluya `flow_action_payload.data` con esos valores y que la pantalla de entrada tenga los campos ligados a datos (`init-value: "${data.specialty}"`, etc.). **Confirmar** si los Flows publicados ya lo permiten o hay que re‑publicar `paciente_tercero`.
- **Para qué:** para que los valores aparezcan efectivamente pre‑seleccionados/pre‑escritos en el formulario del paciente (sin esto, el `prefill` no se ve).

### R3 — El catálogo exacto de campos y valores
- **Qué necesito:**
  - `relation`: los valores literales que espera (p. ej. `self` / `tercero`).
  - `specialty`: si es desplegable, la **lista de opciones/IDs válidos** tal cual los espera el form.
  - `patient_name`: confirmar que es texto libre.
- **Para qué:** para mandar valores que el formulario reconozca. Si `specialty` no coincide con los IDs de mi `get_specialties`, armo una tabla de mapeo de mi lado.

### R4 — Degradación segura ante valores imperfectos
- **Qué necesito:** que si un valor de `prefill` falta, sobra o no matchea una opción, el formulario **abra igual** (ignorando ese valor), sin error ni pantalla en blanco.
- **Para qué:** para que un pre‑llenado parcial o inexacto nunca rompa el agendamiento.

### R5 — Compatibilidad hacia atrás
- **Qué necesito:** que **hoy mismo** el endpoint **ignore claves desconocidas** (que `prefill` no provoque `400/422`) y siga enviando el formulario como ahora.
- **Para qué:** para poder desplegar el lado del agente sin esperar a que el Flow pinte los datos, sin romper el envío actual.

### R6 — Alcance
- **Qué necesito:** confirmar que el pre‑llenado aplica **solo a `paciente_tercero`** (agendar).
- **Para qué:** acotar el trabajo; `reprogramar_cita` / `cancelar_cita` no lo necesitan (el paciente elige una cita existente).

### R7 — Prefill de DOCTOR
- **Qué necesito:** agregar `doctor` como campo de prefill del Flow `paciente_tercero`, y decirnos cómo lo identifican (id del doctor en el CRM, o nombre). Ej.: "con el Dr. Eloy" → el form abre en ese doctor.
- **Para qué:** que quien ya pide un doctor no tenga que volver a elegirlo.

### R8 — Prefill de FECHA
- **Qué necesito:** agregar `date` como campo de prefill (díganme el formato, p. ej. `YYYY-MM-DD`). El agente convierte "el viernes" / "mañana" a fecha concreta y la manda.
- **Para qué:** que el form abra en el día pedido.

### R9 — Prefill de HORA + sugerencia de cupo
- **Qué necesito:**
  - agregar `time` como campo de prefill (p. ej. `HH:MM`). Ej.: "a las 13:00" → se manda como preferencia.
  - **Si NO viene `time`:** que el form **sugiera/preseleccione el cupo disponible más próximo** del doctor/fecha indicados.
  - Si el paciente **no quiere** ese cupo: que pueda **elegir fecha y hora por separado** (flujo normal del form).
  - **Degradación (como R4):** si el doctor no existe o la fecha/hora pedida no tiene cupo, el form abre igual mostrando lo disponible — nunca se rompe ni promete un horario inexistente.
- **Para qué:** que "con el Dr. Eloy el viernes a las 13:00" se resuelva sin empezar de 0, y que la disponibilidad real siga siendo la fuente de verdad.

> Nota de diseño: la **sugerencia del cupo más próximo** vive en el **form** (es quien tiene la agenda real). El agente solo manda doctor/fecha/hora como preferencia tomada del chat; nunca confirma un horario por su cuenta.

---

## Qué hacemos nosotros al confirmar R1–R9

Cambio del lado del agente (diseñado en `docs/mejoras-odontoking-seguro-y-prefill.md`, "Mejora 2"):
- `enviar_formulario(flow, prefill=…)` → incluye en `prefill` solo datos ciertos del chat: relation, patient_name, relationship, specialty, y —tras R7–R9— doctor, date, time.
- Regla en el prompt: mapear el doctor mencionado → doctor real (`get_specialty_doctors` / `get_doctors`), "viernes"/"mañana" → fecha, "13:00" → hora; nunca inventar, omitir lo dudoso.

**No desplegamos doctor/fecha/hora del lado del agente hasta tener su confirmación de R7–R9** (sobre todo el formato de `doctor` y `date`), para evitar inconsistencias. R1–R6 ya están aplicados y desplegados.
