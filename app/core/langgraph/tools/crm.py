"""IMPRIMIR CRM tool — Krayin contacts, leads, products, activities and tags.

Flow: WhatsApp → Krayin CRM → agent → CrmGateway. The agent receives conversation_id + wa_id,
drives the quotation, and replies in plain text (the gateway sends it).

Design note — ONE coarse tool per action:
The naive design (resolve_person → ensure_organization → create_lead → add_lead_product →
add_lead_note → tag_lead as six separate LLM tools) forces the model to thread person_id / lead_id
between calls. Weaker models call them in parallel in a single turn and hallucinate those ids
(e.g. person_id=0). So the LLM-facing surface is just:

    register_cotizacion(...)          — ventas: does the whole resolve→org→lead→product→note→tag
    registrar_consulta_postventa(...) — postventa: creates a lead in the postventa pipeline
    get_person_leads(wa_id)           — what this contact already has

Each takes only wa_id (always in context) plus plain fields — no ids to thread. The step functions
below are internal helpers, not tools.

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
# Initial stage of the postventa/legacy pipeline (pipeline 3). Confirm the real stage id in Krayin
# and fill it here; while None, a postventa lead stays in the default pipeline until a human moves it.
_POSTVENTA_STAGE_ID: Optional[int] = None

# Lead temperature → tag id (internal only; never shown to the client).
_TAG_IDS = {"caliente": 1, "tibio": 2, "frio": 3}
_TEMP_ALIASES = {
    "🔥": "caliente", "caliente": "caliente",
    "🌤️": "tibio", "☁️": "tibio", "tibio": "tibio", "cálido": "tibio", "calido": "tibio",
    "❄️": "frio", "frío": "frio", "frio": "frio",
}

# Static catalog → product_id map (spec §4.3): fallback when the live product search misses.
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


# ── Low-level HTTP ────────────────────────────────────────────────────────────

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
    """Call the Krayin API with shared headers, retrying only on transient errors."""
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


# ── Internal step functions (share one httpx client; NOT exposed as tools) ─────

async def _resolve_person(client: httpx.AsyncClient, wa_id: str, nombre: Optional[str]) -> Optional[int]:
    """Find or create the person; return person_id."""
    person = await find_person_by_wa_id(client, wa_id)
    if person and person.get("id"):
        return person["id"]
    payload = {
        "name": _clean_name(nombre),
        "contact_numbers": [{"value": wa_id, "label": "work"}],
        "entity_type": "persons",
    }
    resp = await _request(client, "POST", "/api/v1/contacts/persons", json=payload)
    data = _data(resp)
    return data.get("id") if isinstance(data, dict) else None


async def _find_organization_by_name(client: httpx.AsyncClient, name: str) -> Optional[int]:
    """Return the id of an organization whose name matches exactly, or None. Best-effort."""
    try:
        resp = await _request(
            client,
            "GET",
            "/api/v1/contacts/organizations/search",
            params={"search": name, "searchFields": "name:like;"},
        )
        data = _data(resp)
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and str(item.get("name", "")).strip().lower() == name.strip().lower():
                return item.get("id")
    except Exception as e:  # noqa: BLE001 — search is best-effort
        logger.warning("imprimir_org_search_failed", empresa=name[:60], error=str(e))
    return None


async def _ensure_organization(
    client: httpx.AsyncClient, person_id: Optional[int], nombre_empresa: str
) -> Optional[int]:
    """Reuse an existing organization by name (or create it) and link it to the person.

    Fully best-effort: the organization is optional enrichment, so any failure here returns None
    (or an already-resolved id) and NEVER aborts the lead creation. Krayin rejects a duplicate
    organization name with a 422 ("name has already been taken"), so we look it up first and only
    create when missing; a create conflict falls back to the lookup.
    """
    empresa = (nombre_empresa or "").strip()
    if not empresa:
        return None
    org_id = await _find_organization_by_name(client, empresa)
    if org_id is None:
        try:
            resp = await _request(client, "POST", "/api/v1/contacts/organizations", json={"name": empresa})
            org = _data(resp)
            org_id = org.get("id") if isinstance(org, dict) else None
        except httpx.HTTPStatusError as e:
            # 409/422 usually means the name was taken between our lookup and create — resolve it.
            logger.warning("imprimir_org_create_conflict", empresa=empresa[:60], status=e.response.status_code)
            org_id = await _find_organization_by_name(client, empresa)
        except Exception as e:  # noqa: BLE001
            logger.warning("imprimir_org_create_failed", empresa=empresa[:60], error=str(e))
            return None
    if org_id and person_id:
        try:
            # Krayin's person PUT validates name/entity_type, so rebuild the payload from the record.
            person = await _request(client, "GET", f"/api/v1/contacts/persons/{person_id}")
            pdata = _data(person)
            pdata = pdata if isinstance(pdata, dict) else {}
            await _request(
                client,
                "PUT",
                f"/api/v1/contacts/persons/{person_id}",
                json={
                    "name": _clean_name(pdata.get("name")),
                    "contact_numbers": pdata.get("contact_numbers") or [],
                    "organization_id": org_id,
                    "entity_type": "persons",
                },
            )
        except Exception as e:  # noqa: BLE001 — org exists even if linking failed
            logger.warning("imprimir_organization_link_failed", organization_id=org_id, error=str(e))
    return org_id


async def _create_lead(
    client: httpx.AsyncClient,
    person_id: Optional[int],
    wa_id: str,
    nombre: Optional[str],
    nombre_empresa: Optional[str],
    organization_id: Optional[int],
    categoria: Optional[str],
    resumen: str,
    es_postventa: bool,
) -> Optional[int]:
    """Create the lead linked to the existing person; return lead_id."""
    etiqueta = (nombre_empresa or nombre or "Cliente").strip()
    prefix = "Postventa WhatsApp" if es_postventa else "Cotización WhatsApp"
    descripcion = " — ".join(p for p in (categoria, resumen) if p and p.strip()) or "Vía WhatsApp"
    # Link the EXISTING person by id. Sending contact_numbers for a number Krayin already has makes
    # the lead-create 422 ("person.contact_numbers.0.value has already been taken"). Only fall back
    # to contact_numbers if we somehow have no person_id.
    person: dict[str, Any] = {"name": _clean_name(nombre)}
    if person_id:
        person["id"] = person_id
    else:
        person["contact_numbers"] = [{"value": wa_id, "label": "work"}]
    if organization_id:
        person["organization_id"] = organization_id
    body: dict[str, Any] = {
        "title": f"{prefix} - {etiqueta}",
        "description": descripcion,
        "lead_value": "0",
        "lead_source_id": _SOURCE_WHATSAPP,
        "lead_type_id": _LEAD_TYPE_POSTVENTA if es_postventa else _LEAD_TYPE_VENTA,
        "user_id": _OWNER_USER_ID,
        "person": person,
        "entity_type": "leads",
    }
    if es_postventa and _POSTVENTA_STAGE_ID is not None:
        body["lead_pipeline_stage_id"] = _POSTVENTA_STAGE_ID
    resp = await _request(client, "POST", "/api/v1/leads", json=body)
    data = _data(resp)
    return data.get("id") if isinstance(data, dict) else None


async def _add_lead_product(
    client: httpx.AsyncClient, lead_id: int, producto: str, cantidad: int
) -> bool:
    """Attach the catalog product to the lead; return True if attached."""
    nombre = (producto or "").strip()
    product_id = await _resolve_product_id(client, nombre)
    if not product_id:
        return False
    await _request(
        client,
        "PUT",
        f"/api/v1/leads/product/{lead_id}",
        json={
            "product_id": product_id,
            "name": nombre,
            "price": 0,
            "quantity": cantidad,
            "amount": 0,
            "is_new": False,
            "id": None,
        },
    )
    return True


async def _add_lead_note(client: httpx.AsyncClient, lead_id: int, title: str, fields: list[tuple[str, Any]]) -> None:
    """Post a note activity on the lead from (label, value) pairs (blank values skipped)."""
    lines = [f"{label}: {value}" for label, value in fields if value not in (None, "", [])]
    if not lines:
        return
    await _request(
        client,
        "POST",
        "/api/v1/activities",
        json={"lead_id": lead_id, "type": "note", "title": title, "comment": "\n".join(lines)},
    )


async def _tag_lead(client: httpx.AsyncClient, lead_id: int, temperatura: str) -> Optional[int]:
    """Attach the temperature tag to the lead; return tag_id or None."""
    key = _TEMP_ALIASES.get(str(temperatura).strip().lower())
    tag_id = _TAG_IDS.get(key) if key else None
    if not tag_id:
        return None
    await _request(client, "POST", f"/api/v1/leads/{lead_id}/tags", json={"tag_id": tag_id})
    return tag_id


# ── LLM-facing tools (one call per action; only wa_id + plain fields) ──────────

@tool
async def register_cotizacion(
    wa_id: str,
    categoria: str,
    producto: str,
    cantidad: int,
    nombre_empresa: Optional[str] = None,
    contacto: Optional[str] = None,
    medida: Optional[str] = None,
    impresion: Optional[str] = None,
    plazo: Optional[str] = None,
    temperatura: str = "tibio",
    adjunto: Optional[str] = None,
    detalle: Optional[str] = None,
    es_postventa: bool = False,
) -> str:
    """Registra la cotización COMPLETA en el CRM en una sola llamada. Llámala UNA vez, solo DESPUÉS
    de que el cliente confirme el resumen con un "sí".

    Hace todo internamente (contacto, empresa, lead, producto, specs y temperatura) — tú solo pasas
    los datos, no manejas ids. Devuelve el número de referencia "Solicitud #<lead_id>".

    Args:
        wa_id: Número de WhatsApp del cliente (está en el contexto).
        categoria: Categoría del catálogo (p. ej. "Envases Flexibles").
        producto: Nombre EXACTO del producto del catálogo (p. ej. "Bolsa Pouch").
        cantidad: Cantidad solicitada (entero).
        nombre_empresa: Empresa del cliente.
        contacto: Nombre de la persona de contacto.
        medida: Medida / capacidad / dimensiones.
        impresion: Detalle de impresión o arte ("sí, con arte" / "no lleva").
        plazo: "Urgente" / "Este mes" / "Solo cotizando".
        temperatura: Temperatura INTERNA del lead: "caliente" | "tibio" | "frio".
        adjunto: "sí" si el cliente ya envió un archivo, si aplica.
        detalle: Cualquier detalle adicional en texto libre.
        es_postventa: True solo si es un cliente existente con una consulta de postventa.

    Devuelve {"lead_id", "solicitud", "temperatura", "producto_adjuntado"}.
    """
    wa = _normalize_wa_id(wa_id)
    log = logger.bind(wa_id=wa)
    resumen = " ".join(str(p) for p in (cantidad, producto) if p)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            person_id = await _resolve_person(client, wa, contacto)
            org_id = await _ensure_organization(client, person_id, nombre_empresa) if nombre_empresa else None
            lead_id = await _create_lead(
                client, person_id, wa, contacto, nombre_empresa, org_id, categoria, resumen, es_postventa
            )
            if not lead_id:
                log.error("register_cotizacion_no_lead_id")
                return json.dumps({"lead_id": None, "error": "no_lead_id"}, ensure_ascii=False)

            # Best-effort enrichment — a failure here must not lose the lead already created.
            attached = False
            try:
                attached = await _add_lead_product(client, lead_id, producto, cantidad)
            except Exception as e:  # noqa: BLE001
                log.warning("register_cotizacion_product_failed", lead_id=lead_id, error=str(e))
            try:
                await _add_lead_note(
                    client,
                    lead_id,
                    "Datos de cotización",
                    [
                        ("Producto", producto if not attached else None),  # producto ya está como line item si se adjuntó
                        ("Categoría", categoria),
                        ("Medida", medida),
                        ("Impresión/arte", impresion),
                        ("Cantidad", cantidad),
                        ("Plazo", plazo),
                        ("Adjunto", adjunto),
                        ("Detalle", detalle),
                    ],
                )
            except Exception as e:  # noqa: BLE001
                log.warning("register_cotizacion_note_failed", lead_id=lead_id, error=str(e))
            tag_id = None
            try:
                tag_id = await _tag_lead(client, lead_id, temperatura)
            except Exception as e:  # noqa: BLE001
                log.warning("register_cotizacion_tag_failed", lead_id=lead_id, error=str(e))

            log.info(
                "imprimir_cotizacion_registered",
                lead_id=lead_id,
                producto=producto[:40],
                cantidad=cantidad,
                producto_adjuntado=attached,
                tag_id=tag_id,
                es_postventa=es_postventa,
            )
            return json.dumps(
                {
                    "lead_id": lead_id,
                    "solicitud": f"#{lead_id}",
                    "temperatura": _TEMP_ALIASES.get(str(temperatura).strip().lower(), "tibio"),
                    "producto_adjuntado": attached,
                },
                ensure_ascii=False,
            )
    except httpx.HTTPStatusError as e:
        body_text = e.response.text[:800] if e.response is not None else ""
        log.exception("register_cotizacion_http_error", status=e.response.status_code, body=body_text)
        return json.dumps({"lead_id": None, "error": f"api_{e.response.status_code}"}, ensure_ascii=False)
    except Exception as e:
        log.exception("register_cotizacion_failed", error=str(e))
        return json.dumps({"lead_id": None, "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def registrar_consulta_postventa(
    wa_id: str,
    detalle: str,
    nombre_empresa: Optional[str] = None,
    contacto: Optional[str] = None,
    nro_pedido: Optional[str] = None,
) -> str:
    """Registra la consulta de un CLIENTE EXISTENTE (postventa) como lead en el pipeline de postventa.

    Úsala en la rama "Soy cliente y tengo una consulta". No mezcla con ventas nuevas. Llámala UNA vez.

    Args:
        wa_id: Número de WhatsApp del cliente.
        detalle: Descripción breve de la consulta del cliente.
        nombre_empresa: Empresa del cliente.
        contacto: Nombre de contacto.
        nro_pedido: Nº de pedido o cotización previa, si lo tiene.

    Devuelve {"lead_id", "solicitud"}.
    """
    wa = _normalize_wa_id(wa_id)
    log = logger.bind(wa_id=wa)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            person_id = await _resolve_person(client, wa, contacto)
            org_id = await _ensure_organization(client, person_id, nombre_empresa) if nombre_empresa else None
            lead_id = await _create_lead(
                client, person_id, wa, contacto, nombre_empresa, org_id, "Postventa", (detalle or "").strip(), True
            )
            if not lead_id:
                return json.dumps({"lead_id": None, "error": "no_lead_id"}, ensure_ascii=False)
            try:
                await _add_lead_note(
                    client,
                    lead_id,
                    "Consulta postventa",
                    [("Nº pedido/cotización", nro_pedido), ("Consulta", detalle)],
                )
            except Exception as e:  # noqa: BLE001
                log.warning("postventa_note_failed", lead_id=lead_id, error=str(e))
            log.info("imprimir_postventa_registered", lead_id=lead_id)
            return json.dumps({"lead_id": lead_id, "solicitud": f"#{lead_id}"}, ensure_ascii=False)
    except Exception as e:
        log.exception("registrar_consulta_postventa_failed", error=str(e))
        return json.dumps({"lead_id": None, "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def get_person_leads(wa_id: str) -> str:
    """Lista las cotizaciones (leads) que ya tiene este contacto.

    Úsala para no duplicar y para responder "¿en qué va mi cotización?".

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
