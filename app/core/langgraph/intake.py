"""Deterministic intake controller — guarantees steps 1→6 of the booking flow.

The LLM never controls the order of these steps (it skipped them intermittently). This
module asks each question in a fixed order, parses the answer deterministically, and does
NOT advance until the current slot is filled. Insurance is a hard gate. After step 6
(motivo) the conversation hands off to the LLM agent (steps 7→12) with all data injected.

State is a plain JSON-serializable dict persisted per wa_id in cache_service (Redis), so it
survives across WhatsApp turns. One worker per wa_id (message buffer) avoids races.
"""

import json
import re
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Optional

from app.core.cache import cache_service
from app.core.langgraph.tools.insurance import verify_insurance
from app.core.logging import logger

VerifyFn = Callable[[str, str], Awaitable[dict]]

_SEGURO_OPTIONS = ["Alianza", "Nacional Vida", "Membresía Odontoking", "No tengo seguro"]

# Keywords that signal the patient wants to book — used to start the intake.
_BOOKING_KEYWORDS = (
    "agendar", "agenda", "cita", "reservar", "reserva", "turno", "consulta",
    "sacar hora", "sacar cita", "quiero ver",
)


# Keywords for non-booking FAQ questions (location, hours, price, phone). On first contact
# these go to the LLM; anything else starts the booking intake, since the bot's purpose is
# booking and most patients open with a greeting rather than the word "cita".
_FAQ_KEYWORDS = (
    "ubicacion", "ubicación", "ubicad", "donde estan", "dónde están", "donde queda", "dónde queda",
    "direccion", "dirección", "como llego", "cómo llego", "como llegar", "cómo llegar", "mapa", "sucursal",
    "horario", "horarios", "abren", "abierto", "cierran", "a que hora atienden", "a qué hora atienden",
    "precio", "precios", "costo", "costos", "cuanto cuesta", "cuánto cuesta", "tarifa",
    "telefono", "teléfono", "numero de contacto", "número de contacto",
)


# ── Intent detection ──────────────────────────────────────────────────────────

def is_booking_intent(text: str) -> bool:
    """Heuristic: does the message express intent to book an appointment?"""
    low = (text or "").lower()
    return any(k in low for k in _BOOKING_KEYWORDS)


def is_faq_query(text: str) -> bool:
    """Heuristic: is this a non-booking FAQ (location, hours, price, phone)?"""
    low = (text or "").lower()
    return any(k in low for k in _FAQ_KEYWORDS)


# ── State helpers ─────────────────────────────────────────────────────────────

def new_state(nombre: Optional[str] = None, nombre_whatsapp: Optional[str] = None) -> dict:
    """Create a fresh intake state.

    `nombre` is the real name the patient confirms (asked during intake — left None so it is
    always requested). `nombre_whatsapp` is the WhatsApp profile name, kept only to build the
    CRM display name "<perfil> - <nombre solicitado>" once the real name is collected.
    """
    return {
        "nombre": (nombre or None),
        "nombre_whatsapp": (nombre_whatsapp or None),
        "edad": None,
        "is_for_self": None,
        "tercero_nombre": None,
        "tercero_edad": None,
        "es_antiguo": None,
        "seguro": None,
        "ci": None,
        "seguro_estado": None,  # "VIGENTE" | "PARTICULAR" | None
        "motivo": None,
        "motivo_otro_pendiente": False,  # patient chose "Otro" → awaiting the free-text detail
        "pending": None,
        "completo": False,
        # Post-booking sub-flow (see postbooking.py) — correcting the name or cancelling the cita.
        "post_phase": None,
        "pending_new_name": None,
        # Phase-2 deterministic booking (see booking.py) — populated after intake completes.
        "wa_id": None,
        "booking_phase": None,
        "specialty_id": None,
        "service_id": None,
        "service_name": None,
        "proposed_doctors": [],
        "doctor_id": None,
        "doctor_name": None,
        "schedule": [],
        "current_slots": [],
        "chosen_date": None,
        "chosen_day_label": None,
        "chosen_start": None,
        "chosen_end": None,
        "booking_confirmado": False,
    }


def next_pending(state: dict) -> Optional[str]:
    """Return the next slot that must be asked, or None when intake is complete."""
    if not state.get("nombre"):
        return "nombre"
    if state.get("edad") is None:
        return "edad"
    if state.get("is_for_self") is None:
        return "para_quien"
    if state.get("is_for_self") is False:
        if not state.get("tercero_nombre"):
            return "tercero_nombre"
        if state.get("tercero_edad") is None:
            return "tercero_edad"
    if state.get("es_antiguo") is None:
        return "antiguo"
    if not state.get("seguro"):
        return "seguro"
    if not state.get("seguro_estado"):
        # insurer chosen but not yet verified → need the CI
        return "ci"
    if not state.get("motivo"):
        # "Otro" was chosen → ask for the free-text detail instead of re-listing the options.
        if state.get("motivo_otro_pendiente"):
            return "motivo_detalle"
        return "motivo"
    return None


