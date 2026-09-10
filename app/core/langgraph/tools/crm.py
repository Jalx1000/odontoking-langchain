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


# Marca (custom attribute del quote): las bolsas Magia Verde llevan marca "Magia Verde"; el resto del
# catálogo (hules, stretch film, tapas) es "Imprimir". Se deriva del SKU en código — no lo decide el LLM.
_MAGIA_VERDE_SKUS = {"CM_00002"}


def _marca_de_sku(sku: str) -> str:
    """Return the quote brand for a SKU: 'Magia Verde' for the bags, 'Imprimir' for everything else."""
    return "Magia Verde" if (sku or "").strip().upper() in _MAGIA_VERDE_SKUS else "Imprimir"


@tool
async def crear_cotizacion(
    sku: str,
    cantidad: int,
    criterios: Optional[dict[str, str]] = None,
    nit: Optional[str] = None,
    empresa: Optional[str] = None,
    notas: Optional[str] = None,
    *,
    config: RunnableConfig,
) -> str:
    """Deja la cotización pre-elaborada en el CRM al cerrar la toma de interés.

    Llamala en el PASO 5, ANTES del mensaje de despedida y ANTES de derivar. No calcules el precio ni el
    total: los pone el CRM desde la misma lista que contestó precio_producto. No manejas ids: el
    conversation_id viene del contexto.

    Args:
        sku: SKU del producto, tal como lo devolvió buscar_productos.
        cantidad: Cuántas unidades lleva el cliente.
        criterios: La variante elegida, igual que en precio_producto (ej. {"Tamaño": "50 L", "Canal": …}).
        nit: NIT del cliente, tal como lo dictó, sin corregirlo. Viaja también a la ficha del contacto.
        empresa: Razón social.
        notas: Lo que dijo el cliente y no entra en ningún campo.
        config: Interno; lo inyecta el sistema. No lo pases.
    """
    conversation_id = _ctx_conversation_id(config)
    if not conversation_id:
        logger.error("crm_crear_cotizacion_sin_conversation_id", sku=sku)
        return "No hay una conversación activa para crear la cotización. Avisá al equipo técnico."

    # El precio y el total NO se mandan a propósito: los resuelve el CRM desde la lista del catálogo.
    # `marca` es el custom attribute de la cotización: "Magia Verde" para bolsas, "Imprimir" para el resto.
    body: dict[str, Any] = {"sku": sku, "cantidad": cantidad, "marca": _marca_de_sku(sku)}
    if criterios:
        body["criterios"] = criterios
    if nit:
        body["nit"] = nit
    if empresa:
        body["empresa"] = empresa
    if notas:
        body["notas"] = notas

    async with httpx.AsyncClient(timeout=_PRODUCTOS_TIMEOUT) as http:
        resp = await http.post(
            f"{_BASE}/api/v1/productos/conversations/{conversation_id}/cotizacion",
            json=body,
            headers=_HEADERS,
        )

    if resp.status_code in (200, 201):
        payload = resp.json()
        logger.info(
            "crm_crear_cotizacion_ok",
            sku=sku,
            quote_id=payload.get("quote_id") if isinstance(payload, dict) else None,
        )
        return json.dumps(payload, ensure_ascii=False)

    mensaje = resp.json().get("message", "") if _es_json(resp) else ""

    if resp.status_code == 409:
        logger.info("crm_crear_cotizacion_derivada", conversation_id=conversation_id)
        return "STOP: la conversación fue derivada a un asesor humano. No respondas nada más."

    if resp.status_code == 422:
        # El CRM nombra qué falta (un eje, o "contacto sin asociar") o por qué no se cotiza (la nota de
        # la fila: distribuidor/hogar → derivar). Se lo pasamos tal cual al modelo.
        return mensaje or "No se pudo crear la cotización con los datos recibidos."

    if resp.status_code == 404:
        return mensaje or f"No existe un producto con el SKU {sku}."

    return _mensaje_error(resp, "No se pudo crear la cotización.")


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


# ══════════════════════════════════════════════════════════════════════════════
# Material de productos por WhatsApp (fotos, fichas técnicas, enlaces) + opciones
# tocables. Endpoints ya desplegados; ver langchain-herramientas-material-producto.md.
#
# La idea que gobierna el diseño: el agente pide "3 imágenes del SKU X"; el CRM
# decide QUÉ archivos, en qué orden y por qué vía. Las rutas/URLs de los archivos
# NUNCA pasan por el modelo — sólo maneja el `sku` (público, ya aparece en las
# cotizaciones) y ve CUÁNTOS adjuntos hay de cada tipo.
#
# Sin tenacity a propósito: reintentar el POST de /media o /interactive puede
# DUPLICAR el envío al cliente, y ningún error de estos endpoints mejora
# reintentando. Todos los errores se devuelven como TEXTO, nunca como excepción:
# una excepción corta el turno y el cliente se queda sin respuesta.
# ══════════════════════════════════════════════════════════════════════════════

