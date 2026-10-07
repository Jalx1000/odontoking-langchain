#!/usr/bin/env python
"""Live validation gate for the lead `ciudad` custom attribute (Krayin attribute 98).

The agent sends `ciudad` as the SELECT option id inside the lead pipeline PUT, but the standard
Krayin `PUT /api/v1/leads/{id}` currently drops it (returns 200 without persisting). This script is
the objective "se valida de verdad" check to run AFTER the CRM (sofo-crm) adds the handling:

    uv run python scripts/verify_lead_ciudad.py <lead_id> [ciudad]

Uses the ODONTOKING_API_URL / ODONTOKING_API_TOKEN of the loaded env (default: development, which
locally points at the same prod CRM). It PUTs the ciudad option id, re-reads the lead, and asserts
the value persisted. Exit 0 = PASS. Run it against a throwaway/test lead — it mutates pipeline+ciudad.
"""

import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.langgraph.tools.crm import (  # noqa: E402
    _resolve_ciudad_option_id,
    _resolve_pipeline_id,
)

# Quiet the httpx/httpcore wire logging so the PASS/FAIL line stands alone.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# city label (as the client would say it) → the select option id of attribute 98 (entity=leads)
_CITY_OPTION_LABELS = {
    40: "Santa Cruz", 41: "La Paz", 42: "Cochabamba",
    44: "Sucre", 45: "Oruro", 46: "Potosí",
}


def _headers() -> dict[str, str]:
    return {
        "accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.ODONTOKING_API_TOKEN}",
    }


def _lead_ciudad(data: dict) -> object:
    """Read the lead's ciudad attribute in whatever shape the CRM serializes it."""
    if "ciudad" in data:
        return data["ciudad"]
    attrs = data.get("custom_attributes") or data.get("attribute_values") or {}
    if isinstance(attrs, dict):
        return attrs.get("ciudad")
    if isinstance(attrs, list):
        for a in attrs:
            if isinstance(a, dict) and (a.get("code") == "ciudad" or a.get("attribute_code") == "ciudad"):
                return a.get("value") or a.get("text_value") or a.get("option_id")
    return None


def main() -> int:
    """PUT the ciudad option id on the lead and assert it persisted. Returns a shell exit code."""
    if len(sys.argv) < 2:
        print("usage: verify_lead_ciudad.py <lead_id> [ciudad]", file=sys.stderr)
        return 2
    lead_id = int(sys.argv[1])
    ciudad = sys.argv[2] if len(sys.argv) > 2 else "Cochabamba"

    option_id = _resolve_ciudad_option_id(ciudad)
    if option_id is None:
        print(f"FAIL: '{ciudad}' no mapea a una opción del atributo ciudad.", file=sys.stderr)
        return 2
    pipeline_id, _ = _resolve_pipeline_id(ciudad)
    base = settings.ODONTOKING_API_URL

    with httpx.Client(timeout=30, headers=_headers()) as c:
        before = c.get(f"{base}/api/v1/leads/{lead_id}").json().get("data", {})
        stage_id = before.get("lead_pipeline_stage_id")
        body = {"lead_pipeline_id": pipeline_id, "lead_pipeline_stage_id": stage_id, "ciudad": option_id}
        put = c.put(f"{base}/api/v1/leads/{lead_id}", json=body)
        after = c.get(f"{base}/api/v1/leads/{lead_id}").json().get("data", {})

    got = _lead_ciudad(after)
    expected_label = _CITY_OPTION_LABELS.get(option_id, ciudad)
    print(f"lead={lead_id} ciudad='{ciudad}' option_id={option_id} pipeline={pipeline_id}")
    print(f"PUT status: {put.status_code} | {put.json().get('message') if put.headers.get('content-type','').startswith('application/json') else ''}")
    print(f"ciudad después del PUT: {got!r}")

    persisted = str(got) in {str(option_id), expected_label} or (
        isinstance(got, dict) and str(got.get("id")) == str(option_id)
    )
    if persisted:
        print(f"PASS ✅ — la ciudad persiste ({got!r}). El CRM ya valida el atributo.")
        return 0
    print("FAIL ❌ — el PUT devolvió 200 pero la ciudad NO persiste. Falta el fix del CRM "
          "(manejar el atributo `ciudad` en el update/serialización de leads).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
