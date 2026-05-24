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
async def _call_verify(client: httpx.AsyncClient, ci_paciente: str, seguro_paciente: str) -> dict:
    resp = await client.get(
        f"{_BASE}/api/v1/insurance/verify",
        params={"ci_paciente": ci_paciente, "seguro_paciente": seguro_paciente},
        headers=_HEADERS,
    )
    resp.raise_for_status()
    return resp.json()


@tool
async def verify_insurance(ci_paciente: str, seguro_paciente: str) -> str:
    """Verify if a patient has active dental insurance at Odontoking clinic.

    Ask the patient for their cedula and insurance company name before calling this tool.

    Args:
        ci_paciente: Patient's national ID (cedula), digits only (e.g. '0912345678').
        seguro_paciente: Insurance company name (e.g. 'Alianza', 'Nacional Vida', 'Membresía Odontoking').
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            result = await _call_verify(client, ci_paciente, seguro_paciente)
            logger.info("insurance_verified", ci_paciente=ci_paciente, seguro=seguro_paciente)
            return json.dumps(result, ensure_ascii=False)

    except httpx.HTTPStatusError as e:
        logger.exception("verify_insurance_http_error", status=e.response.status_code)
        if e.response.status_code >= 500:
            return json.dumps({
                "has_insurance": False,
                "error": "El servicio de verificación no está disponible en este momento. Inténtelo de nuevo en unos minutos.",
                "retry": True,
            })
        return json.dumps({"has_insurance": False, "error": str(e)})
    except Exception as e:
        logger.exception("verify_insurance_failed", error=str(e))
        return json.dumps({"has_insurance": False, "error": str(e)})