_PRODUCTOS_TIMEOUT = 20.0
_WHATSAPP_MAX_OPCIONES = 10  # techo de Meta para filas de lista (el CRM también lo valida)


def _es_json(resp: httpx.Response) -> bool:
    return resp.headers.get("content-type", "").startswith("application/json")


def _mensaje_error(resp: httpx.Response, fallback: str) -> str:
    """Return a client-facing message for a failed productos/whatsapp call.

    401 y 5xx son fallas de infraestructura que el agente no puede resolver, pero sí puede avisar en vez
    de quedarse mudo. Cuando el CRM manda un `message` (ya redactado en español), se prefiere ese.
    """
    logger.warning("crm_productos_http_error", status=resp.status_code, url=str(resp.url))
    if resp.status_code == 401:
        return "El CRM rechazó las credenciales. Avisá al equipo técnico."
    mensaje = resp.json().get("message", "") if _es_json(resp) else ""
    return mensaje or fallback


def _precio_desc(item: dict[str, Any]) -> str:
    """Precio legible de un producto del catálogo, sin exponer 0 en los de precio variable.

    Cuando el producto se cotiza por variante, la API devuelve `precio: null` (su columna de precio
    fijo vale 0 a propósito): mostrarlo como monto haría que el agente ofrezca productos a 0 Bs. En ese
    caso se indica que el precio sale de `precio_producto`, no un número inventado.
    """
    if item.get("precio_variable") or item.get("precio") is None:
        ejes = ", ".join(item.get("ejes_precio") or [])
        return f"precio por variante — ejes: {ejes} (usá precio_producto)" if ejes else "precio por variante (usá precio_producto)"
    return f"Bs {item['precio']}"


@tool
async def buscar_productos(busqueda: str) -> str:
    """Busca productos del catálogo que tengan material para enviar (fotos, fichas, enlaces).

    Devuelve el SKU, nombre, precio y cuántos adjuntos de cada tipo (imagen, documento, enlace) tiene
    cada producto. Solo aparecen productos que SÍ tienen material: si un producto no está en la lista,
    no hay nada que mandar. Úsala ANTES de ofrecerle o prometerle fotos o fichas al cliente.

    Args:
        busqueda: Texto a buscar en el nombre o el SKU del producto.
    """
    async with httpx.AsyncClient(timeout=_PRODUCTOS_TIMEOUT) as http:
        resp = await http.get(
            f"{_BASE}/api/v1/productos/catalogo",
            params={"q": busqueda, "limit": 10},
            headers=_HEADERS,
        )

    if resp.status_code != 200:
        return _mensaje_error(resp, "No pude consultar el catálogo de material.")

    productos = _data(resp)
    if not isinstance(productos, list) or not productos:
        return f"No hay productos con material que coincidan con «{busqueda}»."

    return "\n".join(
        "{sku} — {nombre} ({precio}) · imágenes: {imagen}, documentos: {documento}, enlaces: {enlace}".format(
            sku=p["sku"],
            nombre=p["nombre"],
            precio=_precio_desc(p),
            **p["adjuntos"],
        )
        for p in productos
    )


@tool
async def ficha_producto(sku: str) -> str:
    """Devuelve la descripción y las características técnicas de un producto del catálogo.

    Úsala para describirle el producto al cliente con datos reales en vez de improvisar.

    Args:
        sku: SKU exacto del producto, tal como lo devolvió buscar_productos.
    """
    async with httpx.AsyncClient(timeout=_PRODUCTOS_TIMEOUT) as http:
        resp = await http.get(f"{_BASE}/api/v1/productos/catalogo/{sku}", headers=_HEADERS)

    if resp.status_code == 404:
        mensaje = resp.json().get("message", "") if _es_json(resp) else ""
        return mensaje or f"No existe un producto con el SKU {sku}."

    if resp.status_code != 200:
        return _mensaje_error(resp, "No pude consultar la ficha del producto.")

    d = _data(resp)

    lineas = [f"{d['sku']} — {d['nombre']}", f"Precio: {_precio_desc(d)}"]

    if d.get("descripcion"):
        lineas.append(d["descripcion"])

    # `caracteristicas` es SIEMPRE un objeto, nunca una lista. Vacío es {}.
    for etiqueta, valor in (d.get("caracteristicas") or {}).items():
        lineas.append(f"{etiqueta}: {valor}")

    adj = d["adjuntos"]
    lineas.append(
        f"Material disponible — imágenes: {adj['imagen']}, "
        f"documentos: {adj['documento']}, enlaces: {adj['enlace']}"
    )

    return "\n".join(lineas)


