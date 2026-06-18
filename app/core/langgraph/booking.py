"""Deterministic Phase-2 booking controller — drives steps 7-11 after intake.

The LLM proved unreliable rendering schedules (duplicated slots, mixed days under one label,
hallucinated a time it then denied, skipped the day step, timed out). Like the intake
controller, this module owns the doctor → day → time → confirm flow deterministically: it
calls the Odontoking tools directly and builds each message from the EXACT API data, so a
slot is never fabricated and the order is guaranteed.

Every motivo runs deterministically: the 4 fixed buttons map to a specialty via keywords, and
free-text / "Otro" descriptions are classified into a specialty+service by a single bounded LLM
call (molestia_classifier) that returns only IDs — never conversation. So the flow can neither
loop nor hallucinate a slot regardless of how the patient phrased their molestia.

State is the same per-wa_id dict used by intake (extended with the booking_* fields seeded in
intake.new_state), persisted in cache_service so it survives across WhatsApp turns.
"""

import json
import re
import unicodedata
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.core.langgraph.molestia_classifier import classify_molestia
from app.core.langgraph.tools.crm import update_crm
from app.core.langgraph.tools.odontoking import (
    get_doctor_schedule,
    get_doctors,
    get_services,
    get_specialties,
)
from app.core.langgraph.intake import crm_display_name
from app.core.logging import logger

_DURATION_DEFAULT = 60  # every service returns duration_minutes=None, so the schedule default
_TZ_BOLIVIA = ZoneInfo("America/La_Paz")

# motivo (normalized) → ordered specialty-name keywords matched against get_specialties.
# Tunable per clinic. "Limpieza" → Periodoncia matches the clinic's observed behavior.
MOTIVO_SPECIALTY: dict[str, list[str]] = {
    "dolor dental": ["endodon", "general"],
    "diente quebrado": ["rehabilita", "estetica", "general"],
    "encia inflamada": ["periodon"],
    "limpieza": ["periodon", "general"],
}

# motivo (normalized) → service-name keywords (best-effort, for the CRM product link).
MOTIVO_SERVICE_KW: dict[str, list[str]] = {
    "dolor dental": ["consulta", "emergencia"],
    "diente quebrado": ["restaura"],
    "encia inflamada": ["periodon", "limpieza"],
    "limpieza": ["limpieza", "profilaxis"],
}


def _norm(s: Optional[str]) -> str:
    """Lowercase, strip accents/whitespace — for accent-insensitive matching."""
    text = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn").strip()


def motivo_is_deterministic(motivo: Optional[str]) -> bool:
    """True when the motivo is one of the fixed categories we can route deterministically."""
    return _norm(motivo) in MOTIVO_SPECIALTY


class BookingResult:
    """Outcome of one booking turn: the updated state, the reply to send, and a done flag."""

    def __init__(self, state: dict, reply: Optional[str], done: bool) -> None:
        """Store the state, the WhatsApp reply (always sent), and whether the flow is finished."""
        self.state = state
        self.reply = reply
        self.done = done


# ── Tool wrappers (patched in tests) ──────────────────────────────────────────

async def _fetch_specialties() -> list[dict]:
    raw = json.loads(await get_specialties.ainvoke({}))
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []


async def _fetch_services() -> list[dict]:
    raw = json.loads(await get_services.ainvoke({"keyword": ""}))
    data = raw.get("data", []) if isinstance(raw, dict) else raw
    return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []


async def _fetch_doctors() -> list[dict]:
    raw = json.loads(await get_doctors.ainvoke({}))
    data = raw.get("data", []) if isinstance(raw, dict) else raw
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


async def _fetch_schedule(doctor_id: int) -> list[dict]:
    raw = json.loads(await get_doctor_schedule.ainvoke({"id_doctor": doctor_id, "duration_minutes": _DURATION_DEFAULT}))
    sched = raw.get("schedule", []) if isinstance(raw, dict) else []
    return [d for d in sched if isinstance(d, dict)] if isinstance(sched, list) else []


async def _book_crm(payload: dict) -> dict:
    try:
        raw = await update_crm.ainvoke(payload)
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"success": False, "appointment_registered": False}


# ── Matching helpers ──────────────────────────────────────────────────────────

