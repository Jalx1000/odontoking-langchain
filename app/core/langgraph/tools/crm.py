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
from datetime import date, timedelta
from typing import Any, Optional

import httpx
from langchain_core.runnables import RunnableConfig
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
# Initial stage of the sales pipeline. Krayin's REST lead-create REQUIRES lead_pipeline_stage_id —
# omitting it 500s with "Undefined array key lead_pipeline_stage_id". Krayin's default first stage
# is id 1; adjust if the IMPRIMIR sales pipeline's initial stage differs.
_VENTA_STAGE_ID = 1
# Initial stage of the postventa/legacy pipeline (pipeline 3). Confirm the real stage id in Krayin
# and fill it here; while None, postventa leads use the sales initial stage (so the key is present).
_POSTVENTA_STAGE_ID: Optional[int] = None

# Lead temperature → tag id (internal only; never shown to the client).
_TAG_IDS = {"caliente": 1, "tibio": 2, "frio": 3}
_TEMP_ALIASES = {
    "🔥": "caliente", "caliente": "caliente",
    "🌤️": "tibio", "☁️": "tibio", "tibio": "tibio", "cálido": "tibio", "calido": "tibio",
    "❄️": "frio", "frío": "frio", "frio": "frio",
}

# Static catalog → product_id map: fallback when the live product search misses. These are the
# REAL ids from the IMPRIMIR Krayin catalog (verified via GET /api/v1/products) — the live
# products/search is still tried first, this is only the last resort.
_PRODUCT_IDS = {
    "bolsa pouch": 12,
    "bolsa flow pack": 6,
    "bolsa sachet": 8,
    "bolsa almohada": 9,
    "bolsa wicket": 11,
    "bolsa sello lateral": 7,
    "etiqueta sleeve": 15,
    "etiqueta roll feed": 16,
    "tapa plástica 1881 short finish": 14,
}

_PLACEHOLDER_NAME = "Cliente WhatsApp"

# ── City → sales pipeline routing (agent-quotes.md §1) ────────────────────────
# The CRM's default pipeline is Santa Cruz, so EVERY messaging lead is born there regardless of the
# client's city; the agent moves it once the city is confirmed. The ids are NOT correlative (2, 3, 5
# are absent) — never generate or assume them: use this map, or resolve by name via
# GET /api/v1/settings/pipelines.
_CITY_PIPELINE_IDS = {
    "santa cruz": 1,
    "potosi": 4,
    "oruro": 6,
    "la paz": 7,
    "cochabamba": 8,
    "sucre": 9,
}
_PIPELINE_SIN_CIUDAD = 10  # fallback: no city / unrecognised city — keeps Santa Cruz metrics clean

# The CRM auto-creates the lead in this initial stage. A lead in ANY other stage means a human
# advisor already took it, so the agent must NOT move or modify it (see the Q1 "mixta" rule).
_UNATTENDED_STAGE_NAME = "no atendido"

# Free-text city (accents/abbreviations the client may type) → canonical pipeline key.
_CITY_ALIASES = {
    "santa cruz": "santa cruz", "santacruz": "santa cruz", "scz": "santa cruz",
    "potosi": "potosi", "potosí": "potosi",
    "oruro": "oruro",
    "la paz": "la paz", "lapaz": "la paz", "lp": "la paz",
    "cochabamba": "cochabamba", "cbba": "cochabamba", "cocha": "cochabamba",
    "sucre": "sucre", "chuquisaca": "sucre",
}


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


# ── City / lead / quote helpers (agent-quotes.md §1–§2) ───────────────────────

def _ctx_ids(config: Optional[RunnableConfig]) -> tuple[Optional[int], Optional[int]]:
    """Read (lead_id, person_id) injected by the graph via config.metadata.

    These come from the CRM event (contact.lead_id / contact.person_id) and are injected server-side
    so the LLM never sees or passes them — the module deliberately keeps ids off the LLM surface to
    avoid hallucinated ids.
    """
    metadata = (config or {}).get("metadata") or {}
    return metadata.get("lead_id"), metadata.get("person_id")


