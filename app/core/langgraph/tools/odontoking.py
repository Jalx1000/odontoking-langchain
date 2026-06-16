"""Odontoking API tools — services, specialties, doctors, schedules, availability."""

import json
from datetime import datetime as _dt

import httpx
from langchain_core.tools import tool
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

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


def _is_retryable_slots_error(exc: BaseException) -> bool:
    """Retry only on 429 (rate limit) and 5xx server errors, not other 4xx."""
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and (exc.response.status_code == 429 or exc.response.status_code >= 500)
    )


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
                params={"page": 1, "limit": 150},
                headers={"accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            filtered = [
                {
                    "id": d["id"],
                    "name": d["name"],
                    "status": d.get("is_active"),
                    "has_availability": d.get("has_availability"),
                    "minimum_patient_age": d.get("age_range_min"),
                    "maximum_patient_age": d.get("age_range_max"),
                    "specialties": [
                        {"id": s["id"], "name": s["name"]}
                        for s in (d.get("specialties") or [])
                    ],
                }
                for d in data
                # Skip doctors with no availability: the API flags them as
                # has_availability=False with an empty availability list. Validate both so a
                # doctor with no real slots is never offered to the patient.
                if d.get("is_active") and d.get("has_availability") and (d.get("availability") or [])
            ]
            logger.info("odontoking_doctors_fetched", count=len(filtered))
            return json.dumps({"data": filtered}, ensure_ascii=False)
    except Exception as e:
        logger.exception("get_doctors_failed", error=str(e))
        return json.dumps({"error": str(e)})


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=3, max=10),
    retry=retry_if_exception(_is_retryable_slots_error),
    reraise=True,
)
async def _fetch_doctor_slots(
    client: httpx.AsyncClient,
    id_doctor: int,
    date: str,
    days: int,
    duration_minutes: int = 60,
) -> httpx.Response:
    """Call the public slots endpoint. Retries on 429/5xx, re-raises on the final attempt."""
    resp = await client.get(
        f"{_BASE}/api/doctors/{id_doctor}/slots",
        params={"date": date, "days": days, "duration_minutes": duration_minutes},
        headers={"accept": "application/json", "X-CSRF-TOKEN": ""},
    )
    resp.raise_for_status()
    return resp


@tool
async def get_doctor_schedule(id_doctor: int, duration_minutes: int = 60, days: int = 7) -> str:
    """Get real available appointment slots for a doctor.

    Args:
        id_doctor: Numeric ID of the doctor from get_doctors().
        duration_minutes: Appointment duration in minutes. Use the value from get_services() for the chosen service. Defaults to 60.
        days: Number of days ahead to query (1–30). Default is 7.
    """
    if id_doctor < 1 or id_doctor > 9999:
        return json.dumps({"error": "invalid_doctor_id", "slots": []}, ensure_ascii=False)
    days = max(1, min(days, 30))
    duration_minutes = max(15, min(duration_minutes, 480))

    today = _dt.now().date().isoformat()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await _fetch_doctor_slots(client, id_doctor, today, days, duration_minutes)

        payload = resp.json()
        # API returns {"schedule": [{"date": "YYYY-MM-DD", "slots": [{start_time, end_time, status}]}]}
        raw_schedule = payload.get("schedule", []) if isinstance(payload, dict) else []

        schedule = [
            {
                "date": day["date"],
                "day_label": _add_day_name(day["date"]),
                "slots": [
                    {"start_time": s["start_time"], "end_time": s["end_time"]}
                    for s in day.get("slots", [])
                    if s.get("status") == "available"
                ],
            }
            for day in raw_schedule
            if isinstance(day, dict) and day.get("date") and day.get("slots")
        ]

        total_slots = sum(len(d["slots"]) for d in schedule)
        logger.info(
            "odontoking_doctor_schedule_fetched",
            id_doctor=id_doctor,
            days=days,
            duration_minutes=duration_minutes,
            total_slots=total_slots,
        )
        return json.dumps(
            {"doctor_id": id_doctor, "schedule": schedule, "days_queried": days},
            ensure_ascii=False,
        )

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status == 404:
            logger.warning("odontoking_doctor_not_found", id_doctor=id_doctor)
            return json.dumps(
                {"error": "doctor_not_found", "message": "Doctor no encontrado en el sistema"},
                ensure_ascii=False,
            )
        if status == 422:
            logger.warning(
                "get_doctor_schedule_invalid_parameters",
                id_doctor=id_doctor,
                days=days,
                duration_minutes=duration_minutes,
            )
            return json.dumps({"error": "invalid_parameters", "slots": []}, ensure_ascii=False)
        logger.exception("get_doctor_schedule_http_error", id_doctor=id_doctor, status=status)
        return json.dumps({"error": f"API returned {status}", "slots": []}, ensure_ascii=False)
    except Exception as e:
        logger.exception("get_doctor_schedule_failed", id_doctor=id_doctor, error=str(e))
        return json.dumps({"error": str(e), "slots": []}, ensure_ascii=False)


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
