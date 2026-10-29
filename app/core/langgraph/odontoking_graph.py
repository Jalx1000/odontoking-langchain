"""OdontokingAgent — LangGraph agent for WhatsApp dental clinic assistant."""

import asyncio
import json
import os as _os
import re
import unicodedata
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import (
    Any,
    Optional,
    cast,
)
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    convert_to_openai_messages,
)
from langchain_core.runnables.config import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import GraphBubbleUp, GraphInterrupt, GraphRecursionError
from langgraph.graph import (
    END,
    StateGraph,
)
from langgraph.graph.state import (
    Command,
    CompiledStateGraph,
)
from langgraph.types import RetryPolicy
from psycopg import (
    AsyncConnection,
    sql,
)
from psycopg.rows import (
    DictRow,
    dict_row,
)
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

from app.core.cache import cache_service
from app.core.config import settings
from app.services.database import database_service
from app.core.langgraph.tools.crm import (
    cancel_appointment,
    cancel_appointment_tool,
    create_appointment,
    get_citas,
    preload_patient_citas,
    reschedule_appointment_tool,
    save_insurance,
    save_patient,
)
from app.core.langgraph.tools.insurance import verify_insurance
from app.core.langgraph.tools.odontoking import (
    get_doctor_schedule,
    get_doctors,
    get_services,
    get_specialties,
    get_specialty_doctors,
)
from app.core.langgraph.tools.whatsapp_flows import enviar_formulario, responder_recordatorio
from app.core.langgraph.booking import advance_booking, start_reschedule
from app.core.langgraph.intake import (
    advance_intake,
    crm_display_name,
    intake_store,
    is_booking_intent,
    is_faq_query,
    new_state,
)
from app.core.langgraph.postbooking import (
    advance_postbooking,
    is_cancel_intent,
    is_name_change_intent,
    is_reschedule_intent,
)
from app.core.logging import logger
from app.core.observability import langfuse_callback_handler
from app.schemas import GraphState
from app.utils import (
    dump_messages,
    process_llm_response,
)

PostgresConnPool = AsyncConnectionPool[AsyncConnection[DictRow]]

# Classic chat-based deployment: the LLM drives the whole booking flow and writes to the CRM.
_CLASSIC_TOOLS = [
    get_services,
    get_specialties,
    get_doctors,
    get_specialty_doctors,
    get_doctor_schedule,
    verify_insurance,
    save_patient,
    save_insurance,
    create_appointment,
    cancel_appointment_tool,
    reschedule_appointment_tool,
    get_citas,
]

# WhatsApp native-forms mode: the LLM only detects intent and sends a form; the CRM books, cancels
# and reschedules. The booking write tools (create/cancel/reschedule/save) are intentionally NOT
# bound so the agent cannot touch citas "por otra vía" — only read tools remain (FAQ + answering
# cita questions) plus enviar_formulario. verify_insurance IS bound for REACTIVE coverage checks:
# it only reads/validates a policy (never writes a cita), per the prompt's "SEGURO Y MEMBRESÍA" rule.
# get_doctors is bound so a named doctor ("con el Dr. Eloy") can be resolved to its CRM id for the
# `doctor` prefill (read-only lookup; the booking itself still happens in the form).
_FORMS_TOOLS = [
    enviar_formulario,
    responder_recordatorio,  # registra la respuesta del paciente a un recordatorio de cita (C1/C2)
    get_citas,
    get_services,
    get_specialties,
    get_doctors,
    verify_insurance,
]

_ODONTOKING_TOOLS = _FORMS_TOOLS if settings.WHATSAPP_FLOWS_ENABLED else _CLASSIC_TOOLS

# Forms mode uses odontoking.md (send-a-form flows); the classic chat-based flows live in
# odontoking-classic.md and remain the default so WHATSAPP_FLOWS_ENABLED=false is byte-for-byte
# the previous behaviour.
_PROMPT_NAME = "odontoking.md" if settings.WHATSAPP_FLOWS_ENABLED else "odontoking-classic.md"
_PROMPT_FILE = _os.path.join(_os.path.dirname(__file__), "..", "prompts", _PROMPT_NAME)
_TZ_BOLIVIA = ZoneInfo("America/La_Paz")
_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

with open(_PROMPT_FILE, "r") as _f:
    _PROMPT_TEMPLATE = _f.read()


def _format_citas_context(citas: list[dict] | None, cita_activa: int | None) -> list[str]:
    """Render the patient's cita history as context lines (CONTRATO A: preloaded, don't re-ask)."""
    if not citas:
        return ["citas_previas: []  # el paciente no tiene citas registradas en el CRM"]
    lines = ["citas_previas:  # historial completo — cada lead-cita es UNA cita (no vuelvas a preguntar)"]
    for c in citas:
        fecha = f"{c.get('fecha') or 's/fecha'} {c.get('hora') or ''}".strip()
        lines.append(
            f"  - lead_id={c.get('lead_id')} | {fecha} | {c.get('servicio') or 's/servicio'} | "
            f"Dr. {c.get('doctor') or 's/doctor'} | estado={c.get('estado')}"
        )
    if cita_activa:
        lines.append(
            f"cita_activa_lead_id: {cita_activa}  # cita vigente; para cancelar/reprogramar usa este lead_id"
        )
    lines.append(
        "# Para cancelar/reprogramar una cita concreta pasa su lead_id a la tool. "
        "Con 2+ citas vigentes, pregunta cuál antes de actuar."
    )
    return lines


def _format_pacientes_a_cargo(pacientes: list[dict] | None) -> list[str]:
    """Render who this contact schedules for (hijos, padres, …) as context lines.

    Forms mode (patient_context.pacientes_a_cargo): lets the agent answer about their citas
    without sending a form.
    """
    if not pacientes:
        return []
    lines = ["pacientes_a_cargo:  # personas que este contacto agenda (hijos, padres, pareja, …)"]
    for p in pacientes:
        if not isinstance(p, dict):
            continue
        nombre = p.get("nombre") or p.get("name") or p.get("patient_name") or "s/nombre"
        relacion = p.get("relacion") or p.get("relation") or ""
        extra = f" ({relacion})" if relacion else ""
        lines.append(f"  - {nombre}{extra}")
    return lines


