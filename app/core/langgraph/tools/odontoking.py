"""Odontoking API tools — services, specialties, doctors, schedules."""

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
async def get_services() -> str:
    """Get all available dental services/products from Odontoking clinic."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_BASE}/api/v1/products",
                params={"sort": "id", "limit": 200},
                headers=_HEADERS,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            filtered = [{"id": item["id"], "name": item["name"]} for item in data]
            logger.info("odontoking_services_fetched", count=len(filtered))
            return json.dumps({"data": filtered}, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        logger.exception("get_services_http_error", status=e.response.status_code, error=str(e))
        return json.dumps({"error": f"API returned {e.response.status_code}", "status": e.response.status_code})
    except Exception as e:
        logger.exception("get_services_failed", error=str(e))
        return json.dumps({"error": str(e)})


@tool
async def get_specialties() -> str:
    """Get all dental specialties available at Odontoking clinic."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_BASE}/api/specialties",
                headers={"accept": "application/json"},
            )
            resp.raise_for_status()
            logger.info("odontoking_specialties_fetched")
            return json.dumps(resp.json(), ensure_ascii=False)
    except Exception as e:
        logger.exception("get_specialties_failed", error=str(e))
        return json.dumps({"error": str(e)})


@tool
async def get_doctors() -> str:
    """Get all active doctors with their specialties and weekly availability from Odontoking."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_BASE}/api/doctors",
                params={"page": 1, "limit": 100},
                headers={"accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            filtered = [
                {
                    "id": d["id"],
                    "name": d["name"],
                    "status": d.get("is_active"),
                    "minimum_patient_age": d.get("age_range_min"),
                    "maximum_patient_age": d.get("age_range_max"),
                    "specialties": [
                        {"id": s["id"], "name": s["name"]}
                        for s in (d.get("specialties") or [])
                    ],
                }
                for d in data
            ]
            logger.info("odontoking_doctors_fetched", count=len(filtered))
            return json.dumps({"data": filtered}, ensure_ascii=False)
    except Exception as e:
        logger.exception("get_doctors_failed", error=str(e))
        return json.dumps({"error": str(e)})


@tool
async def get_doctor_schedule(id_doctor: int) -> str:
    """Get the real availability slots for a specific doctor by their ID.

    Args:
        id_doctor: The numeric ID of the doctor from get_doctors().
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_BASE}/api/doctors",
                params={"page": 1, "limit": 100},
                headers={"accept": "application/json"},
            )
            resp.raise_for_status()
            doctors = resp.json().get("data", [])
            matches = [
                {
                    "doctor_id": d["id"],
                    "name": d["name"],
                    "availability": [
                        {"date": slot["date"], "start_time": slot["start_time"]}
                        for slot in (d.get("availability") or [])
                    ],
                }
                for d in doctors
                if d["id"] == id_doctor
            ]
            if not matches:
                return json.dumps({"error": f"doctor with id {id_doctor} not found"})
            logger.info("odontoking_doctor_schedule_fetched", id_doctor=id_doctor)
            return json.dumps(matches[0], ensure_ascii=False)
    except Exception as e:
        logger.exception("get_doctor_schedule_failed", id_doctor=id_doctor, error=str(e))
        return json.dumps({"error": str(e)})
