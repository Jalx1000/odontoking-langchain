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
    # "Nacional Seguros" is the same insurer as "Nacional Vida" — alias it to the same id so the
    # LLM/tool path registers it correctly (the deterministic intake already normalizes the name).
    "nacional seguros": 2,
    "nacional": 2,
    "membresía odontoking": 3,
    "membresia odontoking": 3,
    "no tengo seguro": 65,
}

# CRM lead pipeline stages.
_LEAD_STAGE_CONSULTA = 1   # first contact (the conversation lead — NOT a cita)
_LEAD_STAGE_CANCELADO = 6  # cancelled
_LEAD_STAGE_AGENDADO = 7   # appointment scheduled

# Stage id → human label for get_citas. Unknown ids fall back to the stage name carried on the
# lead payload (Krayin returns lead_pipeline_stage.name), else "Desconocido".
_STAGE_LABELS = {
    1: "Consulta",
    6: "Cancelado",
    7: "Agendado",
}

# Stages that are NOT a real cita: the Consulta lead is the conversation lead, every other lead
# (Agendado / Cancelado / Atendida / …) is one cita. Model: 1 lead-cita = 1 cita.
_NON_CITA_STAGES = {_LEAD_STAGE_CONSULTA}

_AGENT_USERS_WEEKDAY = [2, 3, 5, 7]
_AGENT_USERS_SUNDAY = [7,8]


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
    """Ensure the wa_id is a number."""
    wa_id = wa_id.strip()
    wa_id = wa_id.replace("+", "")
    wa_id = wa_id.replace(" ", "")
    return f"{wa_id}"


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


# ── Lead helpers (matched by person.id, not by the raw wa_id string) ──────────
#
# The lead↔person link is resolved by person.id — matching on the contact number string failed
# whenever the number carried a "+" or spaces (root cause of ~1/3 of leads never linked).

def _lead_person_id(ld: dict[str, Any]) -> Optional[int]:
    """Return the person id attached to a lead, as int, or None."""
    pid = (ld.get("person") or {}).get("id")
    try:
        return int(pid) if pid is not None else None
    except (ValueError, TypeError):
        return None


def _lead_stage_id(ld: dict[str, Any]) -> Optional[int]:
    """Return the lead's pipeline-stage id, from the flat field or the nested object."""
    sid = ld.get("lead_pipeline_stage_id")
    if sid is None:
        sid = (ld.get("lead_pipeline_stage") or {}).get("id")
    try:
        return int(sid) if sid is not None else None
    except (ValueError, TypeError):
        return None


def _lead_stage_label(ld: dict[str, Any]) -> str:
    """Human status for a cita: known stage id → label, else the payload's stage name."""
    sid = _lead_stage_id(ld)
    if sid in _STAGE_LABELS:
        return _STAGE_LABELS[sid]
    name = (ld.get("lead_pipeline_stage") or {}).get("name")
    return name if isinstance(name, str) and name.strip() else "Desconocido"


def _lead_product_name(ld: dict[str, Any]) -> str:
    """First product/service name on the lead (products may be a dict or a list)."""
    products = ld.get("products")
    values = products.values() if isinstance(products, dict) else products
    for v in values if isinstance(values, (list, type({}.values()))) else []:
        if isinstance(v, dict) and v.get("name"):
            return str(v["name"])
    return ""


def _lead_doctor_name(ld: dict[str, Any]) -> str:
    """Doctor name stored on the cita lead: description is '<servicio> - <doctor>'."""
    desc = ld.get("description") or ""
    if " - " in desc:
        return desc.split(" - ", 1)[1].strip()
    return ""


