"""Sofopolis CRM tool — create/update contacts, leads, and appointment activities."""

import json
import random
from datetime import datetime, timedelta
from typing import Optional

import httpx
from langchain_core.tools import tool

from app.core.config import settings
from app.core.logging import logger

_HEADERS = {
    "accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {settings.ODONTOKING_API_TOKEN}",
}
_BASE = settings.ODONTOKING_API_URL

_INSURANCE_ID_MAP = {
    "alianza": 1,
    "nacional vida": 2,
    "membresía odontoking": 3,
    "membresia odontoking": 3,
    "no tengo seguro": 65,
}

_AGENT_USERS_WEEKDAY = [1, 2, 3, 5, 6]
_AGENT_USERS_SUNDAY = [6, 8]


def _pick_agent_user() -> int:
    pool = _AGENT_USERS_SUNDAY if datetime.now().weekday() == 6 else _AGENT_USERS_WEEKDAY
    return random.choice(pool)


def _parse_appointment_datetime(horario_cita: str) -> tuple[str, str]:
    """Parse 'DD/MM/YYYY HH:MM' into (schedule_from, schedule_to) ISO strings."""
    try:
        sep = "_" if "_" in horario_cita else " "
        fecha, hora = horario_cita.strip().split(sep, 1)
        if "/" in fecha:
            day, month, year = fecha.split("/")
        else:
            year, month, day = fecha.split("-")
        dt_start = datetime(int(year), int(month), int(day), int(hora.split(":")[0]), int(hora.split(":")[1]))
        dt_end = dt_start + timedelta(hours=1)
        fmt = "%Y-%m-%d %H:%M:%S"
        return dt_start.strftime(fmt), dt_end.strftime(fmt)
    except Exception:
        return "", ""


