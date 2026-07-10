# Limpieza de entidades creadas por los evals — 2026-07-09

Corrida de `evals/run_eval` (11 escenarios, gpt-4.1) contra el CRM real
(`https://odontoking.sofopolis.com`). Estas son las entidades de prueba que hay
que borrar manualmente en el CRM y, para la cita, también en SMD/ShareMeData.

## ⚠️ CITA REAL creada (borrar en CRM **y** SMD)

| activity_id | Paciente | Especialidad | Doctor | Fecha/Hora (Bolivia) |
|---|---|---|---|---|
| **32483** | Carlos Rojas | Ortodoncia | Dra. Paola Heresi Guzman | 2026-07-15 09:30 |

Ocupa un slot real en la agenda de Paola → borrarla en ambos sistemas.

## Personas y leads de prueba (borrar en CRM)

| person_id | nombre | lead_id | tiene cita |
|---|---|---|---|
| 3194 | Juan Carlos Mamani | 2759 | no |
| 3195 | Maria Elena Torres | 2760 | no |
| 3196 | Pedro Quispe Flores | 2761 | no |
| 3197 | Roberto Condori Mamani | 2762 | no |
| 3198 | Carmen Vega Salinas | 2763 | no |
| 3199 | Javier Mogro | 2764 | no |
| 3200 | Carlos Rojas | 2765 | **sí → activity 32483** |
| 3201 | Lucia Vargas | 2766 | no |

## NO borrar

- person **2072** (daniela ortiz): preexistente, NO lo creó el eval (falso match de búsqueda).

## Notas

- Solo 1 de los escenarios de agendamiento completó la reserva (turnos justos);
  el resto quedó en el paso de hora sin confirmar → por eso solo 1 cita real.
- Los wa_id de prueba tienen el prefijo `eval_5917000000xx`.