def question_for(slot: str) -> str:
    """Canonical question text for a slot. Numbered options render as WhatsApp buttons."""
    return {
        "nombre": "Para comenzar, ¿podría indicarme su nombre completo, por favor?",
        "edad": "¿Podría indicarme su edad, por favor?",
        "para_quien": "¿Para quién es la cita?\n1) Para mí\n2) Para otra persona",
        "tercero_nombre": "¿Cuál es el nombre completo de la persona para quien es la cita?",
        "tercero_edad": "¿Qué edad tiene esa persona?",
        "antiguo": "¿Es su primera vez en la clínica o ya vino antes?\n1) Primera vez\n2) Ya he ido antes",
        "seguro": "¿Cuenta con algún seguro dental?\n1) Alianza\n2) Nacional Vida\n3) Membresía Odontoking\n4) No tengo seguro",
        "ci": "Para validar su seguro, ¿podría compartir su número de carnet de identidad? ",
        "motivo": "¿Qué molestia o servicio necesita?\n1) Dolor dental\n2) Diente quebrado\n3) Encía inflamada\n4) Limpieza\n5) Otro",
        "motivo_detalle": "Para poder ayudarle mejor, ¿podría indicarme qué molestia o servicio necesita?",
    }[slot]


# ── Deterministic parsing ─────────────────────────────────────────────────────

# Numbered motivo options 1-4 → their canonical labels (option 5 = "Otro" handled separately).
_MOTIVO_NUM = {
    "1": "Dolor dental",
    "2": "Diente quebrado",
    "3": "Encía inflamada",
    "4": "Limpieza",
}


def _norm(text: str) -> str:
    """Lowercase, strip accents and surrounding whitespace — for option matching."""
    t = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn").strip()


def _last_line(text: Optional[str]) -> str:
    r"""Last non-empty line of a buffered turn (messages joined by "\n").

    Structured single-token answers (age, para_quien, antiguo, seguro) read the most recent line,
    so a stray earlier message in the same batch does not break parsing. For a single message this
    returns the message itself.
    """
    lines = [ln for ln in re.split(r"[\r\n]+", text or "") if ln.strip()]
    return lines[-1] if lines else (text or "")


def _collapse_ws(text: Optional[str]) -> str:
    r"""Collapse internal whitespace (including newlines) to single spaces.

    Free-text answers (name, motivo) are CONCATENATED across a buffered turn — "Juan\nPérez" is
    stored as "Juan Pérez", not with an embedded newline.
    """
    return re.sub(r"\s+", " ", (text or "")).strip()


def _parse_age(text: str) -> Optional[int]:
    m = re.search(r"\b(\d{1,3})\b", text or "")
    if not m:
        return None
    age = int(m.group(1))
    return age if 1 <= age <= 120 else None


def _parse_ci(text: str) -> Optional[str]:
    digits = re.sub(r"\D", "", text or "")
    return digits if len(digits) >= 5 else None


def _parse_choice_for_whom(text: str) -> Optional[bool]:
    low = (text or "").lower()
    if any(k in low for k in ("otra", "otro", "persona", "alguien", "hijo", "familiar")) or low.strip() == "2":
        return False
    if any(k in low for k in ("para mí", "para mi", "mí", " mi", "yo", "mismo", "para mi mismo")) or low.strip() == "1":
        return True
    return None


def _parse_antiguo(text: str) -> Optional[bool]:
    low = (text or "").lower()
    if any(k in low for k in ("primera", "nuevo", "nueva", "primera vez")) or low.strip() == "1":
        return False  # first time
    if any(k in low for k in ("ya", "antes", "he ido", "soy paciente", "sí", "si ")) or low.strip() == "2":
        return True
    return None


def _parse_seguro(text: str) -> Optional[str]:
    low = (text or "").lower().strip()
    if "alianza" in low or low == "1":
        return "Alianza"
    if "nacional" in low or low == "2":
        return "Nacional Vida"
    if "membres" in low or "odontoking" in low or low == "3":
        return "Membresía Odontoking"
    if any(k in low for k in ("no tengo", "sin seguro", "ninguno", "particular")) or low in ("no", "4"):
        return "No tengo seguro"
    return None


# Leading words that signal the answer is a question or a command, not a name. A patient who
# replies to "¿su nombre?" with "cual es mi nombre?" must NOT have that phrase stored as a name
# (this happened in production: the cita was registered for "cual es mi nombre?").
_NON_NAME_STARTS = (
    "cual", "cuales", "que", "quien", "quienes", "como", "cuando", "donde", "cuanto", "cuantos",
    "por que", "porque", "para que", "no se", "no lo se", "dime", "adivina", "sabes",
    "cambiar", "cambia", "cancelar", "cancela", "quiero", "necesito", "puedes", "puede",
    "olvida", "ayuda", "ayudame", "hola",
)


