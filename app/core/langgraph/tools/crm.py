"""Sofopolis CRM tool — create/update contacts, leads, and appointment activities."""

import asyncio
import json
import random
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
from langchain_core.tools import tool
from sqlmodel import Session, select

from app.core.config import settings
from app.core.logging import logger
from app.models.chat_history_odonto import ChatHistoryOdonto
from app.services.database import database_service

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


def _person_email_from_wa_id(wa_id: str) -> str:
    return f"{wa_id}@whatsapp.sofopolis.net"


async def find_person_by_wa_id(client: httpx.AsyncClient, wa_id: str) -> dict[str, Any] | None:
    """Look up a CRM person by WhatsApp ID. Returns the person dict or None."""
    try:
        resp = await client.get(
            f"{_BASE}/api/v1/persons",
            params={"wa_id": wa_id},
            headers=_HEADERS,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return data[0] if data and isinstance(data[0], dict) else None
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise


def _person_payload(
    wa_id: str,
    person_name: str,
    person_phone: str,
    *,
    age: int | None = None,
) -> dict[str, Any]:
    clean_name = person_name.strip() if isinstance(person_name, str) and person_name.strip() else "Paciente WhatsApp"
    clean_phone = person_phone.strip() if isinstance(person_phone, str) and person_phone.strip() else wa_id
    person_email = _person_email_from_wa_id(wa_id)
    payload: dict[str, Any] = {
        "name": clean_name,
        "emails": [{"value": person_email, "label": "work"}],
        "contact_numbers": [{"value": clean_phone, "label": "work"}],
        "entity_type": "persons",
    }
    if age is not None:
        # "job_title" column is labeled "Edad" in OdontoCRM persons entity
        payload["job_title"] = str(age)
    return payload


async def _update_person_attributes(
    client: httpx.AsyncClient,
    person_id: int,
    attrs: dict[str, Any],
) -> None:
    """Write custom attribute values onto a CRM person record (mirrors lead attributes endpoint)."""
    resp = await client.put(
        f"{_BASE}/api/v1/contacts/persons/attributes/edit/{person_id}",
        json=attrs,
        headers=_HEADERS,
    )
    resp.raise_for_status()


async def ensure_person_registered(
    client: httpx.AsyncClient,
    wa_id: str,
    person_name: str,
    person_phone: str | None = None,
    *,
    update_existing_name: bool = False,
) -> dict[str, Any]:
    """Find or create a CRM person from a WhatsApp number.

    Uses GET /api/v1/persons?wa_id=X as the primary lookup. Returns person_id,
    creation/update flags, and any stored custom attributes (ci_paciente,
    seguro_paciente) so the agent can skip asking for them again.
    """
    clean_name = person_name.strip() if isinstance(person_name, str) and person_name.strip() else "Paciente WhatsApp"
    clean_phone = person_phone.strip() if isinstance(person_phone, str) and person_phone.strip() else wa_id
    log = logger.bind(wa_id=wa_id, person_name=clean_name)

    person = await find_person_by_wa_id(client, wa_id)

    if person:
        person_id = person.get("id")
        if not person_id:
            return {"person_id": None, "created": False, "updated": False, "is_new_patient": False}

        existing_name = (person.get("name") or "").strip()
        should_update = (
            update_existing_name
            and isinstance(person_name, str)
            and person_name.strip()
            and clean_name != existing_name
            and clean_name != "Paciente WhatsApp"
        )
        if should_update:
            update_resp = await client.put(
                f"{_BASE}/api/v1/contacts/persons/{person_id}",
                json=_person_payload(wa_id, clean_name, clean_phone),
                headers=_HEADERS,
            )
            update_resp.raise_for_status()
            log.info("crm_person_updated", person_id=person_id)

        log.info("crm_person_found", person_id=person_id)
        return {
            "person_id": person_id,
            "created": False,
            "updated": should_update,
            "is_new_patient": False,
            "ci_paciente": person.get("ci_paciente"),
            "seguro_paciente": person.get("seguro_paciente"),
        }

    create_resp = await client.post(
        f"{_BASE}/api/v1/contacts/persons",
        json=_person_payload(wa_id, clean_name, clean_phone),
        headers=_HEADERS,
    )
    create_resp.raise_for_status()
    person_id = create_resp.json().get("data", {}).get("id")
    log.info("crm_person_created", person_id=person_id)
    return {
        "person_id": person_id,
        "created": True,
        "updated": False,
        "is_new_patient": True,
        "ci_paciente": None,
        "seguro_paciente": None,
    }


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
    edad_paciente: Optional[int] = None,
    is_for_self: bool = True,
    motivo_consulta: Optional[str] = None,
    estado_seguro: Optional[str] = None,
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
        edad_paciente: Age of the person writing (primary patient).
        is_for_self: True if the appointment is for the WhatsApp sender; False if for another person.
        motivo_consulta: Patient's reason for visit or main complaint.
        estado_seguro: Insurance status after verification (e.g. 'VIGENTE', 'VENCIDO').
    """
    log = logger.bind(wa_id=wa_id, person_name=person_name)
    person_email = _person_email_from_wa_id(wa_id)

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # 1. Find or create person, updating the name if this call knows a better one
            person_result = await ensure_person_registered(
                client,
                wa_id,
                person_name,
                person_phone,
                update_existing_name=True,
            )
            person_id = person_result["person_id"]
            if not person_id:
                return json.dumps({"error": "could not obtain person_id"})

            # 1b. Persist collected patient attributes onto the person record
            person_attrs: dict[str, Any] = {}
            if edad_paciente is not None:
                person_attrs["job_title"] = str(edad_paciente)
            if numero_carnet:
                person_attrs["ci_paciente"] = numero_carnet
            if seguro_de_vida:
                person_attrs["seguro_paciente"] = seguro_de_vida
            if estado_seguro:
                person_attrs["estado_seguro_paciente"] = estado_seguro
            if person_attrs:
                try:
                    await _update_person_attributes(client, person_id, person_attrs)
                    log.info("crm_person_attributes_updated", person_id=person_id, attrs=list(person_attrs.keys()))
                except Exception as attr_err:
                    log.warning("crm_person_attributes_failed", person_id=person_id, error=str(attr_err))

            # 2. Find existing leads for this person — filter by person_id server-side
            leads_resp = await client.get(
                f"{_BASE}/api/v1/leads",
                params={"sort": "id", "limit": 10, "person_id": str(person_id)},
                headers=_HEADERS,
            )
            leads_resp.raise_for_status()
            all_leads = leads_resp.json().get("data", [])
            # Python-side guard: keep only leads that truly belong to this person
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

            # 5. Register insurance on lead
            if seguro_de_vida:
                insurance_id = _INSURANCE_ID_MAP.get(seguro_de_vida.lower().strip())
                if insurance_id:
                    await client.put(
                        f"{_BASE}/api/v1/leads/attributes/edit/{lead_id}",
                        json={"insurance": [insurance_id]},
                        headers=_HEADERS,
                    )
                    log.info("crm_insurance_registered", lead_id=lead_id, insurance_id=insurance_id)

            # 6. Register CI + age + third-party name + insurance status on lead
            lead_attrs: dict[str, Any] = {}
            if numero_carnet:
                lead_attrs["ci"] = numero_carnet
            patient_age = edad_paciente_de_otra_persona if not is_for_self else edad_paciente
            if patient_age is not None:
                lead_attrs["edad_lead"] = str(patient_age)
            if not is_for_self and nombre_paciente_de_otra_persona:
                lead_attrs["nombre_paciente_de_otra_persona"] = nombre_paciente_de_otra_persona
            if estado_seguro:
                lead_attrs["estado_seguro_paciente_cita"] = estado_seguro
            if lead_attrs:
                await client.put(
                    f"{_BASE}/api/v1/leads/attributes/edit/{lead_id}",
                    json=lead_attrs,
                    headers=_HEADERS,
                )
                log.info("crm_lead_attributes_updated", lead_id=lead_id, attrs=list(lead_attrs.keys()))

            # 7. Create appointment activity when confirmed
            appointment_registered = False
            if es_cita_confirmada and horario_cita and doctor_id:
                schedule_from, schedule_to = _parse_appointment_datetime(horario_cita)
                if not schedule_from:
                    return json.dumps({
                        "success": False,
                        "appointment_registered": False,
                        "error_type": "invalid_appointment_datetime",
                        "message": "No se pudo interpretar la fecha y hora de la cita.",
                        "person_id": person_id,
                        "lead_id": lead_id,
                    })

                appointment_patient = nombre_paciente_de_otra_persona if not is_for_self else person_name
                patient_type_label = "Tercero" if not is_for_self else "Mismo paciente"
                comment_lines = [
                    f"Paciente: {appointment_patient} ({patient_type_label})",
                    f"Servicio: {products_name or 'Por definir'}",
                    f"Motivo: {motivo_consulta or 'No especificado'}",
                    f"Seguro: {seguro_de_vida or 'Ninguno'}",
                    f"Estado seguro: {estado_seguro or 'No verificado'}",
                    f"Doctor: {nombre_doctor or str(doctor_id)}",
                ]
                if not is_for_self and nombre_paciente_de_otra_persona:
                    comment_lines.append(f"Solicitado por: {person_name}")

                activity_body: dict = {
                    "lead_id": lead_id,
                    "title": f"{appointment_patient} - {products_name or 'Consulta'}",
                    "type": "meeting",
                    "schedule_from": schedule_from,
                    "schedule_to": schedule_to,
                    "location": "Consultorio",
                    "comment": "\n".join(comment_lines),
                    "participants": {
                        "persons": [str(person_id)],
                        "users": ["1"],
                        "doctors": [str(doctor_id)],
                    },
                }
                if products_product_id is not None:
                    activity_body["product_id"] = products_product_id
                act_resp = await client.post(
                    f"{_BASE}/api/v1/activities",
                    json=activity_body,
                    headers=_HEADERS,
                )
                if act_resp.status_code == 422:
                    try:
                        error_detail = act_resp.json()
                    except Exception:
                        error_detail = {"message": act_resp.text}
                    log.error("crm_activity_422", body=activity_body, response=act_resp.text)
                    return json.dumps({
                        "success": False,
                        "appointment_registered": False,
                        "error_type": "appointment_conflict",
                        "message": error_detail.get("message", "No se pudo registrar la cita."),
                        "person_id": person_id,
                        "lead_id": lead_id,
                    })
                act_resp.raise_for_status()
                appointment_registered = True
                log.info("crm_activity_created", lead_id=lead_id, schedule=schedule_from)

            return json.dumps({
                "success": (not es_cita_confirmada) or appointment_registered,
                "person_id": person_id,
                "lead_id": lead_id,
                "appointment_registered": appointment_registered,
            })

    except Exception as e:
        log.exception("update_crm_failed", error=str(e))
        return json.dumps({"success": False, "error": str(e)})


@tool
async def get_citas(wa_id: str) -> str:
    """Get all appointments (meetings) for a patient by their WhatsApp ID.

    Searches the CRM for the lead associated with the WhatsApp number and
    returns all meeting activities (past and upcoming). Use this to check
    what appointments a patient already has before scheduling a new one.

    Args:
        wa_id: WhatsApp ID of the patient (e.g. '591XXXXXXXX').
    """
    person_email = f"{wa_id}@whatsapp.sofopolis.net"
    log = logger.bind(wa_id=wa_id)

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Step 1: find person by wa_id
            person = await find_person_by_wa_id(client, wa_id)
            if not person:
                log.info("get_citas_person_not_found", wa_id=wa_id)
                return json.dumps({"citas": [], "message": "patient_not_found_in_crm"})

            person_id = person["id"]
            # Step 2: find lead filtered by person_id — avoids full-table scan
            leads_resp = await client.get(
                f"{_BASE}/api/v1/leads",
                params={"sort": "id", "limit": 10, "person_id": str(person_id)},
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
            if not matching:
                log.info("get_citas_lead_not_found", email=person_email)
                return json.dumps({"citas": [], "message": "no_lead_found_for_patient"})

            lead_id = matching[-1]["id"]

            # Step 3: fetch activities for the lead
            acts_resp = await client.get(
                f"{_BASE}/api/v1/leads/{lead_id}/activities",
                headers=_HEADERS,
            )
            acts_resp.raise_for_status()
            raw = acts_resp.json()
            # API may return {"data": [...]} or directly [...]
            activities: list = raw.get("data", raw) if isinstance(raw, dict) else raw

            # Step 4: filter meetings only, return minimal fields
            meetings = [
                {
                    "id": a["id"],
                    "title": a.get("title", ""),
                    "schedule_from": a.get("schedule_from", ""),
                    "schedule_to": a.get("schedule_to", ""),
                    "is_done": a.get("is_done", 0),
                    "comment": a.get("comment", ""),
                    "participants": [
                        p.get("person", {}).get("name", "")
                        for p in (a.get("participants") or [])
                        if p.get("person")
                    ],
                }
                for a in (activities if isinstance(activities, list) else [])
                if a.get("type") == "meeting"
            ]

            log.info("get_citas_fetched", lead_id=lead_id, count=len(meetings))
            return json.dumps({"citas": meetings, "lead_id": lead_id}, ensure_ascii=False)

    except httpx.HTTPStatusError as e:
        log.exception("get_citas_http_error", status=e.response.status_code, error=str(e))
        return json.dumps({"error": f"API returned {e.response.status_code}"})
    except Exception as e:
        log.exception("get_citas_failed", error=str(e))
        return json.dumps({"error": str(e)})


def _fetch_transcript(wa_id: str, max_messages: int) -> str:
    """Read and format the last N messages from chat_histories_odonto (sync)."""
    sid = f"{wa_id}@whatsapp.sofopolis.net"
    with Session(database_service.engine) as db:
        rows = db.exec(
            select(ChatHistoryOdonto)
            .where(ChatHistoryOdonto.session_id == sid)
            .order_by(ChatHistoryOdonto.created_at)
        ).all()

    lines: list[str] = []
    for r in rows[-max_messages:]:
        try:
            data = json.loads(r.message)
            msg_type = data.get("type", "")
            if msg_type == "tool":
                continue
            content = data.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            content = str(content).strip()
            if not content:
                continue
            prefix = "Paciente" if msg_type == "human" else "Agente"
            ts = r.created_at.strftime("%d/%m/%Y %H:%M")
            lines.append(f"[{ts}] {prefix}: {content}")
        except (json.JSONDecodeError, AttributeError):
            continue
    return "\n".join(lines)


@tool
async def sync_transcript_to_crm(wa_id: str, max_messages: int = 50) -> str:
    """Push the WhatsApp conversation transcript to the CRM as a note on the lead.

    Reads the last N messages from the local conversation history and creates
    a note activity on the CRM lead for this patient. Call this when the
    conversation reaches a natural end, when an appointment is confirmed,
    or when requested to sync the history.

    Args:
        wa_id: WhatsApp ID of the patient (e.g. '591XXXXXXXX').
        max_messages: Maximum number of recent messages to include (default 50).
    """
    person_email = f"{wa_id}@whatsapp.sofopolis.net"
    log = logger.bind(wa_id=wa_id)

    transcript = await asyncio.to_thread(_fetch_transcript, wa_id, max_messages)
    if not transcript:
        return json.dumps({"success": False, "message": "no_messages_found"})

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            person = await find_person_by_wa_id(client, wa_id)
            if not person:
                return json.dumps({"success": False, "message": "person_not_found_in_crm"})

            person_id = person["id"]
            leads_resp = await client.get(
                f"{_BASE}/api/v1/leads",
                params={"sort": "id", "limit": 10, "person_id": str(person_id)},
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
            if not matching:
                return json.dumps({"success": False, "message": "no_lead_found_for_patient"})

            lead_id = matching[-1]["id"]
            note_title = f"Historial WhatsApp — {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            note_resp = await client.post(
                f"{_BASE}/api/v1/activities",
                json={
                    "lead_id": lead_id,
                    "title": note_title,
                    "type": "note",
                    "comment": transcript,
                },
                headers=_HEADERS,
            )
            note_resp.raise_for_status()
            activity_id = note_resp.json().get("data", {}).get("id")
            log.info("crm_transcript_synced", lead_id=lead_id, activity_id=activity_id)
            return json.dumps({"success": True, "lead_id": lead_id, "activity_id": activity_id})

    except Exception as e:
        log.exception("sync_transcript_to_crm_failed", error=str(e))
        return json.dumps({"success": False, "error": str(e)})