@tool
async def update_crm(
    wa_id: str,
    person_name: str,
    person_phone: str,
    products_name: Optional[str] = None,
    products_product_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
    nombre_doctor: Optional[str] = None,
    seguro_de_vida: Optional[str] = None,
    horario_cita: Optional[str] = None,
    numero_carnet: Optional[str] = None,
    es_cita_confirmada: bool = False,
    es_cita_cancelada: bool = False,
    paciente_antiguo: bool = False,
    nombre_paciente_agente: Optional[str] = None,
    nombre_paciente_de_otra_persona: Optional[str] = None,
    edad_paciente_de_otra_persona: Optional[int] = None,
) -> str:
    """Create or update a patient lead and appointment in Sofopolis CRM.

    Call this tool progressively as patient data is collected. Call it again
    when the appointment is confirmed (es_cita_confirmada=True) to register
    the appointment activity.

    Args:
        wa_id: WhatsApp ID of the contact (e.g. '591XXXXXXXX').
        person_name: Full name of the person writing.
        person_phone: Phone number digits only.
        products_name: Service/product name chosen by patient.
        products_product_id: Numeric ID of the product from get_services().
        doctor_id: Numeric ID of the chosen doctor from get_doctors().
        nombre_doctor: Doctor display name.
        seguro_de_vida: Insurance name (e.g. 'Alianza', 'Nacional Vida').
        horario_cita: Appointment datetime in format 'DD/MM/YYYY HH:MM'.
        numero_carnet: Patient ID card number.
        es_cita_confirmada: True when patient confirmed the appointment.
        es_cita_cancelada: True when patient cancelled.
        paciente_antiguo: True if returning patient.
        nombre_paciente_agente: Display name for the agent.
        nombre_paciente_de_otra_persona: Name if booking for someone else.
        edad_paciente_de_otra_persona: Age if booking for someone else.
    """
    person_email = f"{wa_id}@whatsapp.sofopolis.net"
    log = logger.bind(wa_id=wa_id, person_name=person_name)

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # 1. Find or create person
            search_resp = await client.get(
                f"{_BASE}/api/v1/contacts/persons/search",
                params={"search": person_email, "searchFields": "emails:like"},
                headers=_HEADERS,
            )
            search_resp.raise_for_status()
            persons = search_resp.json().get("data", [])

            if persons:
                person_id = persons[0]["id"]
                log.info("crm_person_found", person_id=person_id)
            else:
                create_resp = await client.post(
                    f"{_BASE}/api/v1/contacts/persons",
                    json={
                        "name": person_name,
                        "emails": [{"value": person_email, "label": "work"}],
                        "contact_numbers": [{"value": person_phone, "label": "work"}],
                        "entity_type": "persons",
                    },
                    headers=_HEADERS,
                )
                create_resp.raise_for_status()
                person_id = create_resp.json().get("data", {}).get("id")
                log.info("crm_person_created", person_id=person_id)

            if not person_id:
                return json.dumps({"error": "could not obtain person_id"})

            # 2. Find existing leads for this person
            leads_resp = await client.get(
                f"{_BASE}/api/v1/leads",
                params={"sort": "id", "limit": 200},
                headers=_HEADERS,
            )
            leads_resp.raise_for_status()
            all_leads = leads_resp.json().get("data", [])
            matching = [
                ld for ld in all_leads
                if any(
                    (e.get("value", "")).lower() == person_email.lower()
                    for e in (ld.get("person", {}).get("emails") or [])
                )
            ]

            agent_user = _pick_agent_user()
            close_date = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")

            if not matching:
                # 3a. Create new lead
                lead_body = {
                    "title": f"Consulta - {person_name}",
                    "description": "Primer contacto vía WhatsApp",
                    "lead_value": 0,
                    "lead_source_id": 6,
                    "lead_pipeline_stage_id": 1,
                    "lead_type_id": 1,
                    "user_id": agent_user,
                    "expected_close_date": close_date,
                    "person": {"id": str(person_id), "name": person_name},
                    "products": {},
                    "entity_type": "leads",
                }
                lead_resp = await client.post(f"{_BASE}/api/v1/leads", json=lead_body, headers=_HEADERS)
                lead_resp.raise_for_status()
                lead_id = lead_resp.json().get("data", {}).get("id")
                log.info("crm_lead_created", lead_id=lead_id)
            else:
                lead_id = matching[-1]["id"]
                # 3b. Update existing lead
                update_body = {
                    "title": person_name,
                    "description": f"{products_name or ''} - {nombre_doctor or ''}",
                    "lead_value": 0,
                    "lead_pipeline_stage_id": 7,
                    "lead_source_id": 6,
                    "lead_type_id": 1,
                    "user_id": agent_user,
                    "expected_close_date": close_date,
                    "person": {"name": person_name, "id": str(person_id)},
                    "entity_type": "leads",
                }
                if products_name and products_product_id:
                    update_body["products"] = {
                        "product_0": {
                            "name": products_name,
                            "product_id": str(products_product_id),
                            "price": "0",
                            "quantity": 1,
                        }
                    }
                upd_resp = await client.put(f"{_BASE}/api/v1/leads/{lead_id}", json=update_body, headers=_HEADERS)
                upd_resp.raise_for_status()
                log.info("crm_lead_updated", lead_id=lead_id)

            if not lead_id:
                return json.dumps({"error": "could not obtain lead_id"})

            # 4. Change stage if cancelled
            if es_cita_cancelada:
                await client.put(
                    f"{_BASE}/api/v1/leads/stage/edit/{lead_id}",
                    json={"lead_pipeline_stage_id": [6]},
                    headers=_HEADERS,
                )
                log.info("crm_lead_stage_cancelled", lead_id=lead_id)

            # 5. Register insurance
            if seguro_de_vida:
                insurance_id = _INSURANCE_ID_MAP.get(seguro_de_vida.lower().strip())
                if insurance_id:
                    await client.put(
                        f"{_BASE}/api/v1/leads/attributes/edit/{lead_id}",
                        json={"insurance": [insurance_id]},
                        headers=_HEADERS,
                    )
                    log.info("crm_insurance_registered", lead_id=lead_id, insurance_id=insurance_id)

            # 6. Register CI
            if numero_carnet:
                await client.put(
                    f"{_BASE}/api/v1/leads/attributes/edit/{lead_id}",
                    json={"ci": numero_carnet},
                    headers=_HEADERS,
                )

            # 7. Create appointment activity when confirmed
            if es_cita_confirmada and horario_cita and doctor_id:
                schedule_from, schedule_to = _parse_appointment_datetime(horario_cita)
                if schedule_from:
                    activity_body = {
                        "lead_id": lead_id,
                        "title": f"{person_name} - {products_name or 'Consulta'}",
                        "type": "meeting",
                        "schedule_from": schedule_from,
                        "schedule_to": schedule_to,
                        "location": "Consultorio",
                        "product_id": products_product_id,
                        "comment": f"{products_name or ''} - Seguro: {seguro_de_vida or 'Ninguno'}",
                        "participants": {
                            "persons": [str(person_id)],
                            "users": ["1"],
                            "doctors": [str(doctor_id)],
                        },
                    }
                    act_resp = await client.post(
                        f"{_BASE}/api/v1/activities",
                        json=activity_body,
                        headers=_HEADERS,
                    )
                    act_resp.raise_for_status()
                    log.info("crm_activity_created", lead_id=lead_id, schedule=schedule_from)

            return json.dumps({
                "success": True,
                "person_id": person_id,
                "lead_id": lead_id,
                "appointment_registered": es_cita_confirmada and bool(horario_cita),
            })

    except Exception as e:
        log.exception("update_crm_failed", error=str(e))
        return json.dumps({"success": False, "error": str(e)})