def _match_specialty_id(motivo: str, specialties: list[dict]) -> Optional[int]:
    for kw in MOTIVO_SPECIALTY.get(_norm(motivo), []):
        for sp in specialties:
            if kw in _norm(sp.get("name")):
                return sp.get("id")
    return None


def _match_service(motivo: str, services: list[dict]) -> Optional[dict]:
    for kw in MOTIVO_SERVICE_KW.get(_norm(motivo), []):
        for sv in services:
            if kw in _norm(sv.get("name")):
                return sv
    return None


def _choose(user_text: Optional[str], titles: list[str]) -> Optional[int]:
    """Map the patient's reply to a 1-based option index.

    Handles both a typed number and a tapped WhatsApp button (which sends the option title,
    possibly truncated). Returns None when nothing matches.
    """
    text = _norm(user_text)
    if not text or not titles:
        return None
    m = re.match(r"^(\d+)", text)
    if m:
        n = int(m.group(1))
        if 1 <= n <= len(titles):
            return n
    for i, title in enumerate(titles, start=1):
        nt = _norm(title)
        if nt and (nt == text or nt.startswith(text) or text.startswith(nt)):
            return i
    return None


_AFFIRMATIVE = {"si", "sí", "s", "yes", "y", "confirmo", "confirmar", "correcto", "ok", "okay", "dale"}


def _is_affirmative(user_text: Optional[str]) -> bool:
    return _norm(user_text) in _AFFIRMATIVE


# ── Message builders ──────────────────────────────────────────────────────────

def _numbered(titles: list[Any]) -> str:
    return "\n".join(f"{i}. {t}" for i, t in enumerate(titles, start=1))


def _patient_name(state: dict) -> str:
    if state.get("is_for_self") is False:
        return state.get("tercero_nombre") or "el paciente"
    return state.get("nombre") or "el paciente"


def _patient_age(state: dict):
    return state.get("tercero_edad") if state.get("is_for_self") is False else state.get("edad")


def _fmt_time(t: Optional[str]) -> str:
    """'17:00:00' → '17:00'."""
    return (t or "")[:5]


def _fmt_date(date_iso: Optional[str]) -> str:
    """'2026-06-18' → '18/06/2026'."""
    if not date_iso:
        return ""
    try:
        y, m, d = date_iso.split("-")
        return f"{d}/{m}/{y}"
    except (ValueError, AttributeError):
        return date_iso


def _future_slots(date_iso: Optional[str], slots: list[dict]) -> list[dict]:
    """Drop slots already past for TODAY (Bolivia time); other days pass through unchanged.

    Offering a slot earlier than the current time is useless, so when the chosen day is today we
    keep only slots whose start_time is after the current local time.
    """
    now = datetime.now(_TZ_BOLIVIA)
    if date_iso != now.date().isoformat():
        return slots
    now_hms = now.strftime("%H:%M:%S")
    return [s for s in slots if (s.get("start_time") or "") > now_hms]


# ── Phase transitions ─────────────────────────────────────────────────────────

async def _resolve_specialty_service(
    motivo: str, specialties: list[dict], services: list[dict]
) -> tuple[Optional[int], Optional[dict]]:
    """Resolve motivo → (specialty_id, service).

    Fixed motivos use keywords; anything else (or a fixed motivo without a keyword match) is
    classified by the bounded LLM (with its own fallback).
    """
    if motivo_is_deterministic(motivo):
        sid = _match_specialty_id(motivo, specialties)
        if sid:
            return sid, _match_service(motivo, services)
    return await classify_molestia(motivo, specialties, services)


async def _enter_doctor_phase(state: dict) -> BookingResult:
    motivo = state.get("motivo") or ""
    specialties = await _fetch_specialties()
    services = await _fetch_services()
    specialty_id, service = await _resolve_specialty_service(motivo, specialties, services)
    state["specialty_id"] = specialty_id
    if service:
        state["service_id"] = service.get("id")
        state["service_name"] = service.get("name")

    doctors = await _fetch_doctors()
    filtered = [
        d for d in doctors
        if specialty_id and any(s.get("id") == specialty_id for s in (d.get("specialties") or []))
    ]
    if not filtered:
        filtered = doctors  # fallback: never dead-end — offer all available doctors
    state["proposed_doctors"] = [{"id": d.get("id"), "name": d.get("name")} for d in filtered[:10]]
    state["booking_phase"] = "doctor"

    if not state["proposed_doctors"]:
        state["booking_phase"] = "done"
        return BookingResult(state, "Por el momento no hay doctores con disponibilidad. Intente más tarde, por favor 🙏.", True)

    # Neutral wording so any motivo (fixed label or free-text phrase) reads naturally.
    titles = [d["name"] for d in state["proposed_doctors"]]
    msg = f"Para agendar su cita, ¿con quién le gustaría atenderse? 😊\n\n{_numbered(titles)}"
    return BookingResult(state, msg, False)


