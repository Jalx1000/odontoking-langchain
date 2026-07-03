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
from app.core.langgraph.tools.crm import cancel_appointment, update_crm
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


def _lines(text: Optional[str]) -> list[str]:
    r"""Split a buffered turn into its non-empty lines.

    The message buffer joins rapid-fire WhatsApp messages with "\n", so a single turn may carry
    several messages. Structured steps parse these line by line (see _choose / _is_affirmative).
    """
    return [ln for ln in re.split(r"[\r\n]+", text or "") if ln.strip()]


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


def _slot_start_hour(title: str) -> Optional[int]:
    """Start hour of a slot title ('08:00 - 09:00' → 8), or None if the title is not a time."""
    m = re.match(r"\s*(\d{1,2}):\d{2}", _norm(title))
    return int(m.group(1)) if m else None


def _choose_one(user_text: Optional[str], titles: list[str]) -> Optional[int]:
    """Map a single message to a 1-based option index (see _choose for the multi-line wrapper).

    Time always wins over position: a leading/lone hour is read as a TIME, never as the option
    number. This fixes the bug where "08:00 - 09:00" was parsed as option 8 (→ 17:00) and the
    case where "5" meant "5pm" but selected the 5th option.
    """
    text = _norm(user_text)
    if not text or not titles:
        return None

    # A bare number: TIME WINS. Treat it as an hour and match an offered slot first; only fall
    # back to the list position when no offered slot has that hour. ("5" → 17:00 when 17:00 is
    # offered; "8" → 08:00; "3" → 15:00.) Patients say "a las 5" far more often than "opción 5",
    # and morning hours 1-6 are never offered (clinic opens 07:30), so the pm reading is safe.
    if re.fullmatch(r"\d{1,2}\.?", text):
        n = int(text.rstrip("."))
        candidate_hours = {n} | ({n + 12} if 1 <= n <= 11 else set())
        hour_matches = [i for i, t in enumerate(titles, start=1) if _slot_start_hour(t) in candidate_hours]
        if len(hour_matches) == 1:
            return hour_matches[0]
        if hour_matches:
            return None  # the number maps to >1 offered hour → ambiguous, let the caller re-offer
        return n if 1 <= n <= len(titles) else None

    # An explicit time ("17:00", "08:00 - 09:00", "8:00") → the slot whose start is that time.
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if m:
        hhmm = f"{int(m.group(1)):02d}:{m.group(2)}"
        for i, title in enumerate(titles, start=1):
            if _norm(title).startswith(hhmm):
                return i
        return None  # a typed time that matches no offered slot → re-offer rather than guess

    # Otherwise match by title text (doctor names, day labels, or a full slot title).
    for i, title in enumerate(titles, start=1):
        nt = _norm(title)
        if nt and (nt == text or nt.startswith(text) or text.startswith(nt)):
            return i
    return None


def _choose(user_text: Optional[str], titles: list[str]) -> Optional[int]:
    """Map the patient's reply to a 1-based option index, tolerating a buffered multi-message turn.

    When several messages were concatenated (e.g. the patient tapped "1" then "2", or greeted then
    chose), the most RECENT line that maps to a valid option wins. For a single message this is
    identical to _choose_one.
    """
    if not titles:
        return None
    lines = _lines(user_text)
    if len(lines) <= 1:
        return _choose_one(user_text, titles)
    for ln in reversed(lines):
        idx = _choose_one(ln, titles)
        if idx is not None:
            return idx
    return None


_AFFIRMATIVE = {"si", "sí", "s", "yes", "y", "confirmo", "confirmar", "correcto", "ok", "okay", "dale"}


def _is_affirmative(user_text: Optional[str]) -> bool:
    r"""True when the patient confirmed.

    On a buffered multi-message turn the LAST line decides, so a double-tap ("Sí\nSí") confirms
    and a later correction is not overridden by an earlier "Sí".
    """
    lines = _lines(user_text)
    candidate = lines[-1] if lines else user_text
    return _norm(candidate) in _AFFIRMATIVE


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


async def _revalidate_slot(state: dict) -> Optional[BookingResult]:
    """Re-check, at confirmation time, that the chosen slot is still free.

    The slots were fetched when the day was picked; by the time the patient confirms, that slot
    may already be taken (or have passed, for today). Returns a re-offer BookingResult when the
    slot is gone, or None when it is still available and the caller may proceed to book.
    """
    doctor_id = state.get("doctor_id")
    chosen_date = state.get("chosen_date")
    chosen_start = state.get("chosen_start")
    if not (doctor_id and chosen_date and chosen_start):
        return None  # nothing to validate against → let the CRM be the final gate

    schedule = [
        d for d in await _fetch_schedule(doctor_id)
        if d.get("date") and _future_slots(d["date"], d.get("slots") or [])
    ]
    day = next((d for d in schedule if d.get("date") == chosen_date), None)
    if day and any((s.get("start_time") or "") == chosen_start for s in _future_slots(chosen_date, day.get("slots") or [])):
        return None  # still free → proceed to book

    # Slot is gone — drop the stale pick so no obsolete hour can be carried into a later booking.
    state.pop("chosen_start", None)
    state.pop("chosen_end", None)

    if day:  # the day still has OTHER slots → re-offer that same day
        reoffer = _enter_hora_phase(state, day)
        reoffer.reply = f"Disculpe, el horario de las {_fmt_time(chosen_start)} ya fue tomado. {reoffer.reply}"
        return reoffer

    # The whole day is gone → send the patient back to pick another day (or doctor).
    state["schedule"] = schedule
    if not schedule:
        state["booking_phase"] = "doctor"
        titles = [d["name"] for d in state.get("proposed_doctors", [])]
        return BookingResult(
            state,
            f"Disculpe, el/la Dr/a. {state.get('doctor_name')} ya no tiene horarios disponibles. "
            f"¿Con quién más le gustaría agendar? 😊\n\n{_numbered(titles)}",
            False,
        )
    state["booking_phase"] = "dia"
    titles = [d.get("day_label") or d["date"] for d in schedule]
    return BookingResult(
        state,
        f"Disculpe, el horario de las {_fmt_time(chosen_start)} del {_fmt_date(chosen_date)} ya no está disponible. "
        f"¿Para qué otro día le gustaría agendar? 📅\n\n{_numbered(titles)}",
        False,
    )


async def start_reschedule(state: dict) -> BookingResult:
    """Begin rescheduling a confirmed cita: keep the same doctor, re-pick día → hora → confirmar.

    Reuses the whole booking flow (so the slot fixes and confirm-time re-validation apply) and
    flags the state so _do_booking cancels the previous cita before creating the new one — no
    duplicate appointment is left behind.
    """
    state["rescheduling"] = True
    state["booking_confirmado"] = False
    return await _enter_dia_phase(state)


async def _do_booking(state: dict) -> BookingResult:
    reoffer = await _revalidate_slot(state)
    if reoffer is not None:
        return reoffer

    # Rescheduling: remove the previous cita BEFORE creating the new one. cancel_appointment
    # deletes the latest meeting, which at this point is still the old one (the new activity does
    # not exist yet), so this never deletes the appointment we are about to create.
    if state.get("rescheduling"):
        cancel = await cancel_appointment(state.get("wa_id") or "")
        if not cancel.get("success"):
            logger.warning("reschedule_cancel_previous_failed", wa_id=state.get("wa_id"), result=cancel)

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
    state["rescheduling"] = False
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