@tool
async def precio_producto(
    sku: str, criterios: dict[str, str], cantidad: Optional[int] = None
) -> str:
    """Devuelve el precio de un producto según los criterios (variante) que eligió el cliente.

    El precio ya NO es fijo: depende de lo que el cliente elija y lo resuelve el CRM. Llamá SIEMPRE a
    esta herramienta antes de decir un precio, para cualquier producto (si es de precio fijo, igual te lo
    devuelve). Nunca calcules, estimes, redondeés ni repitas un precio de memoria.

    Para dar un TOTAL, pasá `cantidad`: la respuesta trae un bloque `cantidad` con `total`,
    `cumple_minimo`, `minimo` y `minimo_texto`. Decí el precio unitario con su `unidad` (p. ej.
    "Bs 14,00 por pack de 10 u") y el `total`; si `cumple_minimo` es false, avisá el mínimo y no cierres.

    Devuelve un JSON con `estado`:
    - "resuelto": una sola combinación coincide → tenés el precio. Ojo: si la fila trae
      `cotiza: false`, NO la cotices — hacé lo que diga su `nota` (p. ej. derivar).
    - "ambiguo": faltan ejes → preguntá al cliente EXACTAMENTE los de `faltan`, ofreciéndole los
      valores de `opciones`, y volvé a llamar. No elijas la variante por él.
    - "sin_datos": la combinación no está cargada → derivá, no ofrezcas la más parecida.

    Args:
        sku: SKU del producto, tal como lo devolvió buscar_productos.
        criterios: Los ejes que el cliente ya definió, ej. {"Tamaño": "50 L", "Canal": "HORECA"}. Mandá
            todo lo que hayas entendido; un criterio que no sea un eje del producto se ignora. Ignora
            acentos/mayúsculas/espacios pero NO acepta sinónimos ("50 litros" no coincide con "50 L").
        cantidad: Unidades pedidas, para obtener el total y validar el mínimo. Omitila si aún no la sabés.
    """
    body: dict[str, Any] = {"criterios": criterios or {}}
    if cantidad is not None:
        body["cantidad"] = cantidad
    async with httpx.AsyncClient(timeout=_PRODUCTOS_TIMEOUT) as http:
        resp = await http.post(
            f"{_BASE}/api/v1/productos/catalogo/{sku}/precio",
            json=body,
            headers=_HEADERS,
        )

    if resp.status_code == 404:
        mensaje = resp.json().get("message", "") if _es_json(resp) else ""
        return mensaje or f"No existe un producto con el SKU {sku}."

    if resp.status_code != 200:
        return _mensaje_error(resp, "No pude consultar el precio.")

    payload = resp.json()
    logger.info(
        "crm_precio_producto_ok",
        sku=sku,
        estado=payload.get("estado") if isinstance(payload, dict) else None,
    )
    # Passthrough del JSON: el prompt (Regla 0) dice cómo reaccionar según `estado`/`cotiza`/`faltan`.
    return json.dumps(payload, ensure_ascii=False)


@tool
async def enviar_material(
    sku: str,
    tipo: str,
    cantidad: int = 3,
    *,
    config: RunnableConfig,
) -> str:
    """Envía al cliente por WhatsApp el material de un producto.

    El CRM elige qué archivos y en qué orden (la portada primero). Los envía SIN pie de foto a
    propósito: tu descripción va como un mensaje de texto aparte, DESPUÉS de que salió el material.
    No manejas ids: el conversation_id viene del contexto.

    Args:
        sku: SKU del producto cuyo material se envía.
        tipo: "imagen", "documento" o "enlace".
        cantidad: Cuántos enviar. 3 por defecto; el CRM no acepta más de 10.
        config: Interno; lo inyecta el sistema. No lo pases.
    """
    conversation_id = _ctx_conversation_id(config)
    if not conversation_id:
        # Falla de wiring, no del modelo: sin conversación no hay a quién mandarle nada.
        logger.error("crm_enviar_material_sin_conversation_id", sku=sku)
        return "No hay una conversación activa para enviar material. Avisá al equipo técnico."

    async with httpx.AsyncClient(timeout=_PRODUCTOS_TIMEOUT) as http:
        resp = await http.post(
            f"{_BASE}/api/v1/productos/conversations/{conversation_id}/media",
            json={"sku": sku, "tipo": tipo, "cantidad": cantidad},
            headers=_HEADERS,
        )

    if resp.status_code == 200:
        d = resp.json()
        logger.info("crm_enviar_material_ok", sku=d.get("sku", sku), enviados=d.get("enviados"))
        return (
            f"Enviados {d.get('enviados')} archivo(s) de {d.get('sku', sku)}. "
            "Ahora mandá tu descripción como texto, en un mensaje aparte."
        )

    # Todos los errores de acá son definitivos para este turno; se devuelven como texto.
    mensaje = resp.json().get("message", "") if _es_json(resp) else ""

    if resp.status_code == 409:
        logger.info("crm_enviar_material_derivada", conversation_id=conversation_id)
        return "STOP: la conversación fue derivada a un asesor humano. No respondas nada más."

    if resp.status_code == 404:
        return mensaje or f"El producto {sku} no tiene material de tipo {tipo}."

    if resp.status_code == 422:
        return mensaje or "No se pudo enviar el material por una restricción de WhatsApp."

    return _mensaje_error(resp, "No se pudo enviar el material.")