async def _search_leads_by_person(client: httpx.AsyncClient, person_id: int) -> list[dict[str, Any]]:
    """All leads whose person.id == person_id (server-filtered, then verified by person.id)."""
    resp = await client.get(
        f"{_BASE}/api/v1/leads/search",
        params={"search": str(person_id), "searchFields": "person_id:=;", "limit": 50},
        headers=_HEADERS,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    return [ld for ld in data if isinstance(ld, dict) and _lead_person_id(ld) == int(person_id)]


async def _latest_meeting(client: httpx.AsyncClient, lead_id: int) -> Optional[dict[str, Any]]:
    """Latest meeting activity on a lead (by schedule_from), or None."""
    resp = await client.get(f"{_BASE}/api/v1/leads/{lead_id}/activities", headers=_HEADERS)
    resp.raise_for_status()
    raw = resp.json()
    activities = raw.get("data", raw) if isinstance(raw, dict) else raw
    meetings = [
        a for a in (activities if isinstance(activities, list) else [])
        if isinstance(a, dict) and a.get("type") == "meeting"
    ]
    if not meetings:
        return None
    meetings.sort(key=lambda a: a.get("schedule_from") or "")
    return meetings[-1]


def _doctor_from_comment(comment: Optional[str]) -> str:
    """Pull the 'Doctor: X' line out of an appointment comment."""
    for line in (comment or "").splitlines():
        if line.lower().startswith("doctor:"):
            return line.split(":", 1)[1].strip()
    return ""


def _service_from_title(title: Optional[str]) -> str:
    """Meeting title is '<paciente> - <servicio>' → return the servicio part."""
    if title and " - " in title:
        return title.split(" - ", 1)[1].strip()
    return ""


async def _summarize_cita(client: httpx.AsyncClient, ld: dict[str, Any]) -> dict[str, Any]:
    """Build one cita summary {lead_id, fecha, hora, doctor, servicio, estado} from a cita-lead."""
    lead_id = ld.get("id")
    servicio = _lead_product_name(ld)
    doctor = _lead_doctor_name(ld)
    estado = _lead_stage_label(ld)
    fecha = hora = ""
    try:
        meeting = await _latest_meeting(client, lead_id) if lead_id is not None else None
    except Exception as e:
        logger.warning("cita_meeting_fetch_failed", lead_id=lead_id, error=str(e))
        meeting = None
    if meeting:
        sf = meeting.get("schedule_from") or ""
        fecha, _, hora = sf.partition(" ")
        hora = hora[:5]
        if not doctor:
            doctor = _doctor_from_comment(meeting.get("comment"))
        if not servicio:
            servicio = _service_from_title(meeting.get("title"))
    return {
        "lead_id": lead_id,
        "fecha": fecha,
        "hora": hora,
        "doctor": doctor,
        "servicio": servicio,
        "estado": estado,
    }


async def _collect_citas(client: httpx.AsyncClient, person_id: int) -> list[dict[str, Any]]:
    """Return ALL of a person's citas (one per cita-lead), excluding the conversation lead.

    Model: 1 lead-cita = 1 cita. Shared by get_citas (on-demand tool) and the pre-chat context
    preload so both read the full history the same way — never just the most recent lead.
    """
    all_leads = await _search_leads_by_person(client, int(person_id))
    cita_leads = [ld for ld in all_leads if _lead_stage_id(ld) not in _NON_CITA_STAGES]
    citas = [await _summarize_cita(client, ld) for ld in cita_leads]
    citas.sort(key=lambda c: (c.get("fecha") or "", c.get("hora") or ""))
    return citas


async def preload_patient_citas(
    wa_id: str,
) -> tuple[Optional[int], list[dict[str, Any]], Optional[int]]:
    """Best-effort: resolve (person_id, all citas, active cita lead_id) for a wa_id.

    Injected into the system prompt BEFORE chatting so the agent already knows the patient's full
    appointment history (past + upcoming) and never re-asks what it can see. Returns (None, [],
    None) on any failure — the conversation must never break because the CRM was slow.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            person = await find_person_by_wa_id(client, wa_id)
            if not person or not person.get("id"):
                return None, [], None
            person_id = int(person["id"])
            citas = await _collect_citas(client, person_id)
            active_label = _STAGE_LABELS[_LEAD_STAGE_AGENDADO]
            activas = [c for c in citas if c.get("estado") == active_label]
            cita_activa = activas[-1]["lead_id"] if activas else None
            return person_id, citas, cita_activa
    except Exception as e:
        logger.warning("preload_patient_citas_failed", wa_id=wa_id, error=str(e))
        return None, [], None


def _person_payload(
    wa_id: str,
    person_name: str,
    person_phone: str,
    *,
    age: int | None = None,
) -> dict[str, Any]:
    clean_name = person_name.strip() if isinstance(person_name, str) and person_name.strip() else "Paciente WhatsApp"
    clean_phone = person_phone.strip() if isinstance(person_phone, str) and person_phone.strip() else wa_id
    payload: dict[str, Any] = {
        "name": clean_name,
        "emails": [],
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

    payload = _person_payload(wa_id, clean_name, clean_phone)
    create_resp = await client.post(
        f"{_BASE}/api/v1/contacts/persons",
        json=payload,
        headers=_HEADERS,
    )
    
    if create_resp.status_code == 422:
        try:
            error_detail = create_resp.json()
        except Exception:
            error_detail = {"text": create_resp.text}
        log.error("crm_person_422_error", payload=payload, response=error_detail)
        create_resp.raise_for_status()
    
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
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            all_leads = await _search_leads_by_person(client, int(person_id))
            # Idempotent: if the person is already in the pipeline (any lead) do not create a
            # second conversation lead. Safe against Meta/CRM webhook retries.
            if all_leads:
                lead_id = all_leads[-1]["id"]
                log.info("crm_lead_exists_skip_create", lead_id=lead_id)
                return {"success": True, "lead_id": lead_id, "created": False}

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


async def _find_cita_lead_for_slot(
    client: httpx.AsyncClient, person_id: int, schedule_from: str
) -> Optional[int]:
    """Return the id of an existing cita-lead already holding a not-done meeting at this slot.

    Idempotency for the 1-lead-per-cita model: scans the person's Agendado leads and reuses the
    one that already has a meeting at the same minute, so a retried confirmation does not create a
    second lead-cita. Fails open (returns None) so a legitimate new booking is never blocked.
    """
    if not (schedule_from or "").strip():
        return None
    try:
        all_leads = await _search_leads_by_person(client, int(person_id))
        for ld in all_leads:
            if _lead_stage_id(ld) != _LEAD_STAGE_AGENDADO:
                continue
            lead_id = ld.get("id")
            if lead_id is not None and await _find_duplicate_meeting_id(client, lead_id, schedule_from) is not None:
                return lead_id
    except Exception as e:
        logger.warning("crm_cita_slot_check_failed", person_id=person_id, error=str(e))
    return None


async def _write_cita_attributes(
    client: httpx.AsyncClient,
    lead_id: int,
    *,
    numero_carnet: Optional[str],
    seguro_de_vida: Optional[str],
    estado_seguro: Optional[str],
    patient_age: Optional[int],
) -> None:
    """Copy carnet / insurance / estado / edad onto a cita-lead via the attributes endpoint."""
    lead_attrs: dict[str, Any] = {}
    if numero_carnet:
        lead_attrs["ci"] = numero_carnet
    if estado_seguro:
        lead_attrs["estado_seguro_paciente_cita"] = estado_seguro
    if patient_age is not None:
        lead_attrs["edad_lead"] = str(patient_age)
    if lead_attrs:
        await client.put(
            f"{_BASE}/api/v1/leads/attributes/edit/{lead_id}",
            json=lead_attrs,
            headers=_HEADERS,
        )
    insurance_id = _INSURANCE_ID_MAP.get((seguro_de_vida or "").lower().strip())
    if insurance_id:
        await client.put(
            f"{_BASE}/api/v1/leads/attributes/edit/{lead_id}",
            json={"insurance": [insurance_id]},
            headers=_HEADERS,
        )


# ── Shared person/lead resolution (used by every write tool) ──────────────────

async def _find_or_create_lead(
    client: httpx.AsyncClient, wa_id: str, person_id: int, person_name: str | None
) -> Optional[int]:
    """Return the person's CONVERSATION lead id (Consulta stage), creating it if none exists.

    This resolves the substrate for save_patient / save_insurance — the CI/insurance the patient
    gives during intake live here. It intentionally does NOT reuse a cita lead (Agendado/Cancelado):
    each cita is its own lead created by create_appointment (model: 1 lead-cita = 1 cita). Matched
    by person.id so a "+"/spaces in the number never breaks the link.
    """
    all_leads = await _search_leads_by_person(client, int(person_id))
    conversation = [ld for ld in all_leads if _lead_stage_id(ld) == _LEAD_STAGE_CONSULTA]
    if conversation:
        return conversation[-1]["id"]

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
        # str(e) is empty for timeouts (ReadTimeout) — capture the exception type and, when the
        # CRM did respond with an error status, its actual status + body so we can see what the
        # CRM returned instead of a blank error.
        resp = getattr(e, "response", None)
        crm_status = getattr(resp, "status_code", None) if resp is not None else None
        try:
            crm_body = resp.text[:1000] if resp is not None else ""
        except Exception:
            crm_body = "<unreadable>"
        log.exception(
            "save_patient_failed",
            error_type=type(e).__name__,
            error=repr(e),
            crm_status=crm_status,
            crm_body=crm_body,
        )
        return json.dumps({"success": False, "error": type(e).__name__, "crm_status": crm_status, "crm_body": crm_body})


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
    numero_carnet: Optional[str] = None,
    is_for_self: bool = True,
    nombre_paciente_de_otra_persona: Optional[str] = None,
    edad_paciente: Optional[int] = None,
    edad_paciente_de_otra_persona: Optional[int] = None,
    person_name: Optional[str] = None,
    person_phone: Optional[str] = None,
) -> str:
    """Create ONE appointment as its OWN lead-cita in the CRM. ONE action: book the cita.

    Model: 1 lead-cita = 1 cita. Every call creates a NEW lead in the Agendado stage (it does NOT
    reuse the conversation lead), so booking twice — or for two different people — yields two
    independent citas that never overwrite each other's doctor/slot/service. The chosen service is
    attached as the lead's product, and the carnet/seguro/estado/edad are copied onto the lead as
    attributes; the doctor and datetime live on the lead's meeting activity.

    Only call this AFTER the patient explicitly confirmed. Idempotent: a retried call for a slot
    the patient already has booked (a cita-lead with a meeting at that time) does not duplicate it.

    Args:
        wa_id: WhatsApp ID of the contact (e.g. '591XXXXXXXX').
        doctor_id: Numeric ID of the chosen doctor from get_doctors().
        horario_cita: Appointment datetime in format 'DD/MM/YYYY HH:MM'.
        nombre_doctor: Doctor display name.
        products_name: Service/product name chosen by patient.
        products_product_id: Numeric ID of the product from get_services().
        motivo_consulta: Patient's reason for visit or main complaint.
        seguro_de_vida: Insurance name (copied to the cita-lead + comment).
        estado_seguro: Insurance status (copied to the cita-lead + comment).
        numero_carnet: Patient ID card number (CI), copied to the cita-lead.
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
            person_result = await ensure_person_registered(
                client, wa_id, person_name, person_phone, update_existing_name=True
            )
            person_id = person_result["person_id"]
            if not person_id:
                return json.dumps({
                    "success": False,
                    "appointment_registered": False,
                    "error": "could not obtain person_id",
                })

            # Idempotency guard: if the patient already has a cita-lead with a not-done meeting at
            # this exact slot, reuse it instead of creating a second cita. Protects against a
            # retried confirmation whose previous POST succeeded but response was lost ("SÍ" again).
            existing = await _find_cita_lead_for_slot(client, person_id, schedule_from)
            if existing is not None:
                log.info("crm_cita_duplicate_skipped", lead_id=existing, schedule=schedule_from)
                return json.dumps({
                    "success": True,
                    "person_id": person_id,
                    "lead_id": existing,
                    "appointment_registered": True,
                    "idempotent": True,
                })

            # Create a NEW lead for THIS cita (Agendado stage). Never reuse the conversation lead:
            # each cita is independent, so booking twice / for two people yields two lead-citas.
            lead_body: dict[str, Any] = {
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
                lead_body["products"] = {
                    "product_0": {
                        "name": products_name,
                        "product_id": str(products_product_id),
                        "price": "0",
                        "quantity": 1,
                    }
                }
            lead_resp = await client.post(f"{_BASE}/api/v1/leads", json=lead_body, headers=_HEADERS)
            lead_resp.raise_for_status()
            lead_id = lead_resp.json().get("data", {}).get("id")
            if not lead_id:
                return json.dumps({
                    "success": False,
                    "appointment_registered": False,
                    "error": "could not create cita lead",
                    "person_id": person_id,
                })
            log.info("crm_cita_lead_created", lead_id=lead_id)

            # Copy carnet / seguro / estado / edad onto the cita-lead as attribute_values.
            await _write_cita_attributes(
                client,
                lead_id,
                numero_carnet=numero_carnet,
                seguro_de_vida=seguro_de_vida,
                estado_seguro=estado_seguro,
                patient_age=(edad_paciente_de_otra_persona if not is_for_self else edad_paciente),
            )

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


async def _latest_active_cita_lead(client: httpx.AsyncClient, wa_id: str) -> Optional[int]:
    """Return the most recent still-active (Agendado) cita-lead id for a wa_id, or None."""
    person = await find_person_by_wa_id(client, wa_id)
    if not person or not person.get("id"):
        return None
    all_leads = await _search_leads_by_person(client, int(person["id"]))
    active = [ld for ld in all_leads if _lead_stage_id(ld) == _LEAD_STAGE_AGENDADO]
    if not active:
        return None
    active.sort(key=lambda ld: ld.get("id") or 0)
    return active[-1].get("id")


async def cancel_appointment(wa_id: str, lead_id: Optional[int] = None) -> dict[str, Any]:
    """Cancel ONE cita — the one identified by lead_id, or the latest active cita if omitted.

    Deletes that cita-lead's not-done meeting (DELETE /api/v1/activities/{id}) and moves ONLY that
    lead to the cancelled stage — other citas of the same patient are untouched. When lead_id is
    omitted (deterministic post-booking / reschedule flow) it targets the latest active cita.
    """
    log = logger.bind(wa_id=wa_id, lead_id=lead_id)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            if lead_id is None:
                lead_id = await _latest_active_cita_lead(client, wa_id)
            if not lead_id:
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
async def cancel_appointment_tool(wa_id: str, lead_id: Optional[int] = None) -> str:
    """Cancel ONE cita. ONE action: cancel the cita.

    Moves that specific cita-lead to the cancelled stage and deletes its meeting — other citas of
    the patient are untouched. Only call this when the patient explicitly asked to cancel and
    confirmed. When the patient has 2+ active citas, first call get_citas and pass the chosen
    cita's lead_id here so the right one is cancelled.

    Args:
        wa_id: WhatsApp ID of the patient (e.g. '591XXXXXXXX').
        lead_id: The cita's lead_id from get_citas. Omit only when the patient has a single active
            cita (then the latest active one is cancelled).
    """
    result = await cancel_appointment(wa_id, lead_id=lead_id)
    return json.dumps(result, ensure_ascii=False)


async def reschedule_appointment(
    wa_id: str,
    lead_id: int,
    horario_cita: str,
    doctor_id: Optional[int] = None,
    nombre_doctor: Optional[str] = None,
) -> dict[str, Any]:
    """Move ONE existing cita-lead to a new datetime (and optionally a new doctor).

    Rebuilds the cita's meeting at the new slot on the SAME lead (keeping stage Agendado), so the
    cita keeps its identity and history. Other citas are untouched.
    """
    log = logger.bind(wa_id=wa_id, lead_id=lead_id)
    schedule_from, schedule_to = _parse_appointment_datetime(horario_cita)
    if not schedule_from:
        return {"success": False, "error": "invalid_appointment_datetime"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Idempotency: if the meeting is already at the requested slot, do nothing.
            if await _find_duplicate_meeting_id(client, lead_id, schedule_from) is not None:
                return {"success": True, "lead_id": lead_id, "idempotent": True}

            old_meeting = await _latest_meeting(client, lead_id)
            person_ids: list[str] = []
            doctor_ids: list[str] = []
            title = "Cita"
            comment = ""
            if old_meeting:
                title = old_meeting.get("title") or title
                comment = old_meeting.get("comment") or ""
                for p in (old_meeting.get("participants") or []):
                    if isinstance(p, dict) and p.get("person"):
                        person_ids.append(str(p["person"].get("id")))
                    if isinstance(p, dict) and p.get("doctor"):
                        doctor_ids.append(str(p["doctor"].get("id")))
                if not old_meeting.get("is_done"):
                    await client.delete(f"{_BASE}/api/v1/activities/{old_meeting.get('id')}", headers=_HEADERS)

            if doctor_id is not None:
                doctor_ids = [str(doctor_id)]
            activity_body: dict[str, Any] = {
                "lead_id": lead_id,
                "title": title,
                "type": "meeting",
                "schedule_from": schedule_from,
                "schedule_to": schedule_to,
                "location": "Consultorio",
                "comment": comment,
                "participants": {
                    "persons": person_ids or [],
                    "users": ["1"],
                    "doctors": doctor_ids or [],
                },
            }
            act_resp = await client.post(f"{_BASE}/api/v1/activities", json=activity_body, headers=_HEADERS)
            act_resp.raise_for_status()
            # Ensure the lead is back in the Agendado stage after a reschedule.
            await client.put(
                f"{_BASE}/api/v1/leads/stage/edit/{lead_id}",
                json={"lead_pipeline_stage_id": [_LEAD_STAGE_AGENDADO]},
                headers=_HEADERS,
            )
            log.info("crm_cita_rescheduled", lead_id=lead_id, schedule=schedule_from)
            return {"success": True, "lead_id": lead_id, "schedule_from": schedule_from}
    except Exception as e:
        log.exception("reschedule_appointment_failed", error=str(e))
        return {"success": False, "error": str(e)}


@tool
async def reschedule_appointment_tool(
    wa_id: str,
    lead_id: int,
    horario_cita: str,
    doctor_id: Optional[int] = None,
    nombre_doctor: Optional[str] = None,
) -> str:
    """Reschedule ONE cita to a new day/time. ONE action: move the cita.

    Points a specific cita (its lead_id from get_citas) to a new datetime on the same lead. With
    2+ active citas, call get_citas first and pass the chosen cita's lead_id. Only call after the
    patient confirmed the new slot.

    Args:
        wa_id: WhatsApp ID of the patient (e.g. '591XXXXXXXX').
        lead_id: The cita's lead_id from get_citas.
        horario_cita: New appointment datetime in format 'DD/MM/YYYY HH:MM'.
        doctor_id: New doctor id, only if the doctor changes. Omit to keep the same doctor.
        nombre_doctor: New doctor display name, if the doctor changes.
    """
    result = await reschedule_appointment(wa_id, lead_id, horario_cita, doctor_id, nombre_doctor)
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
    """Get ALL of a patient's citas by their WhatsApp ID (one per cita-lead).

    Model: each cita is its own lead, so this returns the FULL history — every visit, past and
    upcoming — not just the latest. Use it to review what the patient already booked/attended
    before scheduling, and to pick which cita to cancel/reschedule when there are several.

    Returns {"citas": [{lead_id, fecha, hora, doctor, servicio, estado}, ...]} sorted by date.
    `estado` is the pipeline stage label (Agendado / Cancelado / …). Pass a cita's `lead_id` to
    cancel_appointment_tool / reschedule_appointment_tool to act on that specific cita.

    Args:
        wa_id: WhatsApp ID of the patient (e.g. '591XXXXXXXX').
    """
    log = logger.bind(wa_id=wa_id)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            person = await find_person_by_wa_id(client, wa_id)
            if not person or not person.get("id"):
                log.info("get_citas_person_not_found")
                return json.dumps({"citas": [], "message": "patient_not_found_in_crm"})

            person_id = int(person["id"])
            citas = await _collect_citas(client, person_id)
            log.info("get_citas_fetched", person_id=person_id, count=len(citas))
            return json.dumps({"citas": citas}, ensure_ascii=False)

    except httpx.HTTPStatusError as e:
        log.exception("get_citas_http_error", status=e.response.status_code, error=str(e))
        return json.dumps({"error": f"API returned {e.response.status_code}", "citas": []})
    except Exception as e:
        log.exception("get_citas_failed", error=str(e))
        return json.dumps({"error": str(e), "citas": []})


def _fetch_transcript(wa_id: str, max_messages: int) -> str:
    """Read and format the last N messages from chat_histories_odonto (sync)."""
    sid = f"{wa_id}"
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
                f"{_BASE}/api/v1/leads/search",
                params={"search": str(person_id), "searchFields": "person_id:=;", "limit": 10},
                headers=_HEADERS,
            )
            leads_resp.raise_for_status()
            all_leads = leads_resp.json().get("data", [])
            matching = [
                ld for ld in all_leads
                if any(
                    (e.get("value", "")).lower() == wa_id.lower()
                    for e in (ld.get("person", {}).get("contact_numbers") or [])
                )
            ]
            if not matching:
                return json.dumps({"success": False, "message": "no_lead_found_for_patient"})

            lead_id = matching[-1]["id"]
            # Nota de historial deshabilitada temporalmente (POST /activities comentado).
            # note_title = f"Historial WhatsApp — {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            # note_resp = await client.post(
            #     f"{_BASE}/api/v1/activities",
            #     json={
            #         "lead_id": lead_id,
            #         "title": note_title,
            #         "type": "note",
            #         "comment": transcript,
            #     },
            #     headers=_HEADERS,
            # )
            # note_resp.raise_for_status()
            log.info("crm_transcript_sync_skipped", lead_id=lead_id)
            return json.dumps({"success": True, "lead_id": lead_id, "note_created": False})

    except Exception as e:
        log.exception("sync_transcript_to_crm_failed", error=str(e))
        return json.dumps({"success": False, "error": str(e)})
