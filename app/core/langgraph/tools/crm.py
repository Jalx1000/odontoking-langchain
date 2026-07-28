"""IMPRIMIR CRM tool — Krayin contacts, leads, products, activities and tags.

Flow: WhatsApp → Krayin CRM → agent. The agent receives conversation_id + wa_id, reads
context and replies through the CrmGateway (reply.url), NOT through a tool here. These tools
only perform the CRM writes for a B2B quotation ("cotización"):

    resolve_person → ensure_organization → create_lead → add_lead_product(×n)
    → add_lead_note → tag_lead(temperatura) → [postventa: set_lead_stage]

Saving is incremental: create the lead early (create_lead returns lead_id) and enrich it as the
data arrives. Every new quotation is a NEW lead. The agent keeps lead_id in its conversation
state and passes it back to the enrichment tools, which also makes the writes idempotent against
Meta/CRM webhook retries.

Reuses the ODONTOKING_API_URL / ODONTOKING_API_TOKEN settings — those env vars point at the
IMPRIMIR Krayin instance (https://imprimir.sofopolis.com), authenticated with a sanctum_admin
Bearer token.
"""

import json
from typing import Any, Optional

import httpx
from langchain_core.tools import tool
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import logger

_HEADERS = {
    "accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {settings.ODONTOKING_API_TOKEN}",
}
_BASE = settings.ODONTOKING_API_URL

# ── Fixed Krayin configuration (see spec §4) ──────────────────────────────────
_SOURCE_WHATSAPP = 6          # lead_source_id "WhatsApp"
_LEAD_TYPE_VENTA = 1          # New Business
_LEAD_TYPE_POSTVENTA = 2      # Existing Business
_OWNER_USER_ID = 1            # default lead owner
# Initial stage of the postventa/legacy pipeline (pipeline 3). Confirm the real stage id in
# Krayin (Settings → Pipelines) and fill it here. While None, a postventa lead stays in the
# default pipeline until a human moves it — create_lead never guesses a stage id.
_POSTVENTA_STAGE_ID: Optional[int] = None

# Lead temperature → tag id (internal only; never shown to the client).
_TAG_IDS = {"caliente": 1, "tibio": 2, "frio": 3}
# Aliases the LLM might pass, normalized to the keys above.
_TEMP_ALIASES = {
    "🔥": "caliente", "caliente": "caliente",
    "🌤️": "tibio", "☁️": "tibio", "tibio": "tibio", "cálido": "tibio", "calido": "tibio",
    "❄️": "frio", "frío": "frio", "frio": "frio",
}

# Static catalog → product_id map (spec §4.3). Used as a fallback when the live product search
# does not resolve a product yet.
_PRODUCT_IDS = {
    "bolsa pouch": 1,
    "bolsa flow pack": 2,
    "bolsa sachet": 3,
    "bolsa almohada": 4,
    "bolsa wicket": 5,
    "bolsa sello lateral": 6,
    "etiqueta sleeve": 7,
    "etiqueta roll feed": 8,
    "tapa plástica 1881 short finish": 9,
}