def _ctx_conversation_id(config: Optional[RunnableConfig]) -> Optional[int]:
    """Read the conversation_id injected via config.metadata (the LLM never passes it)."""
    metadata = (config or {}).get("metadata") or {}
    return metadata.get("conversation_id")


def _ctx_contact_name(config: Optional[RunnableConfig]) -> Optional[str]:
    """Best contact name from metadata (registered name, else WhatsApp/Messenger profile), or None.

    Used as the quote subject when there's no company — an individual client (common on Messenger)
    must not end up with a generic "Cotización WhatsApp" subject.
    """
    metadata = (config or {}).get("metadata") or {}
    name = metadata.get("nombre_registrado") or metadata.get("nombre_whatsapp")
    clean = name.strip() if isinstance(name, str) else ""
    return clean or None


def _phone_ask_allowed(
    phone_required: bool, phone_prompt_state: Optional[str], phone_prompt_exhausted: bool
) -> bool:
    """Hard gate (code-owned, not the model's) for whether the agent may ASK for the phone.

    Asking is allowed only when the CRM flags the phone missing, the 3 attempts are not exhausted, and
    it is not already captured/refused. WHEN to ask within that (the conversation must have qualified)
    and the wording are the model's job. Capturing a number the client volunteers is ALWAYS allowed —
    this gates asking only. Absent/false `phone_required` reads as not-required (§4 compatibility), so a
    CRM that hasn't deployed phone-capture never triggers an ask.
    """
    if not phone_required or phone_prompt_exhausted:
        return False
    return (phone_prompt_state or "pending") not in ("captured", "refused")


def _resolve_pipeline_id(ciudad: Optional[str]) -> tuple[int, bool]:
    """Map a free-text city to its sales pipeline id. Returns (pipeline_id, recognised).

    Unrecognised or empty city → pipeline 10 (Sin ciudad). It never falls back to Santa Cruz: an
    unconfirmed city must not inflate the biggest branch's metrics (agent-quotes.md §1).
    """
    key = (ciudad or "").strip().lower()
    canonical = _CITY_ALIASES.get(key)
    if canonical and canonical in _CITY_PIPELINE_IDS:
        return _CITY_PIPELINE_IDS[canonical], True
    return _PIPELINE_SIN_CIUDAD, False


async def _initial_stage_id(client: httpx.AsyncClient, pipeline_id: int) -> Optional[int]:
    """Resolve the initial stage of a pipeline (lowest sort_order).

    Never hardcode stage ids — each pipeline has its own set (agent-quotes.md §1). Returns None if
    the pipeline has no resolvable stages; the caller then omits the stage and lets Krayin default it.
    """
    resp = await _request(client, "GET", f"/api/v1/settings/pipelines/{pipeline_id}")
    data = _data(resp)
    stages = data.get("stages") if isinstance(data, dict) else None
    values = list(stages.values()) if isinstance(stages, dict) else (stages or [])
    stage_list = [s for s in values if isinstance(s, dict) and s.get("id") is not None]
    if not stage_list:
        return None
    first = min(stage_list, key=lambda s: s.get("sort_order") or 0)
    return first.get("id")


async def _get_lead(client: httpx.AsyncClient, lead_id: int) -> Optional[dict[str, Any]]:
    """Fetch a lead by id, or None on 404/failure (best-effort)."""
    try:
        resp = await _request(client, "GET", f"/api/v1/leads/{lead_id}")
        data = _data(resp)
        return data if isinstance(data, dict) else None
    except Exception as e:  # noqa: BLE001
        logger.warning("imprimir_lead_fetch_failed", lead_id=lead_id, error=str(e))
        return None


