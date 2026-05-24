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
_DOCTOR_DETAIL_HEADERS = {
    "accept": "application/json",
    "X-CSRF-TOKEN": "",
}
_BASE = settings.ODONTOKING_API_URL


@tool
async def get_services(keyword: str = "") -> str:
    """Get available dental services/products from Odontoking clinic.

    Args:
        keyword: Optional word to filter services by name (e.g. "Limpieza", "Ortodoncia", "Blanqueamiento").
                 Infer it from what the patient needs. Leave empty to get all services.
    """
    # Sanitize keyword: strip whitespace, cap length to avoid abuse
    clean_keyword = keyword.strip()[:100] if isinstance(keyword, str) else ""

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_BASE}/api/v1/products",
                params={"sort": "id", "limit": 200},
                headers=_HEADERS,
            )
            resp.raise_for_status()

            payload = resp.json()
            if not isinstance(payload, dict):
                logger.warning("get_services_unexpected_response_type", type=type(payload).__name__)
                return json.dumps({"data": [], "warning": "unexpected response format"}, ensure_ascii=False)

            raw_data = payload.get("data", [])
            if not isinstance(raw_data, list):
                logger.warning("get_services_data_not_list", type=type(raw_data).__name__)
                return json.dumps({"data": [], "warning": "unexpected data format"}, ensure_ascii=False)

            all_services = [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "duration_minutes": item.get("duration_minutes"),
                }
                for item in raw_data
                if isinstance(item, dict) and "id" in item and "name" in item
            ]

            if clean_keyword:
                keyword_lower = clean_keyword.lower()
                matched = [s for s in all_services if keyword_lower in s["name"].lower()]
                result = matched if matched else all_services
                if not matched:
                    logger.warning("get_services_keyword_no_match_fallback", keyword=clean_keyword, total=len(all_services))
            else:
                result = all_services

            logger.info("get_services_fetched", total=len(all_services), returned=len(result), keyword=clean_keyword)
            return json.dumps({"data": result}, ensure_ascii=False)

    except httpx.TimeoutException:
        logger.exception("get_services_timeout")
        return json.dumps({"error": "service unavailable: request timed out"})
    except httpx.ConnectError:
        logger.exception("get_services_connection_error")
        return json.dumps({"error": "service unavailable: could not connect to API"})
    except httpx.HTTPStatusError as e:
        logger.exception("get_services_http_error", status=e.response.status_code)
        return json.dumps({"error": f"API returned {e.response.status_code}", "status": e.response.status_code})
    except Exception as e:
        logger.exception("get_services_failed", error=str(e))
        return json.dumps({"error": "unexpected error fetching services"})


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


async def _fetch_doctor_detail(client: httpx.AsyncClient, doctor_id: int) -> dict | None:
    """Fetch one doctor's detail payload. Returns None on any error."""
    try:
        resp = await client.get(
            f"{_BASE}/api/doctors/{doctor_id}",
            headers=_DOCTOR_DETAIL_HEADERS,
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict):
            if isinstance(payload.get("data"), dict):
                return payload["data"]
            return payload
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and item.get("id") == doctor_id:
                    return item
        return None
    except Exception:
        return None


@tool
async def get_doctor_schedule(id_doctor: int) -> str:
    """Get real available appointment slots for a doctor.

    Fetches the doctor's detail endpoint and normalizes its availability slots.

    Args:
        id_doctor: The numeric ID of the doctor from get_doctors().
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            doctor = await _fetch_doctor_detail(client, id_doctor)

        if not isinstance(doctor, dict):
            logger.warning("odontoking_doctor_not_found", id_doctor=id_doctor)
            return json.dumps({"error": "doctor not found"}, ensure_ascii=False)

        schedule = doctor.get("schedule", [])
        if not isinstance(schedule, list):
            schedule = []

        all_slots = []
        for day in schedule:
            if not isinstance(day, dict):
                continue
            date = day.get("date")
            slots = day.get("slots", [])
            if not isinstance(slots, list):
                continue
            for slot in slots:
                if not isinstance(slot, dict):
                    continue
                if slot.get("status") not in (None, "available"):
                    continue
                start_time = slot.get("startTime") or slot.get("start_time")
                end_time = slot.get("endTime") or slot.get("end_time")
                if not start_time or not end_time:
                    continue
                all_slots.append(
                    {
                        "date": date,
                        "day_label": _add_day_name(date) if date else None,
                        "start_time": start_time,
                        "end_time": end_time,
                    }
                )

        logger.info(
            "odontoking_doctor_schedule_fetched",
            id_doctor=id_doctor,
            total_slots=len(all_slots),
        )
        return json.dumps(
            {
                "doctor_id": doctor.get("id", id_doctor),
                "name": doctor.get("name"),
                "availability": all_slots,
            },
            ensure_ascii=False,
        )

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