@tool
async def mostrar_opciones(
    cuerpo: str,
    opciones: list[dict],
    encabezado: Optional[str] = None,
    pie: Optional[str] = None,
    texto_boton_lista: str = "Ver opciones",
    *,
    config: RunnableConfig,
) -> str:
    """Muestra opciones tocables al cliente en vez de pedirle que escriba.

    Úsala cuando el cliente tiene que elegir entre alternativas concretas: qué material quiere ver,
    cuál de varios productos, confirmar sí o no. Le evita tipear un SKU o un número, que es donde más
    se equivocan. Cuando toca una opción, su respuesta llega como un mensaje normal con el título en el
    texto y el id en `selection.id`. Rutea SIEMPRE por el id.

    Args:
        cuerpo: La pregunta o el texto que acompaña las opciones. Máximo 1024.
        opciones: Lista de dicts con `id` (lo que vuelve al elegir, elígelo tú y que sea informativo,
            ej. "fotos:CM_00015"), `titulo` (lo que el cliente ve, máximo 20 caracteres) y
            `descripcion` opcional (máximo 72, solo se muestra si son más de 3 opciones).
        encabezado: Título breve arriba del cuerpo. Máximo 60.
        pie: Texto chico abajo. Máximo 60.
        texto_boton_lista: Solo se usa si hay más de 3 opciones. Máximo 20.
        config: Interno; lo inyecta el sistema. No lo pases.
    """
    conversation_id = _ctx_conversation_id(config)
    if not conversation_id:
        logger.error("crm_mostrar_opciones_sin_conversation_id")
        return "No hay una conversación activa. Avisá al equipo técnico."

    if not opciones:
        return "No se puede mostrar un menú sin opciones."

    # El formato lo elige el código, no el modelo: WhatsApp admite 3 botones, y de 4 a 10 exige lista.
    if len(opciones) > _WHATSAPP_MAX_OPCIONES:
        return (
            f"Son {len(opciones)} opciones y WhatsApp admite {_WHATSAPP_MAX_OPCIONES} como máximo. "
            "Mostrá las más relevantes o preguntá algo que acote la búsqueda."
        )

    es_lista = len(opciones) > 3
    cuerpo_envio: dict[str, Any] = {
        "format": "list" if es_lista else "button",
        "body": cuerpo,
        "header": encabezado,
        "footer": pie,
    }

    if es_lista:
        cuerpo_envio["list_button"] = texto_boton_lista
        cuerpo_envio["sections"] = [
            {
                "rows": [
                    {
                        k: v
                        for k, v in (
                            ("id", str(o["id"])),
                            ("title", o["titulo"]),
                            ("description", o.get("descripcion")),
                        )
                        if v
                    }
                    for o in opciones
                ]
            }
        ]
    else:
        # Los botones no admiten descripción: WhatsApp solo muestra el título.
        cuerpo_envio["buttons"] = [{"id": str(o["id"]), "title": o["titulo"]} for o in opciones]

    async with httpx.AsyncClient(timeout=_PRODUCTOS_TIMEOUT) as http:
        resp = await http.post(
            f"{_BASE}/api/v1/whatsapp/conversations/{conversation_id}/interactive",
            json=cuerpo_envio,
            headers=_HEADERS,
        )

    if resp.status_code == 200:
        logger.info("crm_mostrar_opciones_ok", formato=cuerpo_envio["format"], n=len(opciones))
        return f"Opciones enviadas ({len(opciones)}). Esperá que el cliente elija antes de seguir."

    if resp.status_code == 409:
        return "STOP: la conversación fue derivada a un asesor humano. No respondas nada más."

    if resp.status_code == 501:
        # El canal activo no tiene botones (Kommo, Messenger). No es reintentable.
        return (
            "Este canal no admite botones. Mandá las opciones como texto numerado "
            "y pedile al cliente que responda con el número."
        )

    if resp.status_code == 422:
        # El CRM valida los límites de Meta y nombra el campo que falló.
        return _mensaje_error(resp, "Las opciones no cumplen los límites de WhatsApp.")

    return _mensaje_error(resp, "No se pudieron enviar las opciones.")