def _looks_like_name(text: str) -> bool:
    """Heuristic: does `text` plausibly look like a person's name (not a question/command)?"""
    if "?" in text or "¿" in text:
        return False
    norm = _norm(text)
    if not norm:
        return False
    if any(norm == w or norm.startswith(w + " ") for w in _NON_NAME_STARTS):
        return False
    return sum(c.isalpha() for c in text) >= 2


_INVALID = {
    "nombre": "Por favor indíqueme su nombre completo (nombre y apellido). 🙏",
    "edad": "¿Podría indicarme su edad en números, por favor?",
    "para_quien": "Por favor responda: 1) Para mí  o  2) Para otra persona.",
    "antiguo": "Por favor responda: 1) Primera vez  o  2) Ya he ido antes.",
    "seguro": "Por favor elija: 1) Alianza  2) Nacional Vida  3) Membresía Odontoking  4) No tengo seguro.",
    "ci": "El carnet debe ser numérico. ¿Podría reenviarlo, por favor? ",
}


def validate_name(text: str) -> Optional[str]:
    """Return an error message if `text` is not a plausible full name, else None.

    Shared by the intake (nombre / tercero_nombre) and the post-booking rename flow so a
    question or command is never accepted as a name.
    """
    t = (text or "").strip()
    if len(t) < 3 or t.isdigit() or not _looks_like_name(t):
        return _INVALID["nombre"]
    return None


def apply_answer(state: dict, slot: str, text: str) -> Optional[str]:
    """Fill `slot` from the user's text. Returns an error message to re-ask, or None on success."""
    text = (text or "").strip()
    # Structured single-token answers read the most recent line of a buffered turn; free-text
    # answers (name, motivo) concatenate the whole turn into one space-separated string.
    line = _last_line(text)
    if slot in ("nombre", "tercero_nombre"):
        error = validate_name(text)
        if error:
            return error
        state[slot] = _collapse_ws(text)
        return None
    if slot in ("edad", "tercero_edad"):
        age = _parse_age(line)
        if age is None:
            return _INVALID["edad"]
        state[slot] = age
        return None
    if slot == "para_quien":
        choice = _parse_choice_for_whom(line)
        if choice is None:
            return _INVALID["para_quien"]
        state["is_for_self"] = choice
        return None
    if slot == "antiguo":
        choice = _parse_antiguo(line)
        if choice is None:
            return _INVALID["antiguo"]
        state["es_antiguo"] = choice
        return None
    if slot == "seguro":
        seguro = _parse_seguro(line)
        if seguro is None:
            return _INVALID["seguro"]
        state["seguro"] = seguro
        if seguro == "No tengo seguro":
            state["seguro_estado"] = "PARTICULAR"
        return None
    if slot == "motivo":
        if not _norm(text):
            return "¿Podría indicarme la molestia o servicio que necesita?"
        # An option is a single token, so match it on the most recent line ("hola\n1" → 1).
        norm_line = _norm(line)
        # "Otro" / option 5 → defer to the free-text detail step (do NOT re-list the options).
        if norm_line in ("5", "otro"):
            state["motivo_otro_pendiente"] = True
            return None
        # A numbered option 1-4 → its canonical label.
        if norm_line in _MOTIVO_NUM:
            state["motivo"] = _MOTIVO_NUM[norm_line]
            return None
        # Anything else is a free-text description of the molestia — accept it (concatenated);
        # booking will classify it into a specialty/service.
        state["motivo"] = _collapse_ws(text)
        return None
    if slot == "motivo_detalle":
        detalle = _collapse_ws(text)
        if len(detalle) < 2 or detalle.isdigit():
            return "Por favor describa brevemente qué molestia o servicio necesita 🦷."
        state["motivo"] = detalle
        state["motivo_otro_pendiente"] = False
        return None
    return None  # unknown slot — should not happen


# ── Insurance verification ────────────────────────────────────────────────────

async def _default_verify(ci: str, seguro: str) -> dict:
    raw = await verify_insurance.ainvoke({"ci_paciente": ci, "seguro_paciente": seguro})
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"has_insurance": False}


_INSURANCE_BLOCKED_MSG = (
    "Al verificar su seguro encontramos un inconveniente con su cobertura ⚠️. "
    "Para poder atenderle, le recomendamos comunicarse con su aseguradora y regularizarlo. "
    "Cuando esté en orden, vuelva a enviarnos su número de carnet 🤝."
)


# ── Turn orchestration ────────────────────────────────────────────────────────

