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

---

## Qué hacemos nosotros al confirmar R1–R6

Cambio pequeño del lado del agente (ya diseñado en `docs/mejoras-odontoking-seguro-y-prefill.md`, "Mejora 2"):
- `enviar_formulario(flow, prefill=None)` → incluye `prefill` en el body solo cuando hay datos ciertos del chat.
- Regla en el prompt: si en el saludo el paciente ya dijo para quién / qué necesita, se arma el `prefill` con esos campos (nunca inventados).

**No desplegamos el lado del agente hasta tener su confirmación de R1–R5**, para evitar inconsistencias.
</content>
