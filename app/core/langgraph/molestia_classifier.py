"""Classify a free-text dental molestia into a real specialty + service.

Used by the deterministic booking flow (booking.py) when the patient describes their molestia
in free text (or picks "Otro"), instead of one of the 4 fixed buttons. A single bounded LLM
call receives the molestia plus our REAL specialties and services and returns only the IDs -
the LLM classifies, it never renders the conversation, so it cannot loop or hallucinate slots.

Robustness: the returned IDs are validated against the real catalogs; on any LLM failure or an
invalid id we fall back to a keyword map, and finally to the "General" specialty - the flow is
never left without a specialty to offer doctors for.
"""

import asyncio
import unicodedata
from typing import Optional

from langchain_core.runnables.config import RunnableConfig
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, SecretStr

from app.core.config import settings
from app.core.logging import logger
from app.core.observability import langfuse_callback_handler

# Keyword fallback used only when the LLM is unavailable/invalid: molestia keyword → ordered
# specialty-name keywords (matched against the real get_specialties names).
_FALLBACK_KEYWORDS: list[tuple[tuple[str, ...], list[str]]] = [
    (("corona", "protesis", "puente", "incrustacion", "carilla"), ["rehabilita", "protesis", "estetica"]),
    (("bracket", "ortodoncia", "frenos", "frenillos", "alinea"), ["ortodon"]),
    (("ortopedia", "maxilar"), ["ortopedia"]),
    (("implante",), ["implanto"]),
    (("nervio", "conducto", "endodoncia", "absceso", "pulpa"), ["endodon"]),
    (("encia", "sangra", "periodon", "sarro", "placa", "inflamad"), ["periodon"]),
    (("limpieza", "profilaxis", "blanqueamiento", "estetic"), ["periodon", "estetica", "general"]),
    (("muela", "extraccion", "exodoncia", "cirugia", "cordal", "tercer molar"), ["cirugia", "general"]),
    (("quebrado", "roto", "fractura", "caries", "dolor", "empaste"), ["general", "endodon"]),
    (("niño", "nino", "niña", "nina", "pediatr", "infantil", "leche"), ["pediatr", "odontopediatr"]),
    (("radiograf", "tomograf", "panoramic"), ["radiolog"]),
]

# Last-resort specialty when nothing matches (the clinic's catch-all).
_DEFAULT_SPECIALTY_KW = ["general"]


class MolestiaClassification(BaseModel):
    """Structured result of classifying a molestia into a specialty + service."""

    specialty_id: int = Field(description="ID de la especialidad MÁS adecuada de la lista provista.")
    service_id: Optional[int] = Field(
        default=None, description="ID del servicio MÁS adecuado de la lista, o null si ninguno aplica claramente."
    )


def _norm(text: Optional[str]) -> str:
    """Lowercase and strip accents/whitespace for accent-insensitive matching."""
    t = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn").strip()


_llm_singleton: Optional[ChatOpenAI] = None


def _get_llm() -> ChatOpenAI:
    global _llm_singleton
    if _llm_singleton is None:
        _llm_singleton = ChatOpenAI(
            model=settings.ODONTOKING_LLM_MODEL,
            api_key=SecretStr(settings.OPENAI_API_KEY),
            temperature=0,
            timeout=settings.LLM_REQUEST_TIMEOUT,
            max_retries=1,
        )
    return _llm_singleton


def _catalog_text(specialties: list[dict], services: list[dict]) -> str:
    sp = "\n".join(f"- id={s.get('id')} {s.get('name')}" for s in specialties if s.get("id"))
    sv = "\n".join(f"- id={s.get('id')} {s.get('name')}" for s in services if s.get("id"))
    return f"Especialidades disponibles:\n{sp}\n\nServicios disponibles:\n{sv}"


async def _llm_classify(
    molestia: str, specialties: list[dict], services: list[dict]
) -> Optional[MolestiaClassification]:
    """Raw bounded LLM call. Returns the structured result, or None on any failure."""
    system = (
        "Eres un clasificador para una clínica dental en Bolivia. Dada la molestia o servicio que "
        "describe el paciente, elige de las listas provistas la ESPECIALIDAD más adecuada y, si aplica, "
        "el SERVICIO más adecuado. Responde SOLO con los IDs exactos de las listas. No inventes IDs."
    )
    human = f"{_catalog_text(specialties, services)}\n\nMolestia del paciente: \"{molestia}\"\n\nElige specialty_id y service_id."
    config: RunnableConfig = {"callbacks": [langfuse_callback_handler]} if settings.LANGFUSE_TRACING_ENABLED else {}
    try:
        llm = _get_llm().with_structured_output(MolestiaClassification)
        result = await asyncio.wait_for(
            llm.ainvoke([("system", system), ("human", human)], config=config),
            timeout=settings.LLM_REQUEST_TIMEOUT + 5,
        )
        return result if isinstance(result, MolestiaClassification) else None
    except Exception as e:
        logger.warning("molestia_classify_llm_failed", error=str(e))
        return None


def _fallback_specialty_id(molestia: str, specialties: list[dict]) -> Optional[int]:
    norm = _norm(molestia)
    keyword_sets = [kws for triggers, kws in _FALLBACK_KEYWORDS if any(t in norm for t in triggers)]
    keyword_sets.append(_DEFAULT_SPECIALTY_KW)  # always end with the catch-all
    for kws in keyword_sets:
        for kw in kws:
            for sp in specialties:
                if kw in _norm(sp.get("name")):
                    return sp.get("id")
    return specialties[0].get("id") if specialties else None


def _fallback_service(molestia: str, services: list[dict]) -> Optional[dict]:
    norm = _norm(molestia)
    tokens = [w for w in norm.split() if len(w) >= 4]
    for sv in services:
        name = _norm(sv.get("name"))
        if any(tok in name for tok in tokens):
            return sv
    return None


async def classify_molestia(
    molestia: str, specialties: list[dict], services: list[dict]
) -> tuple[Optional[int], Optional[dict]]:
    """Return (specialty_id, service_dict|None) for a free-text molestia.

    Tries the LLM first, validates the returned IDs against the real catalogs, and falls back to
    keyword matching (and finally the "General" specialty) so the booking flow always has a
    specialty to offer doctors for.
    """
    if not specialties:
        return None, None

    valid_specialty_ids = {s.get("id") for s in specialties}
    services_by_id = {s.get("id"): s for s in services}

    result = await _llm_classify(molestia, specialties, services)
    specialty_id: Optional[int] = None
    service: Optional[dict] = None

    if result is not None:
        if result.specialty_id in valid_specialty_ids:
            specialty_id = result.specialty_id
        if result.service_id in services_by_id:
            service = services_by_id[result.service_id]

    if specialty_id is None:
        specialty_id = _fallback_specialty_id(molestia, specialties)
    if service is None:
        service = _fallback_service(molestia, services)

    logger.info(
        "molestia_classified",
        molestia=molestia[:60],
        specialty_id=specialty_id,
        service_id=(service or {}).get("id"),
        used_llm=result is not None,
    )
    return specialty_id, service
