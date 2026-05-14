"""Insurance verification tool for Odontoking."""

import json

import httpx
from langchain_core.tools import tool
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import logger

_HEADERS = {
    "accept": "application/json",
    "Authorization": f"Bearer {settings.ODONTOKING_API_TOKEN}",
}
_BASE = settings.ODONTOKING_API_URL


def _is_transient(exc: BaseException) -> bool:
    """Retry only on 5xx server errors, not 4xx client errors."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception(_is_transient),
    reraise=True,
)
async def _search_person(client: httpx.AsyncClient, person_email: str) -> list:
    """Search for a person in the CRM by email with retry on 5xx."""
    search_resp = await client.get(
        f"{_BASE}/api/v1/contacts/persons/search",
        params={"search": person_email, "searchFields": "emails:like"},
        headers=_HEADERS,
    )
    search_resp.raise_for_status()
    return search_resp.json().get("data", [])


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception(_is_transient),
    reraise=True,
)
async def _call_verify(client: httpx.AsyncClient, person_id: int, carnet_identidad: str, empresa_seguro: str) -> dict:
    verify_resp = await client.post(
        f"{_BASE}/api/insurance/verify",
        json={"person_id": person_id, "ci": carnet_identidad, "insurance_type": empresa_seguro},
        headers=_HEADERS,
    )
    verify_resp.raise_for_status()
    return verify_resp.json()


@tool
async def verify_insurance(empresa_seguro: str, carnet_identidad: str, wa_id: str) -> str:
    """Verify a patient's insurance coverage at Odontoking clinic.

    Args:
        empresa_seguro: Exact insurance company name. Must be one of:
            'Alianza', 'Nacional Seguro', or 'Membresía Odontoking'.
        carnet_identidad: Patient's ID card number (digits and dashes only, no spaces).
        wa_id: WhatsApp ID of the contact (used to look up the person in CRM).
    """
    try:
        person_email = f"{wa_id}@whatsapp.sofopolis.net"
        async with httpx.AsyncClient(timeout=15) as client:
            persons = await _search_person(client, person_email)

            if not persons:
                logger.warning("insurance_person_not_found", wa_id=wa_id)
                return json.dumps({"verified": False, "reason": "person not found in CRM"})

            person_id = persons[0]["id"]

            result = await _call_verify(client, person_id, carnet_identidad, empresa_seguro)
            logger.info("insurance_verified", wa_id=wa_id, empresa=empresa_seguro, person_id=person_id)
            return json.dumps(result, ensure_ascii=False)

    except httpx.HTTPStatusError as e:
        logger.exception("verify_insurance_failed", wa_id=wa_id, error=str(e))
        if e.response.status_code >= 500:
            return json.dumps({
                "verified": False,
                "error": "El servicio de verificación no está disponible en este momento. Inténtelo de nuevo en unos minutos.",
                "retry": True,
            })
        return json.dumps({"verified": False, "error": str(e)})
    except Exception as e:
        logger.exception("verify_insurance_failed", wa_id=wa_id, error=str(e))
        return json.dumps({"verified": False, "error": str(e)})
