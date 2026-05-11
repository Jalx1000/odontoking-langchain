"""Insurance verification tool for Odontoking."""

import json

import httpx
from langchain_core.tools import tool

from app.core.config import settings
from app.core.logging import logger

_HEADERS = {
    "accept": "application/json",
    "Authorization": f"Bearer {settings.ODONTOKING_API_TOKEN}",
}
_BASE = settings.ODONTOKING_API_URL


@tool
async def verify_insurance(empresa_seguro: str, carnet_identidad: str, wa_id: str) -> str:
    """Verify a patient's insurance coverage at Odontoking clinic.

    Args:
        empresa_seguro: Exact insurance company name. Must be one of:
            'Nacional Vida' or 'Membresía Odontoking'.
        carnet_identidad: Patient's ID card number (digits and dashes only, no spaces).
        wa_id: WhatsApp ID of the contact (used to look up the person in CRM).
    """
    try:
        person_email = f"{wa_id}@whatsapp.sofopolis.net"
        async with httpx.AsyncClient(timeout=15) as client:
            search_resp = await client.get(
                f"{_BASE}/api/v1/contacts/persons/search",
                params={"search": person_email, "searchFields": "emails:like"},
                headers=_HEADERS,
            )
            search_resp.raise_for_status()
            persons = search_resp.json().get("data", [])

            if not persons:
                logger.warning("insurance_person_not_found", wa_id=wa_id)
                return json.dumps({"verified": False, "reason": "person not found in CRM"})

            person_id = persons[0]["id"]

            verify_resp = await client.post(
                f"{_BASE}/api/insurance/verify",
                json={
                    "person_id": person_id,
                    "ci": carnet_identidad,
                    "insurance_type": empresa_seguro,
                },
                headers=_HEADERS,
            )
            verify_resp.raise_for_status()
            result = verify_resp.json()
            logger.info(
                "insurance_verified",
                wa_id=wa_id,
                empresa=empresa_seguro,
                person_id=person_id,
            )
            return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.exception("verify_insurance_failed", wa_id=wa_id, error=str(e))
        return json.dumps({"verified": False, "error": str(e)})