def _lead_stage_name(lead: dict[str, Any]) -> str:
    """Current stage name of a lead, lowercased/stripped ('' when unknown)."""
    stage = lead.get("lead_pipeline_stage") or lead.get("stage") or {}
    name = stage.get("name") if isinstance(stage, dict) else None
    return (name or "").strip().lower()


def _is_unattended(lead: dict[str, Any]) -> bool:
    """True if the lead is still in the initial 'No atendido' stage (safe to move/enrich).

    When the stage is unknown we return True: the fresh auto-created lead is the common case, and
    the CRM PUT is idempotent, so a re-move is harmless — whereas wrongly skipping loses the routing.
    """
    name = _lead_stage_name(lead)
    return name in ("", _UNATTENDED_STAGE_NAME)


def _build_quote_items(items: Any) -> list[dict[str, Any]]:
    """Normalise LLM-provided items into Krayin quote line items with prices at 0.

    Each line needs name, quantity, price, total. Prices are 0 by policy — the agent does not quote
    money; the advisor prices the document later. Entries without a name are skipped. `items` must
    end up non-empty: QuoteRepository::create() does foreach($data['items']) with no guard, so an
    empty/absent items 500s instead of 422 (agent-quotes.md §2).
    """
    out: list[dict[str, Any]] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        try:
            qty = int(it.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1
        sku = str(it.get("sku") or "").strip() or name.upper().replace(" ", "-")[:40]
        out.append({"sku": sku, "name": name, "quantity": max(qty, 1), "price": 0, "total": 0})
    return out


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


async def _build_quote_line_items(
    client: httpx.AsyncClient, items: Any
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build quote line items and VALIDATE each against the catalog (the items ARE the products).

    Every line is resolved to a real catalog product_id (live search + static map). Resolved products
    carry their product_id so the quote line links to the catalog; names that don't match any product
    are still kept (nothing is silently dropped) but returned separately so the caller can surface
    them. Prices/totals stay 0 — the advisor prices the document later.
    """
    line_items = _build_quote_items(items)
    unresolved: list[str] = []
    for line in line_items:
        product_id = await _resolve_product_id(client, line["name"])
        if product_id is None:
            unresolved.append(line["name"])
        else:
            line["product_id"] = product_id
    return line_items, unresolved


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
    """Return the id of an organization whose name matches exactly, or None. Best-effort.

    This Krayin build has NO /organizations/search route (calling it 500s — `/search` is caught by
    `/organizations/{id}` and hits show("search")). The index endpoint instead filters by column,
    so `?name=<value>` does an exact `where name in (<value>)`. That gives us reliable reuse without
    the broken search route.
    """
    clean = (name or "").strip()
    if not clean:
        return None
    try:
        resp = await _request(client, "GET", "/api/v1/contacts/organizations", params={"name": clean})
        data = _data(resp)
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and str(item.get("name", "")).strip().lower() == clean.lower():
                return item.get("id")
    except Exception as e:  # noqa: BLE001 — lookup is best-effort
        logger.warning("imprimir_org_lookup_failed", empresa=clean[:60], error=str(e))
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
    # Always send a stage id — Krayin's REST lead-create 500s ("Undefined array key
    # lead_pipeline_stage_id") when it is omitted. Postventa uses its own stage when known.
    body["lead_pipeline_stage_id"] = (
        _POSTVENTA_STAGE_ID if (es_postventa and _POSTVENTA_STAGE_ID is not None) else _VENTA_STAGE_ID
    )
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
    config: RunnableConfig,
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
    """Enriquece la oportunidad ya abierta por el CRM con los datos de la cotización.

    Reutiliza la oportunidad que el CRM abrió para esta conversación (no crea una nueva) y le agrega
    categoría, producto, specs, plazo y temperatura. Llámala UNA vez, solo DESPUÉS de que el cliente
    confirme el resumen con un "sí". Tú solo pasas los datos, no manejas ids. Devuelve el número de
    referencia "Solicitud #<lead_id>".

    Args:
        wa_id: Número de WhatsApp del cliente (está en el contexto).
        categoria: Categoría del catálogo (p. ej. "Envases Flexibles").
        producto: Nombre EXACTO del producto del catálogo (p. ej. "Bolsa Pouch").
        cantidad: Cantidad solicitada (entero).
        config: Interno; lo inyecta el sistema. No lo pases.
        nombre_empresa: Empresa del cliente.
        contacto: Nombre de la persona de contacto.
        medida: Medida / capacidad / dimensiones.
        impresion: Detalle de impresión o arte ("sí, con arte" / "no lleva").
        plazo: "Urgente" / "Este mes" / "Solo cotizando".
        temperatura: Temperatura INTERNA del lead: "caliente" | "tibio" | "frio".
        adjunto: "sí" si el cliente ya envió un archivo, si aplica.
        detalle: Cualquier detalle adicional en texto libre.
        es_postventa: True solo si es un cliente existente con una consulta de postventa.
    """
    wa = _normalize_wa_id(wa_id)
    lead_ctx, person_ctx = _ctx_ids(config)
    log = logger.bind(wa_id=wa, lead_id=lead_ctx)
    resumen = " ".join(str(p) for p in (cantidad, producto) if p)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Q1 "mixta": the CRM already auto-created ONE lead for this conversation
            # (contact.lead_id). Reuse it — never create a duplicate. Enrich it only while it is
            # still the untouched auto-created lead ("No atendido"); if an advisor already advanced
            # it, leave it exactly as-is.
            if lead_ctx:
                lead = await _get_lead(client, lead_ctx)
                if lead is not None and not _is_unattended(lead):
                    log.info("register_cotizacion_lead_in_progress", stage=_lead_stage_name(lead))
                    return json.dumps(
                        {"lead_id": lead_ctx, "solicitud": f"#{lead_ctx}", "reused": True,
                         "note": "lead_en_proceso"},
                        ensure_ascii=False,
                    )
                lead_id: Optional[int] = lead_ctx
            else:
                # Fallback: no auto-created lead in context (contact.lead_id was null) — create one.
                person_id = person_ctx or await _resolve_person(client, wa, contacto)
                org_id = (
                    await _ensure_organization(client, person_id, nombre_empresa)
                    if nombre_empresa else None
                )
                lead_id = await _create_lead(
                    client, person_id, wa, contacto, nombre_empresa, org_id, categoria, resumen, es_postventa
                )
            if not lead_id:
                log.error("register_cotizacion_no_lead_id")
                return json.dumps({"lead_id": None, "error": "no_lead_id"}, ensure_ascii=False)

            # Best-effort enrichment — a failure here must not lose the lead.
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
        async with httpx.AsyncClient(timeout=15) as client:
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


@tool
async def mover_lead_por_ciudad(ciudad: str, config: RunnableConfig) -> str:
    """Mueve la oportunidad del cliente al pipeline de su CIUDAD.

    Llámala UNA vez, apenas el cliente confirme la ciudad en el saludo, antes de avanzar con la
    consulta. No manejas ids: la oportunidad se toma del contexto; vos solo pasás la ciudad.

    Ciudades válidas: Santa Cruz, Potosí, Oruro, La Paz, Cochabamba, Sucre. Si el cliente no da
    ciudad o dice una que no está en la lista, igual llamá la herramienta con lo que haya dicho: cae
    en "Sin ciudad" automáticamente (no lo dejes en Santa Cruz por omisión).

    Args:
        ciudad: La ciudad que dijo el cliente (texto libre; se normaliza por dentro).
        config: Interno; lo inyecta el sistema. No lo pases.
    """
    lead_id, _ = _ctx_ids(config)
    pipeline_id, recognised = _resolve_pipeline_id(ciudad)
    log = logger.bind(lead_id=lead_id, ciudad=(ciudad or "")[:40])
    if not lead_id:
        log.warning("mover_lead_no_lead_id")
        return json.dumps({"moved": False, "error": "no_lead_id"}, ensure_ascii=False)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            lead = await _get_lead(client, lead_id)
            if lead is None:
                return json.dumps({"moved": False, "error": "lead_not_found"}, ensure_ascii=False)
            # Q1 "mixta" rule: only the untouched auto-created lead ("No atendido") is movable. If an
            # advisor already advanced it, leave it exactly where it is.
            if not _is_unattended(lead):
                log.info("mover_lead_skip_in_progress", stage=_lead_stage_name(lead))
                return json.dumps(
                    {"moved": False, "reason": "lead_en_proceso", "pipeline_id": pipeline_id},
                    ensure_ascii=False,
                )
            stage_id = await _initial_stage_id(client, pipeline_id)
            body: dict[str, Any] = {"lead_pipeline_id": pipeline_id}
            if stage_id is not None:
                body["lead_pipeline_stage_id"] = stage_id
            await _request(client, "PUT", f"/api/v1/leads/{lead_id}", json=body)
            log.info(
                "imprimir_lead_moved",
                pipeline_id=pipeline_id,
                stage_id=stage_id,
                ciudad_reconocida=recognised,
            )
            return json.dumps(
                {"moved": True, "pipeline_id": pipeline_id, "ciudad_reconocida": recognised},
                ensure_ascii=False,
            )
    except httpx.HTTPStatusError as e:
        body_text = e.response.text[:400] if e.response is not None else ""
        log.exception("mover_lead_http_error", status=e.response.status_code, body=body_text)
        return json.dumps({"moved": False, "error": f"api_{e.response.status_code}"}, ensure_ascii=False)
    except Exception as e:
        log.exception("mover_lead_failed", error=str(e))
        return json.dumps({"moved": False, "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def crear_quote(
    nombre_empresa: str,
    items: list[dict],
    config: RunnableConfig,
    descripcion: Optional[str] = None,
) -> str:
    """Crea una cotización formal (quote) en el CRM, ligada a la oportunidad del cliente.

    Una sola llamada, y SOLO después de que el cliente confirme el resumen con un "sí". El ASUNTO de
    la cotización es el nombre de la empresa. Los ítems SON los productos: usá el nombre EXACTO del
    catálogo en cada uno (se validan contra el catálogo). Los PRECIOS van en 0 — el asesor pone los
    precios reales; nunca inventes montos. No manejas ids: person_id y lead_id vienen del contexto.

    Args:
        nombre_empresa: Nombre de la empresa del cliente — va como asunto de la cotización.
        items: Lista de ítems (productos), al menos uno. Cada ítem: {"name": str, "quantity": int}.
            `name` debe ser el nombre EXACTO del producto del catálogo.
        config: Interno; lo inyecta el sistema. No lo pases.
        descripcion: Detalle opcional en texto libre.
    """
    lead_id, person_id = _ctx_ids(config)
    log = logger.bind(lead_id=lead_id, person_id=person_id)
    if not person_id:
        # §4.3: person_id must exist and must never be invented — without it we cannot create.
        log.warning("crear_quote_no_person_id")
        return json.dumps({"quote_id": None, "error": "no_person_id"}, ensure_ascii=False)
    # Subject is the company name; for an individual (no company — common on Messenger) fall back to
    # the contact's name from context, never to a generic "Cotización WhatsApp".
    subject = (nombre_empresa or "").strip() or _ctx_contact_name(config) or "Cotización"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Items ARE the products — validate each against the catalog and attach its product_id.
            line_items, unresolved = await _build_quote_line_items(client, items)
            if not line_items:
                log.warning("crear_quote_no_items")
                return json.dumps({"quote_id": None, "error": "no_items"}, ensure_ascii=False)
            if unresolved:
                log.warning("crear_quote_unresolved_products", productos=unresolved[:10])
            body: dict[str, Any] = {
                "subject": subject,
                "person_id": person_id,
                "user_id": _OWNER_USER_ID,
                # §2: the 7-day expiry lives in the web form, not the API — compute it or the POST 422s.
                "expired_at": (date.today() + timedelta(days=7)).isoformat(),
                "sub_total": 0,
                "grand_total": 0,
                "items": line_items,
            }
            if lead_id:
                # Always link the quote to the lead — it ties the quote to the city pipeline, and the
                # PUT path detaches the lead when lead_id is omitted (§3), so keep it present.
                body["lead_id"] = lead_id
            if descripcion and descripcion.strip():
                body["description"] = descripcion.strip()
            resp = await _request(client, "POST", "/api/v1/quotes", json=body)
            data = _data(resp)
            quote_id = data.get("id") if isinstance(data, dict) else None
            log.info(
                "imprimir_quote_created",
                quote_id=quote_id,
                items=len(line_items),
                productos_no_reconocidos=len(unresolved),
            )
            result: dict[str, Any] = {"quote_id": quote_id, "solicitud": f"#{quote_id}"}
            if unresolved:
                result["productos_no_reconocidos"] = unresolved
            return json.dumps(result, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        body_text = e.response.text[:400] if e.response is not None else ""
        log.exception("crear_quote_http_error", status=e.response.status_code, body=body_text)
        return json.dumps({"quote_id": None, "error": f"api_{e.response.status_code}"}, ensure_ascii=False)
    except Exception as e:
        log.exception("crear_quote_failed", error=str(e))
        return json.dumps({"quote_id": None, "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def derivar_a_asesor(conversation_id: int, reason: str) -> str:
    """Deriva la conversación a un asesor humano del equipo de ventas.

    Úsala cuando el cliente pida explícitamente hablar con una persona/asesor/humano, cuando
    muestre enojo o frustración, o cuando la consulta exceda lo que podés resolver: descuentos por
    volumen, precios especiales, reclamos por un trabajo entregado, pagos, facturación o cambios
    sobre un pedido ya confirmado. NO derives por precios de lista, tiempos de entrega, formatos,
    materiales, horarios ni dirección — eso lo resolvés vos.

    Importante: el mensaje que escribes en ESTA misma respuesta es el aviso que recibe el cliente
    (breve y natural, sin prometer tiempos; ej. "Te comunico con un asesor del equipo, en un
    momento te escriben por acá"). Después de derivar NO vuelvas a escribir en esta conversación:
    la atiende una persona. Si ya derivaste antes en esta conversación, no lo hagas de nuevo.

    Args:
        conversation_id: El conversation_id de esta conversación (está en el contexto).
        reason: Motivo en una frase, en español, para que el asesor entienda el contexto sin leer
            todo el chat. Ej: "Pide cotización de 5000 bolsas pouch con descuento por volumen".
    """
    # Pure signal: the actual POST /handoff is done by the caller AFTER the client notice is sent,
    # because once a conversation is derived the CRM 409s any further /messages.
    return json.dumps({"status": "handoff_signaled", "reason": reason}, ensure_ascii=False)


async def request_handoff(conversation_id: int, reason: str) -> dict:
    """Derive a conversation to a human advisor: POST /whatsapp/conversations/{id}/handoff.

    Not a tool — the gateway calls this AFTER sending the client notice (once derived, the CRM 409s
    any further /messages, so order matters). Idempotent: a second call returns handoff.changed=false.
    Best-effort: logs and returns {} on failure, never raises. Uses the same Bearer token as the
    other CRM calls (verified: the route authenticates and 404s only on an unknown conversation).
    """
    log = logger.bind(conversation_id=conversation_id)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await _request(
                client,
                "POST",
                f"/api/v1/whatsapp/conversations/{conversation_id}/handoff",
                json={"reason": reason},
            )
            payload = resp.json()
            handoff = payload.get("handoff", {}) if isinstance(payload, dict) else {}
            log.info(
                "imprimir_handoff_requested",
                state=handoff.get("state"),
                pooled=handoff.get("pooled"),
                changed=handoff.get("changed"),
            )
            return payload if isinstance(payload, dict) else {}
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300] if e.response is not None else ""
        log.error("request_handoff_http_error", status=e.response.status_code, body=body)
        return {}
    except Exception as e:
        log.exception("request_handoff_failed", error=str(e))
        return {}


# Documented statuses of POST .../contact-phone (integracion §3) → a compact result the model reacts
# to. 422 is the only one worth re-asking on (invalid number, nothing saved); 404/401 are terminal.
_CONTACT_PHONE_STATUS = {422: "invalid", 404: "not_found", 401: "unauthorized"}


async def submit_contact_phone(
    conversation_id: int, phone: Optional[str] = None, refused: bool = False
) -> dict[str, Any]:
    """Report a captured phone (or a refusal) to the CRM: POST .../contact-phone.

    Not a tool — `guardar_telefono_contacto` calls this. Sends the number VERBATIM; the CRM normalises
    and validates it (§3), so we never clean/format it here. Idempotent on the CRM side. Best-effort:
    never raises. Maps the documented statuses:
      200 → {"status": "ok"}            registered; don't ask again
      422 → {"status": "invalid"}       number rejected, nothing saved; re-ask if attempts remain
      404 → {"status": "not_found"}     unknown conversation; don't retry
      401 → {"status": "unauthorized"}  bad token (config issue); don't retry
    """
    log = logger.bind(conversation_id=conversation_id)
    if not refused and not (phone or "").strip():
        # Guard: never POST an empty capture — treat it like an invalid number so the model re-asks.
        return {"status": "invalid", "reason": "empty_phone"}
    payload: dict[str, Any] = (
        {"refused": True} if refused else {"phone": phone, "source": "ai", "confidence": "stated"}
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await _request(
                client,
                "POST",
                f"/api/v1/whatsapp/conversations/{conversation_id}/contact-phone",
                json=payload,
            )
            body = resp.json() if resp.content else {}
            contact = body.get("contact") if isinstance(body, dict) else None
            log.info("imprimir_contact_phone_submitted", refused=refused, captured=not refused)
            return {"status": "ok", "refused": refused, "contact": contact}
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else 0
        result = _CONTACT_PHONE_STATUS.get(status, "error")
        log.warning("imprimir_contact_phone_rejected", status=status, result=result)
        return {"status": result, "http_status": status}
    except Exception as e:
        log.exception("imprimir_contact_phone_failed", error=str(e))
        return {"status": "error", "error": str(e) or type(e).__name__}


@tool
async def guardar_telefono_contacto(
    config: RunnableConfig,
    telefono: Optional[str] = None,
    rechazado: bool = False,
) -> str:
    """Registra en el CRM el teléfono que el cliente dio, o que se niega a darlo (canal Messenger).

    Llamala cuando el cliente ESCRIBE su número en dígitos (pasalo TAL CUAL, sin limpiarlo ni
    completar el código de país) o cuando se niega o cambia de tema sin darlo (`rechazado=True`). Si
    el cliente lo dicta en palabras, NO adivines: pedile que lo escriba en dígitos y no llames esto.
    No manejas ids: el conversation_id viene del contexto.

    Args:
        config: Interno; lo inyecta el sistema. No lo pases.
        telefono: El número tal cual lo escribió el cliente. Omitilo si rechazado=True.
        rechazado: True si el cliente no quiere dar el número.
    """
    conversation_id = _ctx_conversation_id(config)
    log = logger.bind(conversation_id=conversation_id)
    if not conversation_id:
        log.warning("guardar_telefono_no_conversation_id")
        return json.dumps({"status": "error", "error": "no_conversation_id"}, ensure_ascii=False)
    result = await submit_contact_phone(conversation_id, phone=telefono, refused=rechazado)
    if result.get("status") == "invalid":
        # 422 / empty: the number was not saved — the model should re-ask once if attempts remain.
        result["repreguntar"] = True
    return json.dumps(result, ensure_ascii=False)
