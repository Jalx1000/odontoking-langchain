"""Odontoking API tools — services, specialties, doctors, schedules, availability."""

import json
from datetime import datetime as _dt

import httpx
from langchain_core.tools import tool

from app.core.config import settings
from app.core.logging import logger

_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _add_day_name(date_str: str) -> str:
    """Return 'viernes 22/05' style label from a YYYY-MM-DD string."""
    try:
        d = _dt.strptime(date_str, "%Y-%m-%d")
        return f"{_DIAS_ES[d.weekday()]} {d.day:02d}/{d.month:02d}"
    except Exception:
        return date_str

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
                        {
                            "date": slot["date"],
                            "day_label": _add_day_name(slot["date"]),
                            "start_time": slot["start_time"],
                        }
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


@tool
async def get_horarios(doctor_id: int | None = None) -> str:
    """Get the base weekly schedule (in text format) for one or all doctors.

    Use this to show a doctor's general working hours (e.g., Mon-Fri 09:00-17:00).
    For real-time available slots on a specific date, use get_disponibilidad instead.

    Args:
        doctor_id: Optional doctor ID to filter results. Returns all doctors if omitted.
    """
    try:
        params: dict = {}
        if doctor_id is not None:
            params["doctorId"] = doctor_id
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_BASE}/api/horarios",
                params=params,
                headers=_HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info("odontoking_horarios_fetched", doctor_id=doctor_id)
            return json.dumps(data, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        logger.exception("get_horarios_http_error", status=e.response.status_code, error=str(e))
        return json.dumps({"error": f"API returned {e.response.status_code}"})
    except Exception as e:
        logger.exception("get_horarios_failed", error=str(e))
        return json.dumps({"error": str(e)})


@tool
async def get_disponibilidad(doctor_id: int, date: str) -> str:
    """Get real-time available appointment slots for a doctor on a specific date.

    Returns a list of available time blocks (startTime, endTime) that can be offered
    to the patient. Always call this before confirming an appointment to ensure the
    slot is actually free.

    Args:
        doctor_id: The numeric ID of the doctor from get_doctors().
        date: The date to check in YYYY-MM-DD format (e.g., '2026-05-15').
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_BASE}/api/disponibilidad",
                params={"doctorId": doctor_id, "date": date},
                headers=_HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info("odontoking_disponibilidad_fetched", doctor_id=doctor_id, date=date)
            return json.dumps(data, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        logger.exception(
            "get_disponibilidad_http_error",
            status=e.response.status_code,
            doctor_id=doctor_id,
            date=date,
            error=str(e),
        )
        return json.dumps({"error": f"API returned {e.response.status_code}"})
    except Exception as e:
        logger.exception("get_disponibilidad_failed", doctor_id=doctor_id, date=date, error=str(e))
        return json.dumps({"error": str(e)})
