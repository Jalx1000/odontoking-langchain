"""Insurance verification tool for Odontoking.

Single source of truth: POST /api/insurance/verify. This endpoint verifies all three
insurers (Alianza, Nacional Vida, Membresía Odontoking) by routing internally on
`insurance_type`; there is NO per-insurer endpoint. It requires the CRM `person_id`
(resolved here from the WhatsApp `wa_id`), the patient's `ci`, and the `insurance_type`.

Coverage is active ONLY when the returned `status` is "VIGENTE". Every other status
(EN_MORA, VENCIDO, NO_REGISTRADO, SIN_SEGURO, INDETERMINADO) means no confirmed coverage —
`has_insurance` is derived strictly from that, so an expired/at-risk policy is never
reported as active.
"""

import json

import httpx
from langchain_core.tools import tool
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.langgraph.tools.crm import find_person_by_wa_id
from app.core.logging import logger

_HEADERS = {
    "accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {settings.ODONTOKING_API_TOKEN}",
}
_BASE = settings.ODONTOKING_API_URL

# The only status that grants active coverage / allows booking.
_ACTIVE_STATUS = "VIGENTE"


def _is_transient(exc: BaseException) -> bool:
    """Retry only on 5xx server errors, not 4xx client errors."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception(_is_transient),
    reraise=True,
)
async def _call_verify(
    client: httpx.AsyncClient, person_id: int, ci_paciente: str, seguro_paciente: str
) -> dict:
    resp = await client.post(
        f"{_BASE}/api/insurance/verify",
        json={"person_id": person_id, "ci": ci_paciente, "insurance_type": seguro_paciente},
        headers=_HEADERS,
    )
    resp.raise_for_status()
    return resp.json()


def _extract_patient_name(data: dict) -> str | None:
    """Pull the patient's name from the insurer payload (field name varies per insurer)."""
    name = data.get("NOMBRE COMPLETO") or data.get("NOMBRE")
    return name.strip() if isinstance(name, str) and name.strip() else None


@tool
async def verify_insurance(wa_id: str, ci_paciente: str, seguro_paciente: str) -> str:
    """Verify a patient's dental insurance coverage at Odontoking.

    Ask the patient for their cédula (CI) and which insurer they have BEFORE calling this.
    Coverage is active ONLY when the response status is "VIGENTE"; any other status means the
    patient cannot be booked under insurance.

    Args:
        wa_id: WhatsApp ID of the patient (e.g. '591XXXXXXXX'). Used to resolve the CRM person_id.
        ci_paciente: Patient's national ID (cédula), digits only (e.g. '10153980').
        seguro_paciente: Insurer name. One of: 'Alianza', 'Nacional Vida', 'Membresía Odontoking'.
    """
    log = logger.bind(wa_id=wa_id, seguro=seguro_paciente)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            person = await find_person_by_wa_id(client, wa_id)
            person_id = person.get("id") if person else None
            if not person_id:
                log.warning("verify_insurance_person_not_found")
                return json.dumps({
                    "has_insurance": False,
                    "status": "INDETERMINADO",
                    "error": "No se pudo resolver el paciente en el CRM para verificar el seguro.",
                }, ensure_ascii=False)

            result = await _call_verify(client, person_id, ci_paciente, seguro_paciente)
            status = (result.get("status") or "").upper()
            has_insurance = status == _ACTIVE_STATUS
            payload = {
                "has_insurance": has_insurance,
                "status": status or "INDETERMINADO",
                "message": result.get("message"),
                "seguro_name": result.get("seguro_name"),
                "patient_name": _extract_patient_name(result.get("data") or {}),
            }
            log.info("insurance_verified", status=payload["status"], has_insurance=has_insurance)
            return json.dumps(payload, ensure_ascii=False)

    except httpx.HTTPStatusError as e:
        log.exception("verify_insurance_http_error", status=e.response.status_code)
        if e.response.status_code >= 500:
            return json.dumps({
                "has_insurance": False,
                "status": "INDETERMINADO",
                "error": "El servicio de verificación no está disponible en este momento. Inténtelo de nuevo en unos minutos.",
                "retry": True,
            }, ensure_ascii=False)
        return json.dumps({"has_insurance": False, "status": "INDETERMINADO", "error": str(e)}, ensure_ascii=False)
    except Exception as e:
        log.exception("verify_insurance_failed", error=str(e))
        return json.dumps({"has_insurance": False, "status": "INDETERMINADO", "error": str(e)}, ensure_ascii=False)