def _format_recordatorios(recordatorios: list[dict] | None) -> list[str]:
    """Render appointment reminders (C1/C2) so the agent can handle a reply to one.

    A reminder with `respuesta` null is unanswered: a "Sí"/"No" from the patient answers THAT cita
    → use responder_recordatorio with its activity_id.
    """
    if not recordatorios:
        return []
    lines = ["recordatorios:  # recordatorios de cita enviados (respuesta null = sin contestar)"]
    for r in recordatorios:
        if not isinstance(r, dict):
            continue
        aid = r.get("activity_id")
        lid = r.get("lead_id")
        fecha = r.get("fecha") or ""
        hora = r.get("hora") or ""
        doctor = r.get("doctor") or ""
        resp = r.get("respuesta") or "sin responder"
        lines.append(f"  - activity_id={aid} lead_id={lid} {fecha} {hora} {doctor} -> respuesta: {resp}")
    return lines


# Clear yes/no openers for a reminder reply. We match the START of the (normalized) message so
# "No, no voy a poder ir" or "Sí, ahí estaré" are caught, while still staying conservative: a message
# that doesn't clearly open with yes/no (or mixes both) falls through to the LLM.
_REMINDER_NO_STARTS = ("no", "nop", "ya no")
_REMINDER_YES_STARTS = ("si", "sí", "sip", "confirm", "ok", "oka", "dale", "claro", "asi es",
                        "así es", "ahi estare", "ahí estaré", "ahi voy", "voy a ir", "por supuesto",
                        "de acuerdo")


def _detect_reminder_reply(text: str, recordatorios: list[dict] | None) -> Optional[tuple[str, Optional[int]]]:
    """Detect a deterministic yes/no reply to a single pending reminder.

    If there is exactly ONE unanswered reminder and `text` clearly opens with a yes/no, return
    (respuesta, activity_id). activity_id may be None: the CRM applies the response to the single
    pending reminder when no id is given, so we don't hard-require it. Returns None when the text is
    ambiguous or there are 0 / 2+ pending reminders (so the LLM handles it).
    """
    if not recordatorios:
        return None
    pending = [r for r in recordatorios if isinstance(r, dict) and r.get("respuesta") in (None, "", "null")]
    if len(pending) != 1:
        return None
    raw_id = pending[0].get("activity_id")
    activity_id = int(raw_id) if raw_id else None  # 0/None → let the CRM resolve the single reminder
    # Normalize: lowercase, strip surrounding punctuation/space; look at the first word(s).
    norm = (text or "").strip().lower().strip("!¡.?¿, ")
    if not norm:
        return None
    first = norm.split()[0].strip(".,!?")
    # Negation wins ties (e.g. "no, gracias"): check NO first.
    if first in _REMINDER_NO_STARTS or norm.startswith(("no ", "no,", "ya no")):
        return "no_asiste", activity_id
    if first in _REMINDER_YES_STARTS or norm.startswith(("si ", "si,", "sí ", "sí,", "confirm")):
        return "confirma", activity_id
    return None


# ── Direct cancel short-circuit (una sola cita activa) ─────────────────────────
# When the patient has exactly ONE active cita and clearly wants to cancel it (not reschedule),
# we confirm with the cita's detail and, on an explicit "Sí", cancel it directly via the CRM —
# skipping the WhatsApp form. Deterministic (not left to the LLM) and scoped to the forms-mode
# live path. State is a tiny per-wa_id flag in cache_service so it survives the two WhatsApp turns.

_CANCEL_CONFIRM_PREFIX = "cancel_confirm:"
_CANCEL_CONFIRM_TTL = 600  # 10 min — the pending confirmation expires if the patient wanders off

# Wants to cancel (eliminate), NOT reschedule. Mirrors postbooking._CANCEL_KEYWORDS but kept here
# so the forms path doesn't depend on that module.
_DIRECT_CANCEL_KEYWORDS = (
    "cancelar", "cancela", "cancele", "cancélela", "cancelarla", "anular", "anula", "anúlela",
    "ya no quiero la cita", "ya no quiero mi cita", "eliminar mi cita", "eliminar la cita",
    "borrar mi cita", "no quiero la cita", "ya no puedo ir", "ya no podré ir",
)
# A reschedule intent must win over a bare "cancelar" ("cancélela y muévala al viernes"): those go
# to the LLM/form, never to the direct-cancel path.
_RESCHEDULE_HINTS = (
    "reprogram", "reagend", "mover", "muev", "pasar", "pásela", "pasela", "otro dia", "otro día",
    "otra hora", "otra fecha", "cambiar la fecha", "cambiar el dia", "cambiar el día", "cambiar la hora",
    "para mañana", "para el", "al viernes", "al lunes", "al martes", "al miércoles", "al miercoles",
    "al jueves", "al sábado", "al sabado", "al domingo",
)
_CANCEL_YES = {"si", "sí", "sip", "claro", "confirmo", "confirmar", "dale", "ok", "oka", "correcto",
               "por supuesto", "de acuerdo", "asi es", "así es", "cancelala", "cancélala", "cancelar"}
_CANCEL_NO = {"no", "nop", "no gracias", "mejor no", "dejala", "déjala", "dejalo", "déjalo",
              "no cancelar", "no, gracias", "cancelar no"}