async def _enter_dia_phase(state: dict) -> BookingResult:
    schedule = await _fetch_schedule(state["doctor_id"])
    # Keep only days that still have FUTURE slots (today's past hours are dropped).
    schedule = [
        d for d in schedule
        if d.get("date") and _future_slots(d["date"], d.get("slots") or [])
    ]
    state["schedule"] = schedule
    if not schedule:
        # No availability for this doctor → back to choosing another doctor.
        state["booking_phase"] = "doctor"
        titles = [d["name"] for d in state.get("proposed_doctors", [])]
        msg = (
            f"El/la Dr/a. {state.get('doctor_name')} no tiene horarios disponibles en los próximos días. "
            f"¿Con quién más le gustaría agendar? 😊\n\n{_numbered(titles)}"
        )
        return BookingResult(state, msg, False)

    state["booking_phase"] = "dia"
    titles = [d.get("day_label") or d["date"] for d in schedule]
    msg = f"¿Para qué día le gustaría agendar con el/la Dr/a. {state.get('doctor_name')}? 📅\n\n{_numbered(titles)}"
    return BookingResult(state, msg, False)


def _enter_hora_phase(state: dict, day: dict) -> BookingResult:
    # Drop past hours for today, then de-duplicate (the API sometimes repeats), sort ascending, cap 10.
    future = _future_slots(day.get("date"), day.get("slots") or [])
    seen = set()
    slots = []
    for s in sorted(future, key=lambda x: x.get("start_time", "")):
        key = (s.get("start_time"), s.get("end_time"))
        if key in seen:
            continue
        seen.add(key)
        slots.append(s)
    slots = slots[:10]

    if not slots:
        # All of this day's hours already passed → send the patient back to pick another day.
        state["booking_phase"] = "dia"
        titles = [d.get("day_label") or d["date"] for d in state.get("schedule", [])]
        msg = (
            f"Ya no hay horarios disponibles para el {day.get('day_label') or day.get('date')}. "
            f"¿Para qué otro día le gustaría agendar? 📅\n\n{_numbered(titles)}"
        )
        return BookingResult(state, msg, False)

    state["current_slots"] = slots
    state["chosen_date"] = day.get("date")
    state["chosen_day_label"] = day.get("day_label") or day.get("date")
    state["booking_phase"] = "hora"

    titles = [f"{_fmt_time(s.get('start_time'))} - {_fmt_time(s.get('end_time'))}" for s in slots]
    msg = (
        f"Horarios disponibles del/de la Dr/a. {state.get('doctor_name')} "
        f"para el {state['chosen_day_label']}:\n\n{_numbered(titles)}"
    )
    return BookingResult(state, msg, False)


def _enter_confirmar_phase(state: dict) -> BookingResult:
    state["booking_phase"] = "confirmar"
    lines = [
        "Por favor confirme su cita ✅:",
        "",
        f"👤 Paciente: {_patient_name(state)} ({_patient_age(state)})",
        f"🛠️ Servicio: {state.get('motivo')}",
        f"📅 Fecha: {_fmt_date(state.get('chosen_date'))}",
        f"⏰ Hora: {_fmt_time(state.get('chosen_start'))}",
        "",
        'Responda "SÍ" para confirmar.',
    ]
    return BookingResult(state, "\n".join(lines), False)


