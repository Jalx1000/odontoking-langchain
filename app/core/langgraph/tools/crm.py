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

# CRM lead pipeline stages.
_LEAD_STAGE_CONSULTA = 1   # first contact
_LEAD_STAGE_CANCELADO = 6  # cancelled
_LEAD_STAGE_AGENDADO = 7   # appointment scheduled

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


def _real_name_or_none(name: str | None) -> str | None:
    """Return the name only if it's a real one — not empty and not the placeholder.

    A CRM person is auto-created on first contact with the fallback name
    "Paciente WhatsApp" before we know who they are. That placeholder must be
    treated as "no name" so the agent still asks for / resolves the real name.
    """
    if not isinstance(name, str):
        return None
    clean = name.strip()
    if not clean or clean == "Paciente WhatsApp":
        return None
    return clean


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


async def _update_person_age(
    client: httpx.AsyncClient,
    person_id: int,
    wa_id: str,
    person_name: str,
    person_phone: str,
    age: int,
) -> None:
    """Persist the patient's age on the CRM person record.

    There is NO person custom-attributes endpoint in this Krayin REST API (the old
    /contacts/persons/attributes/edit/{id} route 404s), and the standard person PUT ignores
    user-defined custom attributes (ci_paciente, seguro_paciente — a select). The only person
    field the API accepts is the native `job_title` (labeled "Edad"), set via the full person
    payload. CI / insurance / status are persisted on the LEAD instead (see update_crm step 6).
    """
    payload = _person_payload(wa_id, person_name, person_phone, age=age)
    resp = await client.put(
        f"{_BASE}/api/v1/contacts/persons/{person_id}",
        json=payload,
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
            return {"person_id": None, "created": False, "updated": False, "is_new_patient": False, "nombre_registrado": None}

        existing_name = (person.get("name") or "").strip()
        should_update = (
            update_existing_name
            and isinstance(person_name, str)
            and person_name.strip()
            and clean_name != existing_name
            and clean_name != "Paciente WhatsApp"
            # Never downgrade a combined "<perfil WhatsApp> - <nombre solicitado>" name back to
            # a plain name (e.g. a later update_crm call that only knows the real name).
            and not (" - " in existing_name and " - " not in clean_name)
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
            "nombre_registrado": _real_name_or_none(clean_name if should_update else existing_name),
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
        "nombre_registrado": _real_name_or_none(clean_name),
        "ci_paciente": None,
        "seguro_paciente": None,
    }


async def ensure_lead_registered(
    wa_id: str, person_id: int, person_name: str | None = None
) -> dict[str, Any]:
    """Ensure the person has a CRM lead in the 'Consulta' pipeline stage.

    Called on first contact so a brand-new WhatsApp number appears in the pipeline immediately,
    even if the patient only greets and never finishes the intake. Idempotent: if a lead already
    exists for this person it is returned unchanged (no duplicate created), which also makes it
    safe against Meta webhook retries. Mirrors the new-lead branch of update_crm (stage 1,
    source 6, title "Consulta - <nombre>") so a later update_crm reuses this same lead.
    """
    log = logger.bind(wa_id=wa_id)
    name = _real_name_or_none(person_name) or "Paciente WhatsApp"
    person_email = _person_email_from_wa_id(wa_id)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
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
            if matching:
                lead_id = matching[-1]["id"]
                log.info("crm_lead_exists_skip_create", lead_id=lead_id)
                return {"success": True, "lead_id": lead_id, "created": False}

            close_date = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
            lead_body = {
                "title": f"Consulta - {name}",
                "description": "Primer contacto vía WhatsApp",
                "lead_value": 0,
                "lead_source_id": 6,
                "lead_pipeline_stage_id": 1,
                "lead_type_id": 1,
                "user_id": _pick_agent_user(),
                "expected_close_date": close_date,
                "person": {"id": str(person_id), "name": name},
                "products": {},
                "entity_type": "leads",
            }
            lead_resp = await client.post(f"{_BASE}/api/v1/leads", json=lead_body, headers=_HEADERS)
            lead_resp.raise_for_status()
            lead_id = lead_resp.json().get("data", {}).get("id")
            log.info("crm_lead_created_first_contact", lead_id=lead_id)
            return {"success": True, "lead_id": lead_id, "created": True}
    except Exception as e:
        log.exception("ensure_lead_registered_failed", error=str(e))
        return {"success": False, "error": str(e)}


async def _find_duplicate_meeting_id(
    client: httpx.AsyncClient, lead_id: int, schedule_from: str
) -> Optional[int]:
    """Return the id of an existing not-done meeting at the same start time, or None.

    Idempotency guard for appointment creation: prevents a second activity for the same slot
    when update_crm is retried (e.g. the previous POST /activities succeeded on the server but
    its response was lost, so booking.py stayed in the 'confirmar' phase and the patient re-sent
    "SÍ"). Matches on the minute (YYYY-MM-DD HH:MM) since our writer always emits :00 seconds.

    Fails open: any error looking up existing activities returns None so a legitimate booking is
    never blocked — the worst case is the pre-existing behaviour (a possible duplicate).
    """
    try:
        resp = await client.get(f"{_BASE}/api/v1/leads/{lead_id}/activities", headers=_HEADERS)
        resp.raise_for_status()
        raw = resp.json()
        activities = raw.get("data", raw) if isinstance(raw, dict) else raw
        target = (schedule_from or "")[:16]  # compare on the minute, ignore seconds
        if not target:
            return None
        for a in (activities if isinstance(activities, list) else []):
            if (
                a.get("type") == "meeting"
                and not a.get("is_done")
                and (a.get("schedule_from") or "")[:16] == target
            ):
                return a.get("id")
    except Exception as e:
        logger.warning("crm_duplicate_check_failed", lead_id=lead_id, error=str(e))
    return None


# ── Shared person/lead resolution (used by every write tool) ──────────────────

async def _find_or_create_lead(
    client: httpx.AsyncClient, wa_id: str, person_id: int, person_name: str | None
) -> Optional[int]:
    """Return the person's lead id, creating one in the 'Consulta' stage if none exists.

    Idempotent: reuses the most recent matching lead. Every write tool calls this so the
    person→lead substrate is resolved the same way regardless of which single action runs.
    """
    person_email = _person_email_from_wa_id(wa_id)
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
    if matching:
        return matching[-1]["id"]

    name = _real_name_or_none(person_name) or "Paciente WhatsApp"
    close_date = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    lead_body = {
        "title": f"Consulta - {name}",
        "description": "Primer contacto vía WhatsApp",
        "lead_value": 0,
        "lead_source_id": 6,
        "lead_pipeline_stage_id": _LEAD_STAGE_CONSULTA,
        "lead_type_id": 1,
        "user_id": _pick_agent_user(),
        "expected_close_date": close_date,
        "person": {"id": str(person_id), "name": name},
        "products": {},
        "entity_type": "leads",
    }
    lead_resp = await client.post(f"{_BASE}/api/v1/leads", json=lead_body, headers=_HEADERS)
    lead_resp.raise_for_status()
    return lead_resp.json().get("data", {}).get("id")


async def _resolve_person_and_lead(
    client: httpx.AsyncClient, wa_id: str, person_name: str, person_phone: str
) -> tuple[Optional[int], Optional[int]]:
    """Resolve (person_id, lead_id), creating each if missing. Shared by the write tools."""
    person_result = await ensure_person_registered(
        client, wa_id, person_name, person_phone, update_existing_name=True
    )
    person_id = person_result["person_id"]
    if not person_id:
        return None, None
    lead_id = await _find_or_create_lead(client, wa_id, person_id, person_name)
    return person_id, lead_id


def _resolve_name_phone(wa_id: str, person_name: Optional[str], person_phone: Optional[str]) -> tuple[str, str]:
    """Apply the shared CRM fallbacks: placeholder name and wa_id-as-phone."""
    name = person_name.strip() if isinstance(person_name, str) and person_name.strip() else "Paciente WhatsApp"
    phone = person_phone.strip() if isinstance(person_phone, str) and person_phone.strip() else wa_id
    return name, phone


# ── Atomic CRM write tools (one action per tool) ──────────────────────────────

@tool
async def save_patient(
    wa_id: str,
    person_name: Optional[str] = None,
    person_phone: Optional[str] = None,
    edad_paciente: Optional[int] = None,
    is_for_self: bool = True,
    nombre_paciente_de_otra_persona: Optional[str] = None,
    edad_paciente_de_otra_persona: Optional[int] = None,
) -> str:
    """Register or update ONLY the patient's identity and demographics in the CRM.

    Single responsibility: persist who the patient is (name, phone, age). Call it as soon as
    you know the patient's name/age. It does NOT touch insurance or appointments — use
    save_insurance and create_appointment for those.

    Args:
        wa_id: WhatsApp ID of the contact (e.g. '591XXXXXXXX').
        person_name: Full name of the person writing. If omitted, falls back to "Paciente WhatsApp".
        person_phone: Phone number digits only. If omitted, falls back to wa_id.
        edad_paciente: Age of the person writing (primary patient).
        is_for_self: True if the appointment is for the WhatsApp sender; False if for another person.
        nombre_paciente_de_otra_persona: Name if booking for someone else.
        edad_paciente_de_otra_persona: Age if booking for someone else.
    """
    person_name, person_phone = _resolve_name_phone(wa_id, person_name, person_phone)
    log = logger.bind(wa_id=wa_id, person_name=person_name)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            person_id, lead_id = await _resolve_person_and_lead(client, wa_id, person_name, person_phone)
            if not person_id:
                return json.dumps({"success": False, "error": "could not obtain person_id"})

            # Persist the writer's age on the person record (the only person field the CRM REST
            # API accepts). CI / insurance / status go on the lead via save_insurance.
            if edad_paciente is not None:
                try:
                    await _update_person_age(client, person_id, wa_id, person_name, person_phone, edad_paciente)
                    log.info("crm_person_age_updated", person_id=person_id)
                except Exception as attr_err:
                    log.warning("crm_person_age_failed", person_id=person_id, error=str(attr_err))

            lead_attrs: dict[str, Any] = {}
            patient_age = edad_paciente_de_otra_persona if not is_for_self else edad_paciente
            if patient_age is not None:
                lead_attrs["edad_lead"] = str(patient_age)
            if not is_for_self and nombre_paciente_de_otra_persona:
                lead_attrs["nombre_paciente_de_otra_persona"] = nombre_paciente_de_otra_persona
            if lead_attrs and lead_id:
                await client.put(
                    f"{_BASE}/api/v1/leads/attributes/edit/{lead_id}",
                    json=lead_attrs,
                    headers=_HEADERS,
                )
                log.info("crm_lead_attributes_updated", lead_id=lead_id, attrs=list(lead_attrs.keys()))

            log.info("crm_patient_saved", person_id=person_id, lead_id=lead_id)
            return json.dumps({"success": True, "person_id": person_id, "lead_id": lead_id})

    except Exception as e:
        log.exception("save_patient_failed", error=str(e))
        return json.dumps({"success": False, "error": str(e)})


@tool
async def save_insurance(
    wa_id: str,
    seguro_de_vida: str,
    numero_carnet: Optional[str] = None,
    estado_seguro: Optional[str] = None,
    person_name: Optional[str] = None,
    person_phone: Optional[str] = None,
) -> str:
    """Register ONLY the patient's insurance data on the CRM lead.

    Single responsibility: persist the insurance company, ID card (CI) and verification status.
    Call it after verify_insurance returns. It does NOT verify coverage (use verify_insurance)
    and does NOT create appointments.

    Args:
        wa_id: WhatsApp ID of the contact (e.g. '591XXXXXXXX').
        seguro_de_vida: Insurance name (e.g. 'Alianza', 'Nacional Vida', 'Membresía Odontoking').
        numero_carnet: Patient ID card number (CI).
        estado_seguro: Insurance status after verification (e.g. 'VIGENTE', 'VENCIDO').
        person_name: Full name of the person writing (for person/lead resolution).
        person_phone: Phone number digits only. If omitted, falls back to wa_id.
    """
    person_name, person_phone = _resolve_name_phone(wa_id, person_name, person_phone)
    log = logger.bind(wa_id=wa_id, person_name=person_name)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            person_id, lead_id = await _resolve_person_and_lead(client, wa_id, person_name, person_phone)
            if not lead_id:
                return json.dumps({"success": False, "error": "could not obtain lead_id"})

            insurance_id = _INSURANCE_ID_MAP.get((seguro_de_vida or "").lower().strip())
            if insurance_id:
                await client.put(
                    f"{_BASE}/api/v1/leads/attributes/edit/{lead_id}",
                    json={"insurance": [insurance_id]},
                    headers=_HEADERS,
                )
                log.info("crm_insurance_registered", lead_id=lead_id, insurance_id=insurance_id)

            lead_attrs: dict[str, Any] = {}
            if numero_carnet:
                lead_attrs["ci"] = numero_carnet
            if estado_seguro:
                lead_attrs["estado_seguro_paciente_cita"] = estado_seguro
            if lead_attrs:
                await client.put(
                    f"{_BASE}/api/v1/leads/attributes/edit/{lead_id}",
                    json=lead_attrs,
                    headers=_HEADERS,
                )
                log.info("crm_lead_attributes_updated", lead_id=lead_id, attrs=list(lead_attrs.keys()))

            log.info("crm_insurance_saved", person_id=person_id, lead_id=lead_id)
            return json.dumps({"success": True, "person_id": person_id, "lead_id": lead_id})

    except Exception as e:
        log.exception("save_insurance_failed", error=str(e))
        return json.dumps({"success": False, "error": str(e)})


@tool
async def create_appointment(
    wa_id: str,
    doctor_id: int,
    horario_cita: str,
    nombre_doctor: Optional[str] = None,
    products_name: Optional[str] = None,
    products_product_id: Optional[int] = None,
    motivo_consulta: Optional[str] = None,
    seguro_de_vida: Optional[str] = None,
    estado_seguro: Optional[str] = None,
    is_for_self: bool = True,
    nombre_paciente_de_otra_persona: Optional[str] = None,
    edad_paciente: Optional[int] = None,
    edad_paciente_de_otra_persona: Optional[int] = None,
    person_name: Optional[str] = None,
    person_phone: Optional[str] = None,
) -> str:
    """Create the appointment (meeting activity) in the CRM. ONE action: book the cita.

    Only call this AFTER the patient explicitly confirmed. It moves the lead to the scheduled
    stage and creates the meeting for the chosen doctor and slot. It is idempotent: a retried
    call for a slot already booked on the lead does not create a duplicate.

    Args:
        wa_id: WhatsApp ID of the contact (e.g. '591XXXXXXXX').
        doctor_id: Numeric ID of the chosen doctor from get_doctors().
        horario_cita: Appointment datetime in format 'DD/MM/YYYY HH:MM'.
        nombre_doctor: Doctor display name.
        products_name: Service/product name chosen by patient.
        products_product_id: Numeric ID of the product from get_services().
        motivo_consulta: Patient's reason for visit or main complaint.
        seguro_de_vida: Insurance name (for the appointment comment).
        estado_seguro: Insurance status (for the appointment comment).
        is_for_self: True if the appointment is for the WhatsApp sender; False if for another person.
        nombre_paciente_de_otra_persona: Name if booking for someone else.
        edad_paciente: Age of the person writing (primary patient).
        edad_paciente_de_otra_persona: Age if booking for someone else.
        person_name: Full name of the person writing.
        person_phone: Phone number digits only. If omitted, falls back to wa_id.
    """
    person_name, person_phone = _resolve_name_phone(wa_id, person_name, person_phone)
    log = logger.bind(wa_id=wa_id, person_name=person_name)

    if not (horario_cita and doctor_id):
        return json.dumps({
            "success": False,
            "appointment_registered": False,
            "error_type": "missing_appointment_fields",
            "message": "Faltan el doctor o el horario para crear la cita.",
        })

    schedule_from, schedule_to = _parse_appointment_datetime(horario_cita)
    if not schedule_from:
        return json.dumps({
            "success": False,
            "appointment_registered": False,
            "error_type": "invalid_appointment_datetime",
            "message": "No se pudo interpretar la fecha y hora de la cita.",
        })

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            person_id, lead_id = await _resolve_person_and_lead(client, wa_id, person_name, person_phone)
            if not (person_id and lead_id):
                return json.dumps({
                    "success": False,
                    "appointment_registered": False,
                    "error": "could not obtain person/lead id",
                })

            # Move the lead to the scheduled stage and attach the chosen product.
            update_body: dict[str, Any] = {
                "title": person_name,
                "description": f"{products_name or ''} - {nombre_doctor or ''}",
                "lead_value": 0,
                "lead_pipeline_stage_id": _LEAD_STAGE_AGENDADO,
                "lead_source_id": 6,
                "lead_type_id": 1,
                "user_id": _pick_agent_user(),
                "expected_close_date": (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d"),
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

            # Idempotency guard: if a meeting for this exact slot already exists on the lead,
            # do not create a second one. Protects against a retried confirmation where the
            # previous POST succeeded but its response was lost (patient re-sent "SÍ").
            duplicate_id = await _find_duplicate_meeting_id(client, lead_id, schedule_from)
            if duplicate_id is not None:
                log.info("crm_activity_duplicate_skipped", lead_id=lead_id, activity_id=duplicate_id, schedule=schedule_from)
                return json.dumps({
                    "success": True,
                    "person_id": person_id,
                    "lead_id": lead_id,
                    "appointment_registered": True,
                    "activity_id": duplicate_id,
                    "idempotent": True,
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
            # edad params are accepted for a complete signature; the age lives on the person/lead
            # via save_patient, so it is not duplicated in the appointment body.
            _ = (edad_paciente, edad_paciente_de_otra_persona)

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
            act_resp = await client.post(f"{_BASE}/api/v1/activities", json=activity_body, headers=_HEADERS)
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
            log.info("crm_activity_created", lead_id=lead_id, schedule=schedule_from)
            return json.dumps({
                "success": True,
                "person_id": person_id,
                "lead_id": lead_id,
                "appointment_registered": True,
            })

    except Exception as e:
        log.exception("create_appointment_failed", error=str(e))
        return json.dumps({"success": False, "appointment_registered": False, "error": str(e)})


async def _find_person_and_lead(
    client: httpx.AsyncClient, wa_id: str
) -> tuple[dict[str, Any] | None, int | None]:
    """Return (person, lead_id) for a wa_id, matching the lead by the person's WhatsApp email."""
    person = await find_person_by_wa_id(client, wa_id)
    if not person or not person.get("id"):
        return None, None
    person_email = _person_email_from_wa_id(wa_id)
    leads_resp = await client.get(
        f"{_BASE}/api/v1/leads",
        params={"sort": "id", "limit": 10, "person_id": str(person["id"])},
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
    return person, (matching[-1]["id"] if matching else None)


async def cancel_appointment(wa_id: str) -> dict[str, Any]:
    """Cancel a patient's latest appointment.

    Deletes the most recent not-done meeting activity (DELETE /api/v1/activities/{id}) and moves
    the lead to the cancelled pipeline stage. Used by the deterministic post-booking flow.
    """
    log = logger.bind(wa_id=wa_id)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            person, lead_id = await _find_person_and_lead(client, wa_id)
            if not person or not lead_id:
                return {"success": False, "error": "no_appointment_found"}

            acts_resp = await client.get(f"{_BASE}/api/v1/leads/{lead_id}/activities", headers=_HEADERS)
            acts_resp.raise_for_status()
            raw = acts_resp.json()
            activities = raw.get("data", raw) if isinstance(raw, dict) else raw
            meetings = [
                a for a in (activities if isinstance(activities, list) else [])
                if a.get("type") == "meeting" and not a.get("is_done")
            ]
            deleted_id = None
            if meetings:
                meetings.sort(key=lambda a: a.get("schedule_from", ""))
                meeting_id = meetings[-1].get("id")
                del_resp = await client.delete(f"{_BASE}/api/v1/activities/{meeting_id}", headers=_HEADERS)
                del_resp.raise_for_status()
                deleted_id = meeting_id
                log.info("crm_appointment_deleted", lead_id=lead_id, activity_id=meeting_id)

            await client.put(
                f"{_BASE}/api/v1/leads/stage/edit/{lead_id}",
                json={"lead_pipeline_stage_id": [6]},
                headers=_HEADERS,
            )
            log.info("crm_lead_stage_cancelled", lead_id=lead_id)
            return {"success": True, "lead_id": lead_id, "deleted_activity_id": deleted_id}
    except Exception as e:
        log.exception("cancel_appointment_failed", error=str(e))
        return {"success": False, "error": str(e)}


@tool
async def cancel_appointment_tool(wa_id: str) -> str:
    """Cancel the patient's latest appointment. ONE action: cancel the cita.

    Deletes the most recent upcoming meeting and moves the lead to the cancelled stage. Only
    call this when the patient explicitly asked to cancel and confirmed.

    Args:
        wa_id: WhatsApp ID of the patient (e.g. '591XXXXXXXX').
    """
    result = await cancel_appointment(wa_id)
    return json.dumps(result, ensure_ascii=False)


async def rename_person(wa_id: str, new_name: str) -> dict[str, Any]:
    """Update a CRM person's display name, preserving the stored age (job_title)."""
    log = logger.bind(wa_id=wa_id)
    clean = (new_name or "").strip()
    if not clean:
        return {"success": False, "error": "empty_name"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            person = await find_person_by_wa_id(client, wa_id)
            if not person or not person.get("id"):
                return {"success": False, "error": "person_not_found"}
            person_id = person["id"]
            phones = person.get("contact_numbers") or []
            phone = phones[0].get("value") if phones and isinstance(phones[0], dict) else None
            existing_age = str(person.get("job_title") or "")
            age_int = int(existing_age) if existing_age.isdigit() else None
            resp = await client.put(
                f"{_BASE}/api/v1/contacts/persons/{person_id}",
                json=_person_payload(wa_id, clean, phone or wa_id, age=age_int),
                headers=_HEADERS,
            )
            resp.raise_for_status()
            log.info("crm_person_renamed", person_id=person_id)
            return {"success": True, "person_id": person_id}
    except Exception as e:
        log.exception("rename_person_failed", error=str(e))
        return {"success": False, "error": str(e)}


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
