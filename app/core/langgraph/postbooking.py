"""Deterministic post-booking controller — correct the patient's name or cancel the cita.

After a cita is confirmed (booking.py), the LLM used to flatly refuse "quiero cambiar mi nombre"
or "quiero cancelar mi cita" because no such capability existed. This module owns those two
intents deterministically, with an explicit confirmation gate, calling the CRM helpers
(rename_person / cancel_appointment). It operates on the same per-wa_id state dict as intake and
booking, using the `post_phase` field.
"""

import unicodedata
from typing import Optional

from app.core.langgraph.intake import crm_display_name, validate_name
from app.core.langgraph.tools.crm import cancel_appointment, rename_person
from app.core.logging import logger


def _norm(s: Optional[str]) -> str:
    """Lowercase, strip accents/whitespace — for accent-insensitive matching."""
    text = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn").strip()


# Intent keywords (accent-insensitive). Checked AFTER a booking is confirmed.
_CANCEL_KEYWORDS = (
    "cancelar", "cancela", "anular", "anula", "ya no quiero la cita", "ya no quiero mi cita",
    "eliminar mi cita", "eliminar la cita", "borrar mi cita", "no quiero la cita",
)
_RENAME_KEYWORDS = (
    "cambiar mi nombre", "cambiar el nombre", "corregir mi nombre", "corregir el nombre",
    "actualizar mi nombre", "modificar mi nombre", "arreglar mi nombre", "mi nombre esta mal",
    "ese no es mi nombre", "no es mi nombre", "mi nombre no es",
)

_AFFIRMATIVE = {"si", "sí", "s", "yes", "y", "confirmo", "confirmar", "correcto", "ok", "okay", "dale"}


def is_cancel_intent(text: Optional[str]) -> bool:
    """True when the message asks to cancel the appointment."""
    low = _norm(text)
    return any(k in low for k in _CANCEL_KEYWORDS)


def is_name_change_intent(text: Optional[str]) -> bool:
    """True when the message asks to change/correct the patient's name."""
    low = _norm(text)
    return any(k in low for k in _RENAME_KEYWORDS)


def _is_affirmative(text: Optional[str]) -> bool:
    return _norm(text) in _AFFIRMATIVE


def _fmt_date(date_iso: Optional[str]) -> str:
    """'2026-06-18' → '18/06/2026'."""
    if not date_iso:
        return ""
    try:
        y, m, d = date_iso.split("-")
        return f"{d}/{m}/{y}"
    except (ValueError, AttributeError):
        return date_iso


def _fmt_time(t: Optional[str]) -> str:
    """'17:00:00' → '17:00'."""
    return (t or "")[:5]


class PostbookingResult:
    """Outcome of one post-booking turn: updated state, reply to send, and a done flag.

    When `clear` is True the caller should delete the stored state (a fresh conversation), so a
    cancelled patient can start a brand-new booking from scratch.
    """

    def __init__(self, state: dict, reply: Optional[str], done: bool, *, clear: bool = False) -> None:
        """Store the state, the WhatsApp reply, the done flag and the clear-state flag."""
        self.state = state
        self.reply = reply
        self.done = done
        self.clear = clear


# ── Phase transitions ─────────────────────────────────────────────────────────

def _enter_cancel_confirm(state: dict) -> PostbookingResult:
    state["post_phase"] = "cancel_confirm"
    fecha = _fmt_date(state.get("chosen_date"))
    hora = _fmt_time(state.get("chosen_start"))
    doctor = state.get("doctor_name")
    if state.get("booking_confirmado") and fecha and hora:
        msg = (
            f"¿Está seguro de que desea cancelar su cita con el/la Dr/a. {doctor} "
            f'el {fecha} a las {hora}? Responda "SÍ" para confirmar la cancelación.'
        )
    else:
        msg = '¿Desea cancelar su cita? Responda "SÍ" para confirmar la cancelación.'
    return PostbookingResult(state, msg, False)


def _enter_rename_ask(state: dict) -> PostbookingResult:
    state["post_phase"] = "rename_ask"
    return PostbookingResult(state, "Claro 🙂, ¿cuál es su nombre completo correcto?", False)


def _ask_rename_confirm(state: dict, new_name: str) -> PostbookingResult:
    state["pending_new_name"] = new_name
    state["post_phase"] = "rename_confirm"
    return PostbookingResult(
        state, f'¿Confirma que su nombre completo es "{new_name}"? Responda "SÍ" para guardarlo.', False
    )


# ── Turn orchestration ────────────────────────────────────────────────────────

async def advance_postbooking(state: dict, user_text: Optional[str]) -> PostbookingResult:
    """Run one deterministic post-booking turn (cancel or rename), with a confirmation gate."""
    phase = state.get("post_phase")

    if phase is None:
        if is_cancel_intent(user_text):
            return _enter_cancel_confirm(state)
        if is_name_change_intent(user_text):
            return _enter_rename_ask(state)
        # Caller only routes here on an intent or an active phase; nothing to drive otherwise.
        return PostbookingResult(state, None, True)

    if phase == "cancel_confirm":
        if _is_affirmative(user_text):
            result = await cancel_appointment(state.get("wa_id") or "")
            if result.get("success"):
                logger.info("postbooking_cita_cancelled", wa_id=state.get("wa_id"))
                state["post_phase"] = None
                return PostbookingResult(
                    state,
                    "Su cita ha sido cancelada ✅. Cuando guste agendar una nueva, escríbame y con gusto le ayudo 🦷.",
                    True,
                    clear=True,
                )
            return PostbookingResult(
                state,
                "Disculpe, tuve un inconveniente al cancelar su cita. ¿Podría intentarlo nuevamente en un momento? 🙏",
                False,
            )
        # Anything that is not an explicit "sí" keeps the cita untouched.
        state["post_phase"] = None
        return PostbookingResult(
            state, "Perfecto, su cita se mantiene sin cambios. ¿Hay algo más en que pueda ayudarle? 😊", True
        )

    if phase == "rename_ask":
        error = validate_name(user_text or "")
        if error:
            return PostbookingResult(state, error, False)
        return _ask_rename_confirm(state, (user_text or "").strip())

    if phase == "rename_confirm":
        if _is_affirmative(user_text):
            new_real = state.get("pending_new_name") or ""
            full = crm_display_name(state.get("nombre_whatsapp"), new_real) or new_real
            result = await rename_person(state.get("wa_id") or "", full)
            if result.get("success"):
                logger.info("postbooking_name_updated", wa_id=state.get("wa_id"))
                state["nombre"] = new_real
                state["pending_new_name"] = None
                state["post_phase"] = None
                return PostbookingResult(
                    state, f"Listo ✅, actualicé su nombre a {new_real}. ¿Hay algo más en que pueda ayudarle? 😊", True
                )
            return PostbookingResult(
                state,
                "Disculpe, no pude actualizar su nombre en este momento. ¿Podría intentarlo nuevamente? 🙏",
                False,
            )
        # Not a "sí" → treat the reply as a corrected name (or re-ask when it is not a valid name).
        if validate_name(user_text or ""):
            return PostbookingResult(
                state,
                'Para confirmar responda "SÍ", o si desea corregirlo indíqueme su nombre completo 🙂.',
                False,
            )
        return _ask_rename_confirm(state, (user_text or "").strip())

    # Unknown phase → nothing to drive.
    return PostbookingResult(state, None, True)