def _crm_payload(state: dict) -> dict:
    payload: dict = {
        "wa_id": state.get("wa_id"),
        "person_name": crm_display_name(state.get("nombre_whatsapp"), state.get("nombre")),
        "person_phone": state.get("wa_id"),
        "edad_paciente": state.get("edad"),
        "is_for_self": bool(state.get("is_for_self")),
        "paciente_antiguo": bool(state.get("es_antiguo")),
        "motivo_consulta": state.get("motivo"),
        "doctor_id": state.get("doctor_id"),
        "nombre_doctor": state.get("doctor_name"),
        "horario_cita": f"{_fmt_date(state.get('chosen_date'))} {_fmt_time(state.get('chosen_start'))}",
        "es_cita_confirmada": True,
    }
    if state.get("service_name"):
        payload["products_name"] = state["service_name"]
    if state.get("service_id"):
        payload["products_product_id"] = state["service_id"]
    if state.get("is_for_self") is False:
        payload["nombre_paciente_de_otra_persona"] = state.get("tercero_nombre")
        payload["edad_paciente_de_otra_persona"] = state.get("tercero_edad")
    if state.get("seguro") and state.get("seguro") != "No tengo seguro":
        payload["seguro_de_vida"] = state.get("seguro")
        payload["numero_carnet"] = state.get("ci")
        payload["estado_seguro"] = state.get("seguro_estado")
    return payload


async def _do_booking(state: dict) -> BookingResult:
    result = await _book_crm(_crm_payload(state))
    if not (result.get("success") and result.get("appointment_registered")):
        logger.warning("booking_crm_not_registered", wa_id=state.get("wa_id"), result=result)
        return BookingResult(
            state,
            "Disculpe, tuvimos un inconveniente técnico al registrar su cita. ¿Podría confirmar nuevamente respondiendo 'SÍ' en un momento? 🙏",
            False,
        )
    state["booking_phase"] = "done"
    state["booking_confirmado"] = True
    lines = [
        f"Perfecto ✅ {_patient_name(state)}, su cita ha sido agendada exitosamente con el/la Dr/a. {state.get('doctor_name')}:",
        "",
        f"👤 Paciente: {_patient_name(state)} ({_patient_age(state)})",
        f"🛠️ Servicio: {state.get('motivo')}",
        f"📅 Fecha: {_fmt_date(state.get('chosen_date'))}",
        f"⏰ Hora: {_fmt_time(state.get('chosen_start'))}",
        "",
        "Le recomendamos llegar con al menos 10 minutos de anticipación.",
    ]
    return BookingResult(state, "\n".join(lines), True)


# ── Turn orchestration ────────────────────────────────────────────────────────

async def advance_booking(state: dict, user_text: Optional[str] = None) -> BookingResult:
    """Run one deterministic booking turn (doctor → día → hora → confirmar → reservar)."""
    phase = state.get("booking_phase")

    if phase is None:
        return await _enter_doctor_phase(state)

    if phase == "doctor":
        idx = _choose(user_text, [d["name"] for d in state.get("proposed_doctors", [])])
        if idx is None:
            titles = [d["name"] for d in state.get("proposed_doctors", [])]
            return BookingResult(state, f"Por favor elija una opción válida:\n\n{_numbered(titles)}", False)
        doc = state["proposed_doctors"][idx - 1]
        state["doctor_id"], state["doctor_name"] = doc["id"], doc["name"]
        return await _enter_dia_phase(state)

    if phase == "dia":
        schedule = state.get("schedule", [])
        idx = _choose(user_text, [d.get("day_label") or d["date"] for d in schedule])
        if idx is None:
            titles = [d.get("day_label") or d["date"] for d in schedule]
            return BookingResult(state, f"Por favor elija un día de la lista:\n\n{_numbered(titles)}", False)
        return _enter_hora_phase(state, schedule[idx - 1])

    if phase == "hora":
        slots = state.get("current_slots", [])
        titles = [f"{_fmt_time(s.get('start_time'))} - {_fmt_time(s.get('end_time'))}" for s in slots]
        idx = _choose(user_text, titles)
        if idx is None:
            return BookingResult(state, f"Por favor elija un horario de la lista:\n\n{_numbered(titles)}", False)
        slot = slots[idx - 1]
        state["chosen_start"], state["chosen_end"] = slot.get("start_time"), slot.get("end_time")
        return _enter_confirmar_phase(state)

    if phase == "confirmar":
        if _is_affirmative(user_text):
            return await _do_booking(state)
        return BookingResult(
            state,
            'Si los datos son correctos, responda "SÍ" para confirmar. Si desea cambiar algo, indíquemelo por favor ✍️.',
            False,
        )

    # phase == "done" or unknown → nothing more to drive deterministically.
    return BookingResult(state, None, True)