def _norm_cancel(text: str) -> str:
    """Lowercase + strip accents/outer punctuation for cancel-intent matching."""
    t = unicodedata.normalize("NFD", (text or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return t.strip().strip("!¡.?¿, ")


def _wants_direct_cancel(text: str) -> bool:
    """True when the message asks to cancel (eliminate) a cita and is NOT a reschedule."""
    low = _norm_cancel(text)
    if not low:
        return False
    if any(h in low for h in (_norm_cancel(h) for h in _RESCHEDULE_HINTS)):
        return False
    return any(k in low for k in (_norm_cancel(k) for k in _DIRECT_CANCEL_KEYWORDS))


def _is_cancel_yes(text: str) -> bool:
    norm = _norm_cancel(text)
    if norm in _CANCEL_YES:
        return True
    return norm.startswith(("si ", "si,", "sí ", "sí,", "confirm"))


def _is_cancel_no(text: str) -> bool:
    norm = _norm_cancel(text)
    if norm in _CANCEL_NO:
        return True
    return norm.startswith(("no ", "no,"))


def _single_active_cita(citas: list[dict] | None, cita_activa: int | None) -> Optional[dict]:
    """Return the single active (Agendado) cita dict when there is EXACTLY one, else None."""
    if not citas:
        return None
    activas = [c for c in citas if isinstance(c, dict) and c.get("estado") == "Agendado"]
    if len(activas) != 1:
        return None
    return activas[0]


def _fmt_cita_detail(cita: dict) -> str:
    """Human one-liner for a cita: 'el 18/06/2026 a las 14:00 con el/la Dr/a. X'."""
    fecha = cita.get("fecha") or ""
    hora = (cita.get("hora") or "")[:5]
    doctor = cita.get("doctor") or ""
    partes = []
    if fecha:
        # fecha comes as 'YYYY-MM-DD' from the CRM summary → render DD/MM/YYYY.
        try:
            y, m, d = fecha.split("-")
            fecha = f"{d}/{m}/{y}"
        except (ValueError, AttributeError):
            pass
        partes.append(f"el {fecha}")
    if hora:
        partes.append(f"a las {hora}")
    if doctor:
        partes.append(f"con el/la Dr/a. {doctor}")
    return " ".join(partes) if partes else "su cita"


async def _resolve_doctor_in_prefill(args: dict) -> dict:
    """Resolve a named `doctor` prefill to its CRM id (and carry the doctor's specialty).

    The CRM paints the booking-form `doctor` prefill by id (R7), but a weak model often passes the
    doctor NAME ("Eloy") instead of the id, so the form opens without it pre-selected. Here we
    deterministically map whatever the model passed to the doctor's id via get_doctors — removing
    the model as the weak link. When the doctor resolves and no specialty was provided, we also
    carry the doctor's specialty so that field comes pre-filled too (the patient who only named a
    doctor still gets the especialidad set). Best-effort: on any miss/ambiguity the value is kept.
    """
    doctor = args.get("doctor")
    if doctor is None or (isinstance(doctor, str) and not doctor.strip()):
        return args
    try:
        raw = await get_doctors.ainvoke({})
        data = json.loads(raw)
        doctors = data.get("data", []) if isinstance(data, dict) else []
    except Exception as e:
        logger.warning("prefill_doctor_resolve_failed", error=str(e))
        return args
    if not isinstance(doctors, list) or not doctors:
        return args

    token = _norm_cancel(str(doctor))
    match = None
    if str(doctor).strip().isdigit():
        match = next((d for d in doctors if str(d.get("id")) == str(doctor).strip()), None)
    if match is None and token:
        hits = [
            d for d in doctors
            if (nm := _norm_cancel(d.get("name"))) and (token in nm or nm in token)
        ]
        if len(hits) == 1:
            match = hits[0]
    if match is None:
        logger.info("prefill_doctor_unresolved", doctor=str(doctor)[:40])
        return args

    new_args = {**args, "doctor": str(match.get("id"))}
    spec_val = new_args.get("specialty")
    if not (isinstance(spec_val, str) and spec_val.strip()):
        specs = match.get("specialties") or []
        if specs and isinstance(specs[0], dict) and specs[0].get("name"):
            new_args["specialty"] = specs[0]["name"]
    logger.info(
        "prefill_doctor_resolved",
        doctor_in=str(doctor)[:40],
        doctor_id=match.get("id"),
        specialty=new_args.get("specialty"),
    )
    return new_args


def _load_odontoking_prompt(
    wa_id: str,
    *,
    is_new_patient: bool = True,
    ci_paciente: str | None = None,
    seguro_paciente: str | None = None,
    nombre_registrado: str | None = None,
    nombre_whatsapp: str | None = None,
    person_id: int | None = None,
    citas: list[dict] | None = None,
    cita_activa: int | None = None,
    pacientes_a_cargo: list[dict] | None = None,
    recordatorios: list[dict] | None = None,
) -> str:
    """Render the odontoking system prompt with Bolivia local datetime and patient context."""
    now = datetime.now(_TZ_BOLIVIA)
    day_name = _DIAS_ES[now.weekday()]
    month_name = _MESES_ES[now.month - 1]
    current_datetime = f"{day_name} {now.day:02d} {month_name} {now.year} {now.strftime('%H:%M')}"

    # A patient without a real registered name is treated as new so the onboarding
    # flow (name, age, insurance) runs even if they already exist in the CRM.
    if not nombre_registrado:
        is_new_patient = True
    # A patient with at least one cita is, by definition, not new (CONTRATO A).
    if citas:
        is_new_patient = False

    context_lines = [
        "# Contexto del paciente",
        f"wa_id: {wa_id}",
    ]
    if person_id:
        context_lines.append(f"person_id: {person_id}")
    context_lines.append(f"paciente_nuevo: {'true' if is_new_patient else 'false'}")
    # Name resolution: registered CRM name → WhatsApp profile name → ask the patient.
    # (verify_insurance.patient_name takes priority during the flow, per the prompt.)
    if nombre_registrado:
        context_lines.append(f"nombre_registrado: {nombre_registrado}")
    elif nombre_whatsapp:
        context_lines.append(
            f"nombre_whatsapp: {nombre_whatsapp}  # nombre del perfil de WhatsApp; úsalo si no hay nombre del seguro, sin volver a preguntarlo"
        )
    else:
        context_lines.append("nombre_registrado: null  # pedir el nombre completo al paciente")
    # This insurance PRE-FILL context block is classic-mode only. In WhatsApp-forms mode the CRM
    # form handles cédula/seguro and insurance is NOT a hard gate on booking; verify_insurance is
    # still bound there but used REACTIVELY (the agent asks for the carnet fresh in-conversation,
    # per the prompt's "SEGURO Y MEMBRESÍA" rule), so injecting this hard-gate pre-fill would be
    # contradictory noise. Keep these instructions exactly as-is when forms are off.
    if not settings.WHATSAPP_FLOWS_ENABLED:
        if ci_paciente:
            context_lines.append(f"ci_paciente_registrada: {ci_paciente}")
        else:
            context_lines.append("ci_paciente_registrada: null")
        if seguro_paciente:
            context_lines.append(f"seguro_registrado: {seguro_paciente}")
        else:
            context_lines.append("seguro_registrado: null")
        # Insurance is a HARD GATE on every booking (policy: always re-validate). A previously
        # registered insurance belongs to the wa_id owner, can lapse (mora/vencido), and is NOT
        # valid for a third-party patient — so it never skips verify_insurance, it only pre-fills.
        if ci_paciente and seguro_paciente:
            context_lines.append(
                "# ⚠️ SEGURO — el titular tiene un seguro registrado. Úsalo SOLO para pre-llenar "
                "(confírmalo con el paciente y pide/confirma el carnet), pero SIEMPRE vuelve a llamar "
                "verify_insurance en esta conversación. Si la cita es PARA OTRA PERSONA, ese seguro NO "
                "aplica: valida el seguro y el carnet de esa persona. Si NO da VIGENTE, NO agendes."
            )
        else:
            context_lines.append(
                "# ⚠️ SEGURO NO VERIFICADO — OBLIGATORIO antes de agendar (también pacientes recurrentes): "
                "pregunta por el seguro; si elige una aseguradora pide el carnet y llama verify_insurance. "
                "Si la verificación NO da VIGENTE, NO agendes. Solo 'No tengo seguro' puede agendar como particular."
            )

    # Pre-loaded cita history (CONTRATO A): the agent reviews every past/upcoming cita before
    # replying, so it never re-asks "¿ya vino? ¿qué cita tuvo?" — it reads them here.
    context_lines.extend(_format_citas_context(citas, cita_activa))
    # People this contact schedules for (forms mode): lets the agent answer "¿la cita de mi hijo?"
    # without sending a form.
    context_lines.extend(_format_pacientes_a_cargo(pacientes_a_cargo))
    context_lines.extend(_format_recordatorios(recordatorios))

    context = "\n".join(context_lines)
    return _PROMPT_TEMPLATE.format(current_datetime=current_datetime) + f"\n\n{context}"


def _chat_session_id(wa_id: str) -> str:
    return f"{wa_id}"


def _extract_mensaje(content: str) -> str:
    """Pull the user-facing text out of the LLM's JSON response.

    The model must reply with a single {"mensaje": "..."} object, but weaker models
    (gpt-4.1-mini) sometimes emit TWO concatenated objects — which is invalid JSON, so a
    plain json.loads() falls back to the raw string and the patient sees literal braces.
    This tolerates that: single object → its mensaje; several concatenated objects → the
    FIRST mensaje (the correct next step; a premature second message is dropped); malformed
    but recognizable → regex the first mensaje; otherwise the raw text. Never leaks JSON.
    """
    text = (content or "").strip()
    if not text:
        return text
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and obj.get("mensaje"):
            return str(obj["mensaje"])
        if isinstance(obj, dict):
            return text  # valid JSON without mensaje — leave as-is
    except (json.JSONDecodeError, TypeError):
        pass
    # Concatenated JSON objects: decode them one by one and take the first mensaje.
    decoder = json.JSONDecoder()
    idx, n = 0, len(text)
    found: list[str] = []
    while idx < n:
        while idx < n and text[idx] in " \t\r\n":
            idx += 1
        if idx >= n or text[idx] != "{":
            break
        try:
            obj, idx = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict) and obj.get("mensaje"):
            found.append(str(obj["mensaje"]))
    if found:
        if len(found) > 1:
            logger.warning("odontoking_multiple_mensajes", count=len(found))
        return found[0]
    # Malformed JSON that still contains a mensaje field → recover it without the braces.
    if '"mensaje"' in text:
        m = re.search(r'"mensaje"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if m:
            try:
                return json.loads(f'"{m.group(1)}"')
            except json.JSONDecodeError:
                return m.group(1)
        # Truncated/unterminated string: take from mensaje to the end, strip trailing }/".
        m2 = re.search(r'"mensaje"\s*:\s*"(.+)', text, re.DOTALL)
        if m2:
            frag = re.sub(r'["}\s]+$', '', m2.group(1))
            return frag.replace('\\n', '\n').replace('\\"', '"')
    return text


def _extract_handoff(content: str) -> Optional[dict[str, Any]]:
    """Parse the LLM reply for a CONTRATO B handoff signal, or None.

    The agent only EMITS the signal (the CRM executes the pause). A handoff object looks like
    {"mensaje": "...", "action": "handoff", "motivo": "<breve>", "fuera_de_horario": <bool>};
    tolerates it being one of several concatenated JSON objects.
    """
    text = (content or "").strip()
    if '"handoff"' not in text:
        return None
    decoder = json.JSONDecoder()
    idx, n = 0, len(text)
    while idx < n:
        while idx < n and text[idx] in " \t\r\n":
            idx += 1
        if idx >= n or text[idx] != "{":
            break
        try:
            obj, idx = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict) and obj.get("action") == "handoff":
            return {
                "action": "handoff",
                "motivo": str(obj.get("motivo") or ""),
                "fuera_de_horario": bool(obj.get("fuera_de_horario", False)),
            }
    return None


def _serialize_message(m: BaseMessage) -> str:
    return json.dumps(m.model_dump(), ensure_ascii=False, default=str)


def _persist_messages(wa_id: str, messages: list[BaseMessage]) -> None:
    sid = _chat_session_id(wa_id)
    for m in messages:
        try:
            database_service.save_chat_message(sid, _serialize_message(m))
        except Exception as e:
            logger.warning("chat_history_save_failed", wa_id=wa_id, error=str(e))


async def _persist_messages_async(wa_id: str, messages: list[BaseMessage]) -> None:
    await asyncio.to_thread(_persist_messages, wa_id, messages)


class OdontokingAgent:
    """LangGraph agent for the Odontoking WhatsApp dental clinic assistant."""

    def __init__(self):
        """Initialize the Odontoking agent with its own LLM and tool set."""
        self._llm = ChatOpenAI(
            model=settings.ODONTOKING_LLM_MODEL,
            api_key=SecretStr(settings.OPENAI_API_KEY),
            max_tokens=4096,  # pyright: ignore[reportCallIssue]
            temperature=0.2,
            timeout=settings.LLM_REQUEST_TIMEOUT,
            max_retries=2,
        ).bind_tools(_ODONTOKING_TOOLS)
        self.tools_by_name = {t.name: t for t in _ODONTOKING_TOOLS}
        self._connection_pool: Optional[PostgresConnPool] = None
        self._graph: Optional[CompiledStateGraph] = None
        # Tracked background tasks so shutdown can await them and not lose chat history
        self._persist_tasks: set[asyncio.Task] = set()
        logger.info("odontoking_agent_initialized", tools=list(self.tools_by_name.keys()))

    async def _get_connection_pool(self) -> Optional[PostgresConnPool]:
        if self._connection_pool is None:
            try:
                connection_url = (
                    "postgresql://"
                    f"{quote_plus(settings.POSTGRES_USER)}:{quote_plus(settings.POSTGRES_PASSWORD)}"
                    f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
                )
                self._connection_pool = AsyncConnectionPool(
                    connection_url,
                    open=False,
                    min_size=1,
                    max_size=settings.POSTGRES_POOL_SIZE,
                    reconnect_timeout=30,
                    kwargs={
                        "autocommit": True,
                        "connect_timeout": 10,
                        "prepare_threshold": None,
                        "row_factory": dict_row,
                        # TCP keepalives — prevent the remote server from closing idle connections
                        "keepalives": 1,
                        "keepalives_idle": 30,
                        "keepalives_interval": 10,
                        "keepalives_count": 5,
                    },
                )
                await self._connection_pool.open(wait=True, timeout=30)
                logger.info("odontoking_connection_pool_created")
            except Exception as e:
                logger.exception("odontoking_connection_pool_failed", error=str(e))
                return None
        return self._connection_pool

    async def _chat(self, state: GraphState, config: RunnableConfig) -> Command:
        metadata = config.get("metadata", {})
        wa_id = metadata.get("wa_id", "unknown")
        thread_id = config.get("configurable", {}).get("thread_id")
        system_prompt = _load_odontoking_prompt(
            wa_id,
            is_new_patient=metadata.get("is_new_patient", True),
            ci_paciente=metadata.get("ci_paciente"),
            seguro_paciente=metadata.get("seguro_paciente"),
            nombre_registrado=metadata.get("nombre_registrado"),
            nombre_whatsapp=metadata.get("nombre_whatsapp"),
            person_id=metadata.get("person_id"),
            citas=metadata.get("citas"),
            cita_activa=metadata.get("cita_activa"),
            pacientes_a_cargo=metadata.get("pacientes_a_cargo"),
            recordatorios=metadata.get("recordatorios"),
        )
        # When the deterministic intake already collected steps 1-6, inject that data so the
        # LLM continues from step 7 and never re-asks.
        intake_handoff = metadata.get("intake_handoff")
        if intake_handoff:
            system_prompt = f"{system_prompt}\n\n{intake_handoff}"

        # Build messages with LangChain types directly — bypasses Message max_length validation
        langchain_messages = [SystemMessage(content=system_prompt)] + list(state.messages)

        try:
            response_message = await self._llm.ainvoke(
                langchain_messages,
                config={"callbacks": config.get("callbacks", [])},
            )
            response_message = process_llm_response(response_message)
            logger.info("odontoking_llm_response", thread_id=thread_id)

            if isinstance(response_message, AIMessage) and response_message.tool_calls:
                goto = "tool_call"
            else:
                goto = END

            return Command(update={"messages": [response_message]}, goto=goto)
        except Exception as e:
            logger.exception("odontoking_chat_failed", thread_id=thread_id, error=str(e))
            raise

    async def _tool_call(self, state: GraphState, config: Optional[RunnableConfig] = None) -> Command:
        tool_calls = state.messages[-1].tool_calls
        # conversation_id is injected into enviar_formulario here (never supplied by the LLM): it
        # comes from the inbound event via the graph config metadata.
        meta = (config or {}).get("metadata") or {}
        conversation_id = meta.get("conversation_id")
        # Whether the patient currently has an active (Agendado) cita. Used to guard cancel/reschedule
        # forms: sin cita activa, the form must not be sent (the CRM would answer status:none). Only
        # cita_activa counts — a patient whose citas are ALL cancelled/attended (cita_activa=None but
        # citas non-empty) has nothing to cancel/move, so the guard must still fire for them.
        tiene_cita_activa = bool(meta.get("cita_activa"))

        async def _execute(tc: dict) -> ToolMessage:
            args = tc["args"]
            if tc["name"] == "enviar_formulario" and conversation_id is not None:
                # saludar: on first contact (new patient) the tool sends the welcome greeting BEFORE
                # the form, so it lands first in the chat (the form is a side-effect of this call).
                args = {**args, "conversation_id": conversation_id, "saludar": bool(meta.get("is_new_patient"))}
                # Deterministically resolve the doctor NAME the model passed to its CRM id (and carry
                # the doctor's specialty) so the booking form can pre-select both — the model is no
                # longer the weak link for the prefill.
                if args.get("flow") == "paciente_tercero":
                    args = await _resolve_doctor_in_prefill(args)
                # Deterministic guard: block cancelar/reprogramar when there is no active cita, so a
                # weak model can't send the wrong form; the tool returns a "offer booking" signal.
                if not tiene_cita_activa and args.get("flow") in ("cancelar_cita", "reprogramar_cita"):
                    args = {**args, "sin_cita_activa": True}
            try:
                result = await self.tools_by_name[tc["name"]].ainvoke(args)
            except GraphBubbleUp:
                # ask_human's interrupt() raises GraphInterrupt (a GraphBubbleUp / Exception
                # subclass). It MUST bubble up to pause the graph — never swallow it here.
                raise
            except Exception as e:
                logger.warning("tool_execution_failed", tool=tc["name"], error=str(e))
                result = json.dumps({"error": str(e)})
            return ToolMessage(content=result, name=tc["name"], tool_call_id=tc["id"])

        if len(tool_calls) == 1:
            outputs = [await _execute(tool_calls[0])]
        else:
            outputs = list(await asyncio.gather(*[_execute(tc) for tc in tool_calls]))

        return Command(update={"messages": outputs}, goto="chat")

    async def create_graph(self) -> Optional[CompiledStateGraph]:
        """Build and compile the LangGraph state machine, creating the Postgres checkpointer."""
        if self._graph is None:
            try:
                builder = StateGraph(GraphState)
                builder.add_node("chat", self._chat, destinations=("tool_call", END))
                builder.add_node(
                    "tool_call",
                    self._tool_call,
                    destinations=("chat",),
                    retry_policy=RetryPolicy(max_attempts=3),
                )
                builder.set_entry_point("chat")
                builder.set_finish_point("chat")

                pool = await self._get_connection_pool()
                if pool:
                    checkpointer = AsyncPostgresSaver(pool)
                    await checkpointer.setup()
                    checkpointer_kind = "postgres"
                else:
                    # No Postgres reachable (local dev / evals without the DB up): fall back to an
                    # in-memory checkpointer so the graph still runs with per-process thread memory,
                    # instead of raising "No checkpointer set" on every invocation.
                    checkpointer = MemorySaver()
                    checkpointer_kind = "memory"

                self._graph = builder.compile(checkpointer=checkpointer, name="OdontokingAgent")
                logger.info("odontoking_graph_created", checkpointer=checkpointer_kind)
            except Exception as e:
                logger.exception("odontoking_graph_creation_failed", error=str(e))
                raise
        return self._graph

    async def _get_graph(self) -> CompiledStateGraph:
        if self._graph is None:
            self._graph = await self.create_graph()
        if self._graph is None:
            raise RuntimeError("odontoking graph initialization failed")
        return self._graph

    async def _intake_turn(
        self,
        wa_id: str,
        text: str,
        nombre_whatsapp: Optional[str],
    ) -> tuple[Optional[str], Optional[str]]:
        """Drive the deterministic intake for one turn.

        Returns (reply, handoff):
          - reply not None  → send this question/message and end the turn (intake in progress).
          - reply None      → fall through to the LLM (Phase 2). handoff carries the collected
                              data when the intake is complete, else None (general chat).
        """
        state = await intake_store.get(wa_id)

        async def _start() -> tuple[Optional[str], Optional[str]]:
            # nombre is left unset so the intake always asks for the real name; the WhatsApp
            # profile name is stored only to build the combined CRM name later.
            fresh = new_state(nombre_whatsapp=nombre_whatsapp)
            result = await advance_intake(fresh, None, wa_id=wa_id)
            await intake_store.set(wa_id, result.state)
            reply = f"¡Hola! Gracias por escribir a Odontoking 🦷✨, Con gusto le ayudo a agendar su cita. {result.reply}" if result.reply else result.reply
            return reply, None

        if state is None:
            # The bot's purpose is booking and most patients open with "Hola" rather than the
            # word "cita", so start the deterministic intake on first contact — EXCEPT for pure
            # FAQ questions (location/hours/price), which the LLM answers.
            if is_faq_query(text) and not is_booking_intent(text):
                return None, None
            return await _start()

        # Active deterministic booking (Phase 2) → keep driving it; do NOT restart on a
        # booking keyword that may appear mid-flow.
        booking_phase = state.get("booking_phase")
        if booking_phase and booking_phase != "done":
            return await self._booking_turn(wa_id, state, text)

        if state.get("completo"):
            # Post-booking sub-flow (cancel / correct name) takes priority: "cancelar mi cita"
            # also contains the booking keyword "cita", so it must be checked before re-booking.
            if state.get("post_phase") or is_cancel_intent(text) or is_name_change_intent(text):
                return await self._postbooking_turn(wa_id, state, text)
            # Reschedule a confirmed cita (change day/time, same doctor) → re-enter the booking
            # flow at the día step instead of starting a brand-new booking (which duplicated it).
            if is_reschedule_intent(text) and state.get("doctor_id"):
                state["wa_id"] = wa_id
                result = await start_reschedule(state)
                await intake_store.set(wa_id, result.state)
                return result.reply, None
            if is_booking_intent(text):
                return await _start()  # a new booking → re-run from step 1
            # Booking finished (or nothing pending) → post-booking chat goes to the LLM.
            return None, None

        result = await advance_intake(state, text, wa_id=wa_id)
        await intake_store.set(wa_id, result.state)
        if not result.completed:
            return result.reply, None

        logger.info("intake_completed", wa_id=wa_id)
        # Best-effort progressive CRM capture (lead + insurance + CI) so the patient is recorded
        # even if they abandon before confirming the appointment.
        self._persist_intake_crm(wa_id, result.state)
        # Every motivo (fixed buttons AND free-text/"Otro") runs the deterministic booking flow;
        # free-text is routed to a specialty/service by the bounded LLM classifier inside booking.
        return await self._booking_turn(wa_id, result.state, None)

    async def _booking_turn(
        self, wa_id: str, state: dict, text: Optional[str]
    ) -> tuple[Optional[str], Optional[str]]:
        """Drive one deterministic Phase-2 booking turn and persist the updated state."""
        state["wa_id"] = wa_id
        result = await advance_booking(state, text)
        await intake_store.set(wa_id, result.state)
        return result.reply, None

    async def _postbooking_turn(
        self, wa_id: str, state: dict, text: Optional[str]
    ) -> tuple[Optional[str], Optional[str]]:
        """Drive one deterministic post-booking turn (cancel / correct name) and persist state."""
        state["wa_id"] = wa_id
        result = await advance_postbooking(state, text)
        if result.clear:
            await intake_store.clear(wa_id)  # cancelled → fresh start for any new booking
        else:
            await intake_store.set(wa_id, result.state)
        return result.reply, None

    def _persist_intake_crm(self, wa_id: str, state: dict) -> None:
        """Fire-and-forget CRM capture of the intake data (person + insurance).

        Uses the atomic tools: save_patient for identity/demographics and save_insurance for the
        insurance data — no appointment is created here (that happens at confirmation).
        """

        async def _do() -> None:
            try:
                name = crm_display_name(state.get("nombre_whatsapp"), state.get("nombre"))
                patient_payload: dict = {
                    "wa_id": wa_id,
                    "person_name": name,
                    "person_phone": wa_id,
                    "edad_paciente": state.get("edad"),
                    "is_for_self": bool(state.get("is_for_self")),
                }
                if state.get("is_for_self") is False:
                    patient_payload["nombre_paciente_de_otra_persona"] = state.get("tercero_nombre")
                    patient_payload["edad_paciente_de_otra_persona"] = state.get("tercero_edad")
                await save_patient.ainvoke(patient_payload)
                if state.get("seguro") and state.get("seguro") != "No tengo seguro":
                    await save_insurance.ainvoke({
                        "wa_id": wa_id,
                        "person_name": name,
                        "seguro_de_vida": state.get("seguro"),
                        "numero_carnet": state.get("ci"),
                        "estado_seguro": state.get("seguro_estado"),
                    })
            except Exception as e:
                logger.warning("intake_crm_persist_failed", wa_id=wa_id, error=str(e))

        task = asyncio.create_task(_do())
        self._persist_tasks.add(task)
        task.add_done_callback(self._persist_tasks.discard)

    def _schedule_persist(self, wa_id: str, messages: list[BaseMessage]) -> None:
        """Fire-and-forget persist of messages to chat_histories_odonto (tracked for shutdown)."""
        if not messages:
            return
        task = asyncio.create_task(_persist_messages_async(wa_id, messages))
        self._persist_tasks.add(task)
        task.add_done_callback(self._persist_tasks.discard)

    async def _handle_direct_cancel(
        self,
        wa_id: str,
        text: str,
        citas: list[dict] | None,
        cita_activa: int | None,
    ) -> Optional[str]:
        """Deterministic 2-turn direct cancel for a patient with exactly ONE active cita.

        Turn 1 (intent "cancelar", not reschedule): ask to confirm with the cita's detail and
        remember the pending lead_id. Turn 2 ("Sí" → cancel directly via the CRM; "No" → keep it).
        Returns the reply to send (and short-circuits the graph), or None to fall through to the LLM
        (e.g. 2+ citas, ambiguous text, or the CRM cancel failed).
        """
        key = f"{_CANCEL_CONFIRM_PREFIX}{wa_id}"
        pending_raw = await cache_service.get(key)

        # ── Turn 2: a confirmation is pending ──
        if pending_raw:
            if _is_cancel_yes(text):
                try:
                    pending = json.loads(pending_raw)
                except (json.JSONDecodeError, TypeError):
                    pending = {}
                lead_id = pending.get("lead_id")
                await cache_service.delete(key)
                result = await cancel_appointment(wa_id, lead_id=lead_id)
                if result.get("success"):
                    reply = ("Su cita ha sido cancelada ✅. Cuando guste agendar una nueva, escríbame "
                             "y con gusto le ayudo 🦷.")
                    logger.info("direct_cancel_done", wa_id=wa_id, lead_id=result.get("lead_id"))
                    self._schedule_persist(wa_id, [HumanMessage(content=text), AIMessage(content=reply)])
                    return reply
                # CRM cancel failed → let the LLM offer the form fallback.
                logger.warning("direct_cancel_crm_failed", wa_id=wa_id, result=result)
                return None
            if _is_cancel_no(text):
                await cache_service.delete(key)
                reply = "Perfecto, su cita se mantiene sin cambios. ¿Hay algo más en que pueda ayudarle? 😊"
                self._schedule_persist(wa_id, [HumanMessage(content=text), AIMessage(content=reply)])
                return reply
            # Ambiguous reply while awaiting confirmation → re-ask once, keep the pending flag.
            return ('Para confirmar la cancelación responda "Sí"; si desea conservar su cita responda "No". 🙏')

        # ── Turn 1: detect a cancel intent for a single active cita ──
        if not _wants_direct_cancel(text):
            return None
        cita = _single_active_cita(citas, cita_activa)
        if cita is None:
            return None  # 0 or 2+ active citas → LLM/form handles it (incl. the sin_cita_activa guard)
        lead_id = cita.get("lead_id")
        await cache_service.set(
            key, json.dumps({"lead_id": lead_id}, ensure_ascii=False), ttl=_CANCEL_CONFIRM_TTL
        )
        reply = (
            f"¿Está seguro de que desea cancelar su cita {_fmt_cita_detail(cita)}?\n\n"
            'Responda "Sí" para cancelarla o "No" para conservarla.'
        )
        logger.info("direct_cancel_confirm_asked", wa_id=wa_id, lead_id=lead_id)
        self._schedule_persist(wa_id, [HumanMessage(content=text), AIMessage(content=reply)])
        return reply

    async def get_response(
        self,
        messages: list,
        wa_id: str,
        *,
        is_new_patient: bool = True,
        ci_paciente: str | None = None,
        seguro_paciente: str | None = None,
        nombre_registrado: str | None = None,
        nombre_whatsapp: str | None = None,
        person_id: int | None = None,
        citas: list[dict] | None = None,
        cita_activa: int | None = None,
        conversation_id: int | None = None,
        pacientes_a_cargo: list[dict] | None = None,
        recordatorios: list[dict] | None = None,
        handoff_callback: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
    ) -> str:
        """Process a WhatsApp message and return the agent's texto response.

        Returns the content of the 'mensaje' JSON field from the LLM response,
        or the raw text if the response is not a valid JSON. When `citas` is not supplied by the
        caller (CONTRATO A), the patient's full cita history is preloaded here before the LLM runs
        so the agent reviews it up front instead of re-asking.
        """
        last_user_text = messages[-1].content if messages else ""

        # ── Deterministic intake (steps 1-6) — guarantees the flow order ──
        # When active it asks one canonical question per turn and returns early; when the
        # intake completes it hands off to the LLM (steps 7-12) with all data injected.
        # AI_BOOKING_ENABLED bypasses the intake entirely so the LLM graph drives everything.
        # WhatsApp forms mode bypasses the deterministic intake just like AI_BOOKING_ENABLED: the
        # LLM detects the intent and sends a form instead of collecting data by chat.
        intake_handoff: Optional[str] = None
        if settings.INTAKE_ENABLED and not settings.AI_BOOKING_ENABLED and not settings.WHATSAPP_FLOWS_ENABLED:
            # The intake always asks for the real name (the WhatsApp profile name is often
            # wrong). nombre_whatsapp is only used to build the CRM name afterwards.
            intake_reply, intake_handoff = await self._intake_turn(
                wa_id, last_user_text, nombre_whatsapp
            )
            if intake_reply is not None:
                return intake_reply

        # ── Reminder reply short-circuit (C1/C2) ──
        # A "Sí"/"No" answering an appointment reminder is deterministic: if there is exactly ONE
        # unanswered reminder and the text is a clear yes/no, register it directly instead of trusting
        # the LLM (gpt-4o-mini tended to fall into the reception menu here). Ambiguous text or 0/2+
        # reminders fall through to the LLM.
        if settings.WHATSAPP_FLOWS_ENABLED and conversation_id:
            hit = _detect_reminder_reply(last_user_text, recordatorios)
            if hit is not None:
                respuesta, activity_id = hit
                invoke_args: dict[str, Any] = {
                    "respuesta": respuesta,
                    "conversation_id": conversation_id,
                }
                # Pass activity_id only when known; the CRM applies the reply to the single pending
                # reminder when it is omitted (so a missing seeded id doesn't block registration).
                if activity_id is not None:
                    invoke_args["activity_id"] = activity_id
                result = await responder_recordatorio.ainvoke(invoke_args)
                ok = '"success": true' in result or '"success":true' in result
                reply_text: Optional[str] = None
                if ok and respuesta == "confirma":
                    reply_text = "¡Gracias por confirmar su cita! Le esperamos. 🙌"
                elif ok and respuesta == "no_asiste":
                    reply_text = ("Entendido, gracias por avisar que no podrá asistir. ¿Desea reprogramar su "
                                  "cita para otro día o prefiere cancelarla?")
                if reply_text is not None:
                    # This turn short-circuits the graph, so persist it to chat_histories_odonto
                    # (best-effort) — otherwise the transcript would miss the reminder exchange.
                    self._schedule_persist(
                        wa_id,
                        [HumanMessage(content=last_user_text), AIMessage(content=reply_text)],
                    )
                    return reply_text
                # Tool failed (e.g. multiple_reminders / error): fall through to the LLM.
                logger.info("reminder_reply_fallthrough", respuesta=respuesta, result=result[:120])

        # CONTRATO A: preload the patient's full cita history before invoking the LLM so it is in
        # the system prompt from the first token. Best-effort — never blocks the reply on failure.
        if citas is None:
            person_id, citas, cita_activa = await preload_patient_citas(wa_id)

        # ── Direct cancel short-circuit (una sola cita activa) ──
        # When the patient has exactly ONE active cita and asks to cancel it, confirm with the
        # cita's detail and cancel directly via the CRM on "Sí" — no form. Deterministic; falls
        # through to the LLM for 2+ citas, reschedule intent, or a CRM failure.
        if settings.WHATSAPP_FLOWS_ENABLED and conversation_id:
            cancel_reply = await self._handle_direct_cancel(wa_id, last_user_text, citas, cita_activa)
            if cancel_reply is not None:
                return cancel_reply

        graph = await self._get_graph()
        callbacks: list[BaseCallbackHandler] = [langfuse_callback_handler] if settings.LANGFUSE_TRACING_ENABLED else []
        config: RunnableConfig = {
            "configurable": {"thread_id": wa_id},
            "callbacks": callbacks,
            "metadata": {
                "wa_id": wa_id,
                "is_new_patient": is_new_patient,
                "ci_paciente": ci_paciente,
                "seguro_paciente": seguro_paciente,
                "nombre_registrado": nombre_registrado,
                "nombre_whatsapp": nombre_whatsapp,
                "person_id": person_id,
                "citas": citas,
                "cita_activa": cita_activa,
                "conversation_id": conversation_id,
                "pacientes_a_cargo": pacientes_a_cargo,
                "recordatorios": recordatorios,
                "intake_handoff": intake_handoff,
            },
            "recursion_limit": 50,
        }

        from app.services.memory import memory_service

        try:
            if settings.ODONTOKING_MEMORY_ENABLED:
                state_result, memory_result = await asyncio.gather(
                    graph.aget_state(config),
                    memory_service.search(wa_id, messages[-1].content if messages else ""),
                    return_exceptions=True,
                )
                state = state_result if not isinstance(state_result, BaseException) else None
                relevant_memory = memory_result if isinstance(memory_result, str) else ""
                if isinstance(memory_result, Exception):
                    logger.warning("odontoking_memory_search_failed", wa_id=wa_id, error=str(memory_result))
            else:
                state = await graph.aget_state(config)
                relevant_memory = ""

            existing_count = len(state.values.get("messages", [])) if state and state.values else 0

            if state and state.next:
                logger.info("odontoking_resuming_graph", wa_id=wa_id)
                response = await graph.ainvoke(Command(resume=messages[-1].content), config=config)
            else:
                response = await graph.ainvoke(
                    input={"messages": dump_messages(messages), "long_term_memory": relevant_memory},
                    config=config,
                )

            # Persist new messages (human + AI + tool) to chat_histories_odonto
            new_msgs = [m for m in response.get("messages", [])[existing_count:] if isinstance(m, BaseMessage)]
            self._schedule_persist(wa_id, new_msgs)

            # Extract mensaje from JSON response
            ai_messages = [m for m in response.get("messages", []) if isinstance(m, AIMessage) and m.content]
            if not ai_messages:
                return "Disculpe, ocurrió un error. Por favor intente de nuevo."

            last_content = ai_messages[-1].content
            if isinstance(last_content, list):
                last_content = " ".join(
                    block.get("text", "") for block in last_content if isinstance(block, dict)
                )

            # Extract the patient-facing text, tolerating malformed/multiple JSON objects.
            mensaje = _extract_mensaje(last_content)

            # CONTRATO B: if the LLM signalled a handoff, surface it to the caller (the CRM
            # executes the actual pause). The agent never pauses itself — it only emits.
            handoff = _extract_handoff(last_content)
            if handoff:
                logger.info(
                    "agent_handoff_signaled",
                    wa_id=wa_id,
                    motivo=handoff["motivo"],
                    fuera_de_horario=handoff["fuera_de_horario"],
                )
                if handoff_callback is not None:
                    try:
                        await handoff_callback(handoff)
                    except Exception as ho_err:
                        logger.warning("agent_handoff_callback_failed", wa_id=wa_id, error=str(ho_err))

            # Fire-and-forget memory update (only when memory is enabled)
            if settings.ODONTOKING_MEMORY_ENABLED:
                try:
                    openai_msgs = cast(list[dict], convert_to_openai_messages(response["messages"]))
                    asyncio.create_task(memory_service.add(wa_id, openai_msgs, {"wa_id": wa_id}))
                except Exception as mem_err:
                    logger.warning("odontoking_memory_add_skipped", wa_id=wa_id, error=str(mem_err))

            return str(mensaje)

        except GraphInterrupt:
            state = await graph.aget_state(config)
            interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Aguarde un momento."
            return str(interrupt_value)
        except GraphRecursionError:
            logger.warning("odontoking_recursion_limit_hit", wa_id=wa_id)
            try:
                await self.clear_history(wa_id)
                logger.info("odontoking_stuck_state_cleared", wa_id=wa_id)
            except Exception as clear_err:
                logger.warning("odontoking_clear_history_failed", wa_id=wa_id, error=str(clear_err))
            return "Lo siento, ocurrió un error procesando su solicitud. Por favor, intente nuevamente con su consulta."
        except Exception as e:
            logger.exception("odontoking_get_response_failed", wa_id=wa_id, error=str(e))
            raise


    async def close(self) -> None:
        """Await pending persist tasks and close the connection pool on shutdown."""
        if self._persist_tasks:
            await asyncio.gather(*self._persist_tasks, return_exceptions=True)
        if self._connection_pool:
            await self._connection_pool.close()
            logger.info("odontoking_connection_pool_closed")

    async def clear_history(self, wa_id: str) -> None:
        """Delete all LangGraph checkpoint data for a given WhatsApp ID."""
        pool = await self._get_connection_pool()
        if pool is None:
            raise RuntimeError("connection pool unavailable")
        async with pool.connection() as conn:
            async with conn.pipeline():
                for table in settings.CHECKPOINT_TABLES:
                    await conn.execute(
                        sql.SQL("DELETE FROM {} WHERE thread_id = %s").format(sql.Identifier(table)),
                        (wa_id,),
                    )
        logger.info("odontoking_history_cleared", wa_id=wa_id)


odontoking_agent = OdontokingAgent()