_PLACEHOLDER_NAME = "Cliente WhatsApp"


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _is_transient(exc: BaseException) -> bool:
    """Retry on transient failures only: 429, 5xx, and network timeouts/connection errors."""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and (exc.response.status_code == 429 or exc.response.status_code >= 500)
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception(_is_transient),
    reraise=True,
)
async def _request(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Call the Krayin API with the shared headers, retrying only on transient errors.

    Non-transient responses (4xx such as 422 validation) are raised immediately so the calling
    tool can inspect the body; transient ones (429/5xx/timeout) are retried with backoff.
    """
    resp = await client.request(method, f"{_BASE}{path}", headers=_HEADERS, **kwargs)
    resp.raise_for_status()
    return resp


def _normalize_wa_id(wa_id: str) -> str:
    """Digits-only WhatsApp id — strip '+', spaces so the CRM lookup never misses."""
    return (wa_id or "").replace("+", "").replace(" ", "").strip()


def _clean_name(name: Optional[str]) -> str:
    """Return a real name or the placeholder used until the client tells us who they are."""
    clean = name.strip() if isinstance(name, str) else ""
    return clean or _PLACEHOLDER_NAME


def _real_name_or_none(name: Optional[str]) -> Optional[str]:
    """Return the name only if it is real — not empty and not the placeholder."""
    clean = name.strip() if isinstance(name, str) else ""
    return clean if clean and clean != _PLACEHOLDER_NAME else None


def _data(resp: httpx.Response) -> Any:
    """Return the 'data' field of a Krayin JSON response (dict or list), else the raw json."""
    payload = resp.json()
    return payload.get("data", payload) if isinstance(payload, dict) else payload


async def find_person_by_wa_id(client: httpx.AsyncClient, wa_id: str) -> Optional[dict[str, Any]]:
    """Look up a Krayin person by WhatsApp number (contact_numbers LIKE). Returns dict or None."""
    resp = await _request(
        client,
        "GET",
        "/api/v1/contacts/persons/search",
        params={"search": _normalize_wa_id(wa_id), "searchFields": "contact_numbers:like;"},
    )
    data = _data(resp)
    return data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None


async def _resolve_product_id(client: httpx.AsyncClient, name: str) -> Optional[int]:
    """Resolve a product_id by exact catalog name: live search first, static map as fallback."""
    clean = (name or "").strip()
    if not clean:
        return None
    try:
        resp = await _request(
            client,
            "GET",
            "/api/v1/products/search",
            params={"search": f"name:{clean};", "searchFields": "name:like;"},
        )
        data = _data(resp)
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and str(item.get("name", "")).strip().lower() == clean.lower():
                return item.get("id")
    except Exception as e:  # noqa: BLE001 — search is best-effort; fall back to the static map
        logger.warning("imprimir_product_search_failed", producto=clean[:60], error=str(e))
    return _PRODUCT_IDS.get(clean.lower())


# ── CRM write tools (one Krayin call each) ────────────────────────────────────

@tool
async def resolve_person(wa_id: str, nombre: Optional[str] = None) -> str:
    """Buscar o crear el contacto (Person) del cliente a partir de su número de WhatsApp.

    Llama a esto al inicio para obtener el person_id. Si el contacto ya existe se reutiliza; si no,
    se crea con el nombre indicado (o "Cliente WhatsApp" si aún no lo conoces).

    Args:
        wa_id: Número de WhatsApp del cliente (p. ej. '591XXXXXXXX').
        nombre: Nombre real del contacto si ya lo conoces. Omítelo para usar el placeholder.

    Devuelve {"person_id", "nombre_registrado" (null si es placeholder), "created"}.
    """
    wa = _normalize_wa_id(wa_id)
    log = logger.bind(wa_id=wa)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            person = await find_person_by_wa_id(client, wa)
            if person and person.get("id"):
                log.info("imprimir_person_found", person_id=person["id"])
                return json.dumps(
                    {
                        "person_id": person["id"],
                        "nombre_registrado": _real_name_or_none(person.get("name")),
                        "created": False,
                    },
                    ensure_ascii=False,
                )

            payload = {
                "name": _clean_name(nombre),
                "contact_numbers": [{"value": wa, "label": "work"}],
                "entity_type": "persons",
            }
            resp = await _request(client, "POST", "/api/v1/contacts/persons", json=payload)
            data = _data(resp)
            person_id = data.get("id") if isinstance(data, dict) else None
            log.info("imprimir_person_created", person_id=person_id)
            return json.dumps(
                {"person_id": person_id, "nombre_registrado": _real_name_or_none(nombre), "created": True},
                ensure_ascii=False,
            )
    except Exception as e:
        log.exception("resolve_person_failed", error=str(e))
        return json.dumps({"person_id": None, "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def ensure_organization(person_id: int, nombre_empresa: str) -> str:
    """Crear la empresa (Organization) del cliente y vincularla a su contacto.

    Args:
        person_id: id del contacto obtenido de resolve_person.
        nombre_empresa: Nombre de la empresa del cliente.

    Devuelve {"organization_id", "linked"}.
    """
    empresa = (nombre_empresa or "").strip()
    log = logger.bind(person_id=person_id)
    if not empresa:
        return json.dumps({"organization_id": None, "error": "missing_organization_name"}, ensure_ascii=False)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await _request(client, "POST", "/api/v1/contacts/organizations", json={"name": empresa})
            org = _data(resp)
            org_id = org.get("id") if isinstance(org, dict) else None
            log.info("imprimir_organization_created", organization_id=org_id, empresa=empresa[:60])

            linked = False
            if org_id:
                try:
                    # Krayin's person PUT validates name/entity_type, so rebuild the payload from the
                    # existing record and merge the organization_id rather than PUTting a bare field.
                    person = await _request(client, "GET", f"/api/v1/contacts/persons/{person_id}")
                    pdata = _data(person)
                    pdata = pdata if isinstance(pdata, dict) else {}
                    put_payload = {
                        "name": _clean_name(pdata.get("name")),
                        "contact_numbers": pdata.get("contact_numbers") or [],
                        "organization_id": org_id,
                        "entity_type": "persons",
                    }
                    await _request(client, "PUT", f"/api/v1/contacts/persons/{person_id}", json=put_payload)
                    linked = True
                    log.info("imprimir_organization_linked", organization_id=org_id)
                except Exception as link_err:  # noqa: BLE001 — org exists even if the link failed
                    log.warning("imprimir_organization_link_failed", organization_id=org_id, error=str(link_err))

            return json.dumps({"organization_id": org_id, "linked": linked}, ensure_ascii=False)
    except Exception as e:
        log.exception("ensure_organization_failed", error=str(e))
        return json.dumps({"organization_id": None, "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def create_lead(
    wa_id: str,
    nombre: Optional[str] = None,
    nombre_empresa: Optional[str] = None,
    organization_id: Optional[int] = None,
    categoria: Optional[str] = None,
    resumen: Optional[str] = None,
    es_postventa: bool = False,
) -> str:
    """Crear la cotización como Lead en el CRM (paso 1 del guardado incremental).

    Crea el lead apenas haya intención + producto y devuelve su lead_id (el número de referencia
    "Solicitud #<lead_id>"). Enriquece ese MISMO lead con add_lead_product / add_lead_note /
    tag_lead. IDEMPOTENCIA: si ya tienes un lead_id de esta cotización, NO vuelvas a llamar aquí.

    Args:
        wa_id: Número de WhatsApp del cliente.
        nombre: Nombre del contacto.
        nombre_empresa: Empresa del cliente (para el título del lead).
        organization_id: id de empresa de ensure_organization, si existe.
        categoria: Categoría del catálogo (p. ej. "Envases Flexibles").
        resumen: Resumen breve de la solicitud.
        es_postventa: True si es un cliente existente ("Soy cliente"); usa lead_type postventa.

    Devuelve {"lead_id"}.
    """
    wa = _normalize_wa_id(wa_id)
    log = logger.bind(wa_id=wa)
    etiqueta = (nombre_empresa or nombre or "Cliente").strip()
    descripcion = " — ".join(p for p in (categoria, resumen) if p and p.strip()) or "Cotización vía WhatsApp"
    body: dict[str, Any] = {
        "title": f"Cotización WhatsApp - {etiqueta}",
        "description": descripcion,
        "lead_value": "0",
        "lead_source_id": _SOURCE_WHATSAPP,
        "lead_type_id": _LEAD_TYPE_POSTVENTA if es_postventa else _LEAD_TYPE_VENTA,
        "user_id": _OWNER_USER_ID,
        "person": {
            "name": _clean_name(nombre),
            "contact_numbers": [{"value": wa, "label": "work"}],
            "organization_id": organization_id,
        },
        "entity_type": "leads",
    }
    # Venta: omit the stage so the lead lands in the default pipeline's initial stage. Postventa:
    # only set it when the id is known (see _POSTVENTA_STAGE_ID), otherwise leave it for a human.
    if es_postventa and _POSTVENTA_STAGE_ID is not None:
        body["lead_pipeline_stage_id"] = _POSTVENTA_STAGE_ID
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await _request(client, "POST", "/api/v1/leads", json=body)
            data = _data(resp)
            lead_id = data.get("id") if isinstance(data, dict) else None
            log.info("imprimir_lead_created", lead_id=lead_id, es_postventa=es_postventa)
            return json.dumps({"lead_id": lead_id, "es_postventa": es_postventa}, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        body_text = e.response.text[:1000] if e.response is not None else ""
        log.exception("create_lead_http_error", status=e.response.status_code, body=body_text)
        return json.dumps({"lead_id": None, "error": f"api_{e.response.status_code}"}, ensure_ascii=False)
    except Exception as e:
        log.exception("create_lead_failed", error=str(e))
        return json.dumps({"lead_id": None, "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def add_lead_product(lead_id: int, producto: str, cantidad: int = 1) -> str:
    """Adjuntar un producto del catálogo al lead (paso 2). Llámalo una vez por producto.

    Args:
        lead_id: id del lead devuelto por create_lead.
        producto: Nombre EXACTO del producto del catálogo (p. ej. "Bolsa Pouch").
        cantidad: Cantidad solicitada.

    Si el producto aún no existe en el CRM, no se adjunta y debe quedar en add_lead_note.
    Devuelve {"attached", "product_id"}.
    """
    log = logger.bind(lead_id=lead_id)
    nombre = (producto or "").strip()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            product_id = await _resolve_product_id(client, nombre)
            if not product_id:
                log.info("imprimir_product_unresolved", producto=nombre[:60])
                return json.dumps(
                    {"attached": False, "product_id": None, "reason": "product_not_found_use_note"},
                    ensure_ascii=False,
                )
            body = {
                "product_id": product_id,
                "name": nombre,
                "price": 0,
                "quantity": cantidad,
                "amount": 0,
                "is_new": False,
                "id": None,
            }
            await _request(client, "PUT", f"/api/v1/leads/product/{lead_id}", json=body)
            log.info("imprimir_product_attached", product_id=product_id, cantidad=cantidad)
            return json.dumps({"attached": True, "product_id": product_id}, ensure_ascii=False)
    except Exception as e:
        log.exception("add_lead_product_failed", error=str(e))
        return json.dumps({"attached": False, "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def add_lead_note(
    lead_id: int,
    producto: Optional[str] = None,
    medida: Optional[str] = None,
    impresion: Optional[str] = None,
    cantidad: Optional[str] = None,
    plazo: Optional[str] = None,
    adjunto: Optional[str] = None,
    detalle: Optional[str] = None,
) -> str:
    """Guardar las especificaciones de la cotización como nota en el lead (paso 3).

    Args:
        lead_id: id del lead de create_lead.
        producto: Producto solicitado.
        medida: Medida / dimensiones.
        impresion: Detalle de impresión o arte.
        cantidad: Cantidad.
        plazo: Plazo (Urgente / Este mes / Solo cotizando).
        adjunto: "sí"/"no" — indica si el cliente ya envió un adjunto (queda en la conversación).
        detalle: Cualquier detalle adicional en texto libre.

    Devuelve {"success"}.
    """
    log = logger.bind(lead_id=lead_id)
    fields = [
        ("Producto", producto),
        ("Medida", medida),
        ("Impresión/arte", impresion),
        ("Cantidad", cantidad),
        ("Plazo", plazo),
        ("Adjunto", adjunto),
    ]
    lines = [f"{label}: {value}" for label, value in fields if value and str(value).strip()]
    if detalle and detalle.strip():
        lines.append(detalle.strip())
    if not lines:
        return json.dumps({"success": False, "error": "empty_note"}, ensure_ascii=False)
    body = {
        "lead_id": lead_id,
        "type": "note",
        "title": "Datos de cotización",
        "comment": "\n".join(lines),
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            await _request(client, "POST", "/api/v1/activities", json=body)
            log.info("imprimir_lead_note_added", fields=[label for label, value in fields if value])
            return json.dumps({"success": True}, ensure_ascii=False)
    except Exception as e:
        log.exception("add_lead_note_failed", error=str(e))
        return json.dumps({"success": False, "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def tag_lead(lead_id: int, temperatura: str) -> str:
    """Etiquetar la temperatura (interna) del lead. NUNCA se muestra al cliente.

    Temperatura: 🔥 "caliente" (cantidad relevante + plazo Urgente/Este mes), 🌤️ "tibio"
    (producto y cantidad con plazo flexible), ❄️ "frio" ("solo cotizando"/sin datos).

    Args:
        lead_id: id del lead de create_lead.
        temperatura: "caliente", "tibio" o "frio".

    Devuelve {"success", "tag_id"}.
    """
    log = logger.bind(lead_id=lead_id)
    key = _TEMP_ALIASES.get(str(temperatura).strip().lower())
    tag_id = _TAG_IDS.get(key) if key else None
    if not tag_id:
        return json.dumps({"success": False, "error": "unknown_temperatura"}, ensure_ascii=False)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            await _request(client, "POST", f"/api/v1/leads/{lead_id}/tags", json={"tag_id": tag_id})
            log.info("imprimir_lead_tagged", tag_id=tag_id, temperatura=key)
            return json.dumps({"success": True, "tag_id": tag_id}, ensure_ascii=False)
    except Exception as e:
        log.exception("tag_lead_failed", error=str(e))
        return json.dumps({"success": False, "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def set_lead_stage(lead_id: int, stage_id: int) -> str:
    """Mover el lead a otra etapa del embudo (opcional; p. ej. postventa a pipeline 3).

    Args:
        lead_id: id del lead.
        stage_id: id de la etapa destino en Krayin.

    Devuelve {"success", "stage_id"}.
    """
    log = logger.bind(lead_id=lead_id)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            await _request(
                client,
                "PUT",
                f"/api/v1/leads/stage/edit/{lead_id}",
                json={"lead_pipeline_stage_id": stage_id},
            )
            log.info("imprimir_lead_stage_set", stage_id=stage_id)
            return json.dumps({"success": True, "stage_id": stage_id}, ensure_ascii=False)
    except Exception as e:
        log.exception("set_lead_stage_failed", error=str(e))
        return json.dumps({"success": False, "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def get_person_leads(wa_id: str) -> str:
    """Listar las cotizaciones (leads) que ya tiene este contacto.

    Úsalo para no duplicar y para responder "¿en qué va mi cotización?".

    Args:
        wa_id: Número de WhatsApp del cliente.

    Devuelve {"leads": [{lead_id, title, etapa, productos}]}.
    """
    wa = _normalize_wa_id(wa_id)
    log = logger.bind(wa_id=wa)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            person = await find_person_by_wa_id(client, wa)
            if not person or not person.get("id"):
                log.info("get_person_leads_person_not_found")
                return json.dumps({"leads": [], "message": "contact_not_found"}, ensure_ascii=False)

            person_id = int(person["id"])
            resp = await _request(
                client,
                "GET",
                "/api/v1/leads/search",
                params={"search": str(person_id), "searchFields": "person_id:=;", "limit": 50},
            )
            data = _data(resp)
            leads = []
            for ld in data if isinstance(data, list) else []:
                if not isinstance(ld, dict):
                    continue
                if (ld.get("person") or {}).get("id") not in (person_id, str(person_id)):
                    continue
                products = ld.get("products")
                values = products.values() if isinstance(products, dict) else (products or [])
                nombres = [v.get("name") for v in values if isinstance(v, dict) and v.get("name")]
                leads.append(
                    {
                        "lead_id": ld.get("id"),
                        "title": ld.get("title"),
                        "etapa": (ld.get("lead_pipeline_stage") or {}).get("name"),
                        "productos": nombres,
                    }
                )
            log.info("get_person_leads_fetched", person_id=person_id, count=len(leads))
            return json.dumps({"leads": leads}, ensure_ascii=False)
    except Exception as e:
        log.exception("get_person_leads_failed", error=str(e))
        return json.dumps({"leads": [], "error": str(e) or type(e).__name__}, ensure_ascii=False)