class IntakeResult:
    """Outcome of one intake turn."""

    def __init__(self, state: dict, reply: Optional[str], completed: bool) -> None:
        """Store the updated state, the reply to send (or None), and the completed flag."""
        self.state = state
        self.reply = reply
        self.completed = completed


async def advance_intake(
    state: dict,
    user_text: Optional[str],
    *,
    verify_fn: VerifyFn = _default_verify,
) -> IntakeResult:
    """Run one intake turn: parse the pending answer, then ask the next question.

    - user_text is the latest patient message (None when intake is just starting).
    - Returns IntakeResult(state, reply, completed). When completed, reply is None and the
      caller hands off to the LLM (steps 7→12).
    """
    pending = state.get("pending")

    if user_text is not None and pending:
        if pending == "ci":
            ci = _parse_ci(user_text)
            if ci is None:
                state["pending"] = "ci"
                return IntakeResult(state, _INVALID["ci"], False)
            state["ci"] = ci
            try:
                result = await verify_fn(ci, state["seguro"])
            except Exception as e:
                logger.exception("intake_verify_insurance_failed", error=str(e))
                result = {"has_insurance": False, "retry": True}
            if result.get("has_insurance") and result.get("status") == "VIGENTE":
                state["seguro_estado"] = "VIGENTE"
            else:
                state["ci"] = None  # allow resending the CI after regularizing
                state["pending"] = "ci"
                return IntakeResult(state, _INSURANCE_BLOCKED_MSG, False)
        else:
            error = apply_answer(state, pending, user_text)
            if error:
                state["pending"] = pending
                return IntakeResult(state, error, False)

    nxt = next_pending(state)
    if nxt is None:
        state["completo"] = True
        state["pending"] = None
        return IntakeResult(state, None, True)

    state["pending"] = nxt
    # When booking for a third person, validate THAT person's CI (the patient), not the
    # writer's — so the carnet question must name the third person.
    if nxt == "ci" and state.get("is_for_self") is False and state.get("tercero_nombre"):
        tercero = state["tercero_nombre"]
        return IntakeResult(
            state,
            f"Para validar el seguro de {tercero}, ¿podría compartir el número de carnet de identidad de {tercero}? ",
            False,
        )
    return IntakeResult(state, question_for(nxt), False)


def crm_display_name(nombre_whatsapp: Optional[str], nombre_solicitado: Optional[str]) -> Optional[str]:
    """Build the CRM person name as "<perfil WhatsApp> - <nombre solicitado>".

    Falls back to whichever part is available. "Paciente WhatsApp" is the placeholder used
    before a profile name is known, so it is treated as no profile name.
    """
    wa = (nombre_whatsapp or "").strip()
    real = (nombre_solicitado or "").strip()
    if wa == "Paciente WhatsApp":
        wa = ""
    if wa and real:
        return f"{wa} - {real}"
    return real or wa or None


def handoff_context(state: dict) -> str:
    """Build the context block injected into the LLM (Phase 2) after intake completes."""
    paciente = state.get("tercero_nombre") if state.get("is_for_self") is False else state.get("nombre")
    edad = state.get("tercero_edad") if state.get("is_for_self") is False else state.get("edad")
    lines = [
        "# INTAKE COMPLETO — datos ya recolectados (NO los vuelvas a preguntar)",
        f"nombre_paciente: {paciente}",
        f"edad_paciente: {edad}",
        f"is_for_self: {state.get('is_for_self')}",
        f"paciente_antiguo: {state.get('es_antiguo')}",
        f"seguro: {state.get('seguro')}",
        f"estado_seguro: {state.get('seguro_estado')}",
        f"ci_paciente: {state.get('ci')}",
        f"motivo: {state.get('motivo')}",
        "→ Continúa DESDE EL PASO 7 (proponer doctores según el motivo). No repitas los pasos 1-6.",
    ]
    return "\n".join(lines)


# ── Persistence (cache_service / Redis) ───────────────────────────────────────

class IntakeStore:
    """Per-wa_id intake state in cache_service (Redis when configured, else in-memory)."""

    _PREFIX = "intake:"
    _TTL = 3600

    def _key(self, wa_id: str) -> str:
        return f"{self._PREFIX}{wa_id}"

    async def get(self, wa_id: str) -> Optional[dict]:
        """Return the stored intake state for a wa_id, or None."""
        raw = await cache_service.get(self._key(wa_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set(self, wa_id: str, state: dict) -> None:
        """Persist the intake state for a wa_id with a TTL."""
        await cache_service.set(self._key(wa_id), json.dumps(state, ensure_ascii=False), ttl=self._TTL)

    async def clear(self, wa_id: str) -> None:
        """Delete the intake state for a wa_id."""
        await cache_service.delete(self._key(wa_id))


intake_store = IntakeStore()
