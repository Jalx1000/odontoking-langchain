"""Kohlberg "Club del Vino" (Sofía) CRM tools - promos catalog, sucursales, order registration.

Flow: WhatsApp -> Krayin CRM (kohlberg.sofopolis.com) -> agent -> CrmGateway. Same CRM as the other
tenants, different subdomain (KOHLBERG_API_URL / KOHLBERG_API_TOKEN, falling back to the sofo-crm
gateway pair CRM_BASE_URL / CRM_API_KEY).

Design note - one coarse tool per action, only plain fields (no ids threaded by the LLM):

    get_promos()          - active wine promotions (the ONLY source of truth for wines/prices).
    get_sucursales(...)   - branch info by city (pickup point / advisor phone). Static catalog.
    registrar_pedido(...) - registers the confirmed order as a Krayin lead with product lines.
    think(...)            - no-op scratchpad so the model can verify flow coherence before replying.

The conversation's auto-created lead (contact.lead_id) and person (contact.person_id) are injected
server-side via config.metadata and never reach the model; registrar_pedido reuses them so an order
enriches the existing lead instead of creating a duplicate. Wines carry a real product_id that comes
from get_promos - the LLM passes those ids straight through (they are catalog ids, safe to surface).
"""

import json
import asyncio
import time
import unicodedata
from typing import Any, Optional

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import logger

_BASE = settings.KOHLBERG_API_URL
_HEADERS = {
    "accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {settings.KOHLBERG_API_TOKEN}",
}

_PLACEHOLDER_NAME = "Cliente WhatsApp"
_SOURCE_WHATSAPP = 6          # lead_source_id "WhatsApp"
_LEAD_TYPE_VENTA = 1          # New Business
_OWNER_USER_ID = 1           # default lead owner

# Real Kohlberg per-city pipeline + stage ids. Each city is its own pipeline; the STAGE is what marks
# an order's state, so we move the lead by stage id (never by a tag - tags here mean delivery type and
# tagging with a wrong id mislabels the lead). "entregado" ("Pedidos entregados") is a concreted order
# and is immutable. Key is the canonical city (see _CITY_ALIASES); unknown/absent city -> "sin ciudad".
_CITY_STAGES: dict[str, dict[str, int]] = {
    "tarija":     {"pipeline": 1,  "no_atendido": 1,  "confirmado": 2,  "sin_interes": 39, "entregado": 5,  "cancelado": 6,  "otros": 58},
    "santa cruz": {"pipeline": 3,  "no_atendido": 11, "confirmado": 12, "sin_interes": 45, "entregado": 13, "cancelado": 14, "otros": 51},
    "potosi":     {"pipeline": 4,  "no_atendido": 15, "confirmado": 16, "sin_interes": 50, "entregado": 17, "cancelado": 18, "otros": 57},
    "oruro":      {"pipeline": 6,  "no_atendido": 23, "confirmado": 24, "sin_interes": 49, "entregado": 25, "cancelado": 26, "otros": 56},
    "la paz":     {"pipeline": 7,  "no_atendido": 27, "confirmado": 28, "sin_interes": 48, "entregado": 29, "cancelado": 30, "otros": 55},
    "cochabamba": {"pipeline": 8,  "no_atendido": 31, "confirmado": 32, "sin_interes": 47, "entregado": 33, "cancelado": 34, "otros": 54},
    "sucre":      {"pipeline": 9,  "no_atendido": 35, "confirmado": 36, "sin_interes": 46, "entregado": 37, "cancelado": 38, "otros": 53},
    "sin ciudad": {"pipeline": 10, "no_atendido": 40, "confirmado": 41, "sin_interes": 44, "entregado": 42, "cancelado": 43, "otros": 52},
}
# Stage-id sets (across every city) for classifying a lead by its lead_pipeline_stage_id.
_STAGE_NO_ATENDIDO_IDS = {s["no_atendido"] for s in _CITY_STAGES.values()}
_STAGE_ENTREGADO_IDS = {s["entregado"] for s in _CITY_STAGES.values()}

# Canonical city -> product-city id (the `ciudad_producto_sucursal` values on each product). These are
# a DIFFERENT id space from the pipeline ids above - do not conflate them. A product is available in a
# city if its list includes that id OR the "todas" id.
_CITY_PRODUCT_IDS: dict[str, int] = {
    "santa cruz": 1,
    "cochabamba": 4,
    "la paz": 5,
    "potosi": 8,
    "tarija": 9,
    "oruro": 11,
    "sucre": 12,
}
_TODAS_CITY_ID = 10  # product available in every city

# Product types on the Kohlberg catalog (custom `product_type` attribute).
_TIPO_VINO = 10
_TIPO_PACK = 11

# Free-text city (accents/abbreviations the client may type) -> canonical key.
_CITY_ALIASES = {
    "santa cruz": "santa cruz", "santacruz": "santa cruz", "scz": "santa cruz",
    "la paz": "la paz", "lapaz": "la paz", "lp": "la paz",
    "cochabamba": "cochabamba", "cbba": "cochabamba", "cocha": "cochabamba",
    "tarija": "tarija",
    "sucre": "sucre", "chuquisaca": "sucre",
    "potosi": "potosi", "potosí": "potosi",
    "oruro": "oruro",
}

# ── Low-level HTTP ────────────────────────────────────────────────────────────

def _is_transient(exc: BaseException) -> bool:
    """Retry only on transient failures: 5xx and network timeouts/connection errors.

    Deliberately does NOT retry 429 (Too Many Attempts): the CRM's throttle is shared across every
    call this agent makes with one Sanctum token, so retrying a 429 hammers it further and deepens the
    throttle. On 429 we fail fast and let the turn degrade gracefully.
    """
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


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


def _data(resp: httpx.Response) -> Any:
    """Return the 'data' field of a Krayin JSON response (dict or list), else the raw json."""
    payload = resp.json() if resp.content else {}
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def _data_list(resp: httpx.Response) -> list[dict[str, Any]]:
    """Return the list of records from a Krayin response, tolerating the shapes we've seen live.

    - `[{"data":[...], "meta":{...}}]`  (the one-element array wrapper, e.g. /api/v1/products)
    - `{"data":[...]}`                   (standard paginated resource)
    - `[ {...}, {...} ]`                 (a bare list of records)
    """
    payload = resp.json() if resp.content else []
    if isinstance(payload, list):
        if payload and isinstance(payload[0], dict) and isinstance(payload[0].get("data"), list):
            payload = payload[0]
        else:
            return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        data = payload.get("data", payload)
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
    return []


def _normalize_wa_id(wa_id: str) -> str:
    """Digits-only WhatsApp id - strip '+', spaces so the CRM lookup never misses."""
    return (wa_id or "").replace("+", "").replace(" ", "").strip()


def _clean_name(name: Optional[str]) -> str:
    """Return a real name or the placeholder used until the client tells us who they are."""
    clean = name.strip() if isinstance(name, str) else ""
    return clean or _PLACEHOLDER_NAME


# ── Context (injected via config.metadata; the LLM never passes ids) ───────────

def _ctx_ids(config: Optional[RunnableConfig]) -> tuple[Optional[int], Optional[int]]:
    """Read (lead_id, person_id) injected by the graph via config.metadata."""
    metadata = (config or {}).get("metadata") or {}
    return metadata.get("lead_id"), metadata.get("person_id")


def _ctx_contact_name(config: Optional[RunnableConfig]) -> Optional[str]:
    """Best contact name from metadata (registered name, else WhatsApp profile), or None."""
    metadata = (config or {}).get("metadata") or {}
    name = metadata.get("nombre_registrado") or metadata.get("nombre_whatsapp")
    clean = name.strip() if isinstance(name, str) else ""
    return clean or None


def _ctx_wa_id(config: Optional[RunnableConfig]) -> str:
    """WhatsApp/conversation key injected via metadata (used only as a person-create fallback)."""
    metadata = (config or {}).get("metadata") or {}
    wa = metadata.get("wa_id")
    return _normalize_wa_id(wa) if isinstance(wa, str) else ""


def _normalizar(texto: Any) -> str:
    """Lowercase + strip accents + trim (mirrors the n8n `normalizar`)."""
    s = unicodedata.normalize("NFD", str(texto or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").strip()


def _resolve_city_key(ciudad: Optional[str]) -> Optional[str]:
    """Map free-text city to a canonical key (None when empty/unrecognised)."""
    return _CITY_ALIASES.get(_normalizar(ciudad))


def _city_product_id(ciudad: Optional[str]) -> int:
    """Product-city id for a free-text city; falls back to the 'todas' id (matches n8n behaviour)."""
    key = _resolve_city_key(ciudad)
    return _CITY_PRODUCT_IDS.get(key, _TODAS_CITY_ID) if key else _TODAS_CITY_ID


def _city_stages(ciudad: Optional[str]) -> dict[str, int]:
    """Pipeline + stage ids for a free-text city; falls back to 'sin ciudad' when unrecognised."""
    key = _resolve_city_key(ciudad)
    return _CITY_STAGES.get(key, _CITY_STAGES["sin ciudad"]) if key else _CITY_STAGES["sin ciudad"]


def _to_int(valor: Any) -> Optional[int]:
    """Coerce to int like JS Number() for the enable/type flags; None when not an integer."""
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


def _parse_ciudades(valor: Any) -> list[int]:
    """Parse a product's `ciudad_producto_sucursal` (comma-separated ids, or list) into ints > 0.

    Mirrors the n8n `parsearCiudades`.
    """
    if valor in (None, ""):
        return []
    raw = valor if isinstance(valor, list) else str(valor).split(",")
    out: list[int] = []
    for part in raw:
        s = str(part).strip()
        if s.lstrip("-").isdigit() and int(s) > 0:
            out.append(int(s))
    return out


def _parse_combo(valor: Any) -> list[Any]:
    """Parse a combos field: array, JSON string, bare id ('15'), or empty (mirrors n8n `parseCombo`)."""
    if valor in (None, ""):
        return []
    if isinstance(valor, list):
        return valor
    s = str(valor).strip()
    if s in ("[]", "null"):
        return []
    if s.isdigit():
        return [{"id": int(s), "name": None, "qty": 1}]
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, list) else []
    except Exception:  # noqa: BLE001
        return []


# ── Lead helpers (best-effort; a lead failure must not break the reply) ────────

async def _find_person_by_wa_id(client: httpx.AsyncClient, wa_id: str) -> Optional[dict[str, Any]]:
    """Look up a Krayin person by WhatsApp number (contact_numbers LIKE). Returns dict or None."""
    resp = await _request(
        client,
        "GET",
        "/api/v1/contacts/persons/search",
        params={"search": _normalize_wa_id(wa_id), "searchFields": "contact_numbers:like;"},
    )
    data = _data(resp)
    return data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None


async def _resolve_person(client: httpx.AsyncClient, wa_id: str, nombre: Optional[str]) -> Optional[int]:
    """Find or create the person; return person_id."""
    person = await _find_person_by_wa_id(client, wa_id)
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


async def _get_lead(client: httpx.AsyncClient, lead_id: int) -> Optional[dict[str, Any]]:
    """Fetch a lead by id, or None on 404/failure (best-effort)."""
    try:
        resp = await _request(client, "GET", f"/api/v1/leads/{lead_id}")
        data = _data(resp)
        return data if isinstance(data, dict) else None
    except Exception as e:  # noqa: BLE001
        logger.warning("kohlberg_lead_fetch_failed", lead_id=lead_id, error=str(e))
        return None


def _lead_stage_name(lead: dict[str, Any]) -> str:
    """Current stage name of a lead, normalized to lowercase with hyphens as spaces ('' when unknown)."""
    stage = lead.get("lead_pipeline_stage") or lead.get("stage") or {}
    name = stage.get("name") if isinstance(stage, dict) else None
    return (name or "").strip().lower().replace("-", " ")


def _lead_stage_id(lead: dict[str, Any]) -> Optional[int]:
    """Current stage id of a lead (top-level or nested), or None."""
    sid = lead.get("lead_pipeline_stage_id")
    if sid is None:
        stage = lead.get("lead_pipeline_stage") or lead.get("stage") or {}
        sid = stage.get("id") if isinstance(stage, dict) else None
    return _to_int(sid)


def _is_unattended(lead: dict[str, Any]) -> bool:
    """True if the lead is still the untouched auto-created lead (safe to enrich).

    Prefers the real stage id (per-city 'No atendido'); falls back to the stage name when the id is
    absent, so a fresh lead with unknown stage is still treated as enrichable.
    """
    sid = _lead_stage_id(lead)
    if sid is not None:
        return sid in _STAGE_NO_ATENDIDO_IDS
    return _lead_stage_name(lead) in ("", "no atendido")


def _is_delivered(lead: dict[str, Any]) -> bool:
    """True if the lead is a concreted/delivered order ('Pedidos entregados') - immutable."""
    sid = _lead_stage_id(lead)
    if sid is not None and sid in _STAGE_ENTREGADO_IDS:
        return True
    return _lead_stage_name(lead) == "pedidos entregados"


def _lead_has_products(lead: dict[str, Any]) -> bool:
    """True if the lead already has at least one product line (an order was already registered on it)."""
    products = lead.get("products")
    if isinstance(products, dict):
        return len(products) > 0
    return bool(products) if isinstance(products, list) else False


def _lead_line_items(lead: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract [{product_id, producto, cantidad}] from a lead's product lines.

    Handles both `products` and `lead_products`, list or dict, and the id/name whether flat or nested
    under `product`. `product_id` lets a "repeat order" reuse the same ids without re-resolving names.
    """
    raw = lead.get("products") or lead.get("lead_products") or []
    values = list(raw.values()) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    items: list[dict[str, Any]] = []
    for it in values:
        if not isinstance(it, dict):
            continue
        prod_obj = it.get("product")
        prod: dict[str, Any] = prod_obj if isinstance(prod_obj, dict) else {}
        name = it.get("name") or prod.get("name")
        if name:
            items.append(
                {"product_id": _to_int(it.get("product_id") or prod.get("id")),
                 "producto": name, "cantidad": it.get("quantity")}
            )
    return items


def _lead_stage_display(lead: dict[str, Any]) -> Optional[str]:
    """Original-case stage name of a lead (for display), or None."""
    stage = lead.get("lead_pipeline_stage") or lead.get("stage") or {}
    name = stage.get("name") if isinstance(stage, dict) else None
    clean = name.strip() if isinstance(name, str) else ""
    return clean or None


def _lead_person_id(lead: dict[str, Any]) -> Optional[int]:
    """Person id of a lead (from the nested person object or the flat person_id), or None."""
    person = lead.get("person")
    pid = person.get("id") if isinstance(person, dict) else lead.get("person_id")
    return _to_int(pid)


def _is_fresh_for_order(lead: dict[str, Any]) -> bool:
    """A lead is enrichable only while it is the untouched auto-created lead AND has no order yet.

    Anything else - an advisor already advanced it, it was delivered ('Pedidos entregados'), or it
    already carries a registered order - is left intact; a new order goes to a brand-new lead.
    """
    return _is_unattended(lead) and not _lead_has_products(lead)


async def _create_lead(
    client: httpx.AsyncClient,
    person_id: Optional[int],
    wa_id: str,
    nombre: Optional[str],
    titulo: Optional[str],
    descripcion: str,
    ciudad: Optional[str],
) -> Optional[int]:
    """Create a fresh order lead in the city's pipeline ('No atendido' stage); return lead_id."""
    stages = _city_stages(ciudad)
    etiqueta = (nombre or "Cliente").strip()
    person: dict[str, Any] = {"name": _clean_name(nombre)}
    if person_id:
        person["id"] = person_id
    else:
        person["contact_numbers"] = [{"value": wa_id, "label": "work"}]
    body: dict[str, Any] = {
        "title": (titulo or f"Pedido Club del Vino - {etiqueta}").strip(),
        "description": descripcion or "Pedido vía WhatsApp",
        "lead_value": "0",
        "lead_source_id": _SOURCE_WHATSAPP,
        "lead_type_id": _LEAD_TYPE_VENTA,
        "user_id": _OWNER_USER_ID,
        "lead_pipeline_id": stages["pipeline"],
        "lead_pipeline_stage_id": stages["no_atendido"],
        "person": person,
        "entity_type": "leads",
    }
    resp = await _request(client, "POST", "/api/v1/leads", json=body)
    data = _data(resp)
    return data.get("id") if isinstance(data, dict) else None


async def _add_lead_product(
    client: httpx.AsyncClient, lead_id: int, product_id: int, name: str, quantity: int
) -> None:
    """Attach a catalog product (by its real id from get_promos) to the lead."""
    await _request(
        client,
        "PUT",
        f"/api/v1/leads/product/{lead_id}",
        json={
            "product_id": product_id,
            "name": (name or "").strip(),
            "price": 0,
            "quantity": max(int(quantity or 1), 1),
            "amount": 0,
            "is_new": False,
            "id": None,
        },
    )


async def _add_lead_note(
    client: httpx.AsyncClient, lead_id: int, title: str, fields: list[tuple[str, Any]]
) -> None:
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


async def _move_lead(client: httpx.AsyncClient, lead_id: int, ciudad: Optional[str], stage_key: str) -> None:
    """Move the lead to the given stage of the city's pipeline (e.g. 'confirmado', 'cancelado').

    Uses the real per-city pipeline + stage ids. Never touches tags: a tag here means delivery type, so
    tagging with a wrong id mislabels the order (e.g. as 'Delivery' when it's pickup-only).
    """
    stages = _city_stages(ciudad)
    body: dict[str, Any] = {"lead_pipeline_id": stages["pipeline"]}
    stage_id = stages.get(stage_key)
    if stage_id is not None:
        body["lead_pipeline_stage_id"] = stage_id
    await _request(client, "PUT", f"/api/v1/leads/{lead_id}", json=body)


def _clean_product(item: dict[str, Any]) -> dict[str, Any]:
    """Clean a catalog product for the model (mirrors the n8n `limpiarBase` + combo parsing).

    Drops Krayin's created_at/updated_at, parses the combo fields, and adds a `product_id` alias for
    the id so the model passes it straight to registrar_pedido. Every other field is kept as-is.
    """
    out = {k: v for k, v in item.items() if k not in ("created_at", "updated_at")}
    out["product_id"] = item.get("id")
    out["combos_productos"] = _parse_combo(item.get("combos_productos"))
    out["combos_productos_cantidad"] = _parse_combo(item.get("combos_productos_cantidad"))
    return out


# ── LLM-facing tools ──────────────────────────────────────────────────────────

async def _fetch_all_products(client: httpx.AsyncClient, max_pages: int = 20) -> list[dict[str, Any]]:
    """Fetch the FULL Krayin product list (with the flat custom attributes), following pagination.

    The n8n flow reads the whole `/api/v1/products` list and filters by `products` (enabled),
    `product_type` and `ciudad_producto_sucursal` in code - those flat custom fields come on this
    list, not on the trimmed por-ciudad endpoint. So we replicate that: fetch all, filter here.
    """
    productos: list[dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        resp = await _request(client, "GET", "/api/v1/products", params={"page": page, "limit": 100})
        payload = resp.json() if resp.content else {}
        # This endpoint wraps the page in a one-element array: [{data:[...], meta:{...}}] (the n8n flow
        # handles the same: `if Array.isArray(rawJson) items = rawJson[0].data`). Unwrap it, but also
        # tolerate the plain {data:[...]} shape.
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        productos.extend(p for p in (data if isinstance(data, list) else []) if isinstance(p, dict))
        meta = payload.get("meta") if isinstance(payload, dict) else None
        last_page = meta.get("last_page") if isinstance(meta, dict) else None
        if not last_page or page >= int(last_page):
            break
        page += 1
    return productos


# Short-TTL cache of the FULL catalog (shared across cities and conversations). The catalog changes
# rarely, so this collapses the heaviest CRM call (the whole product list) to once per window across
# every user - the main lever against the CRM's 429 throttle. Only successful (non-empty) fetches are
# cached, per the project caching rule.
_PROMOS_CACHE_TTL = float(getattr(settings, "KOHLBERG_PROMOS_CACHE_TTL", 60) or 60)
_promos_cache: dict[str, Any] = {"items": None, "at": 0.0}
_promos_lock = asyncio.Lock()


async def _get_products_cached() -> list[dict[str, Any]]:
    """Return the full product list, served from a short-TTL in-process cache on a hit."""
    now = time.monotonic()
    cached = _promos_cache.get("items")
    if cached is not None and (now - _promos_cache["at"]) < _PROMOS_CACHE_TTL:
        return cached
    async with _promos_lock:
        now = time.monotonic()  # re-check: another coroutine may have filled it while we waited
        cached = _promos_cache.get("items")
        if cached is not None and (now - _promos_cache["at"]) < _PROMOS_CACHE_TTL:
            return cached
        async with httpx.AsyncClient(timeout=25) as client:
            items = await _fetch_all_products(client)
        if items:  # cache only successful, non-empty responses
            _promos_cache["items"] = items
            _promos_cache["at"] = time.monotonic()
        return items


@tool
async def get_promos(ciudad: Optional[str] = None) -> str:
    """Obtiene las promociones y vinos ACTIVOS del Club del Vino Kohlberg (única fuente de verdad).

    Es la ÚNICA fuente válida de vinos, precios y promociones: nunca inventes ninguno de esos datos.
    Filtra por la CIUDAD del cliente (pásala apenas la conozcas): solo devuelve productos habilitados y
    disponibles en esa ciudad (o disponibles para todas). Separa `vinos` y `packs`. Cada ítem trae su
    `product_id` (úsalo tal cual al registrar el pedido), `name` (nombre exacto, respétalo), su
    descripción y su precio. Si un vino no trae precio de descuento, NO muestres "Precio Club del Vino"
    con un precio inventado. Muestra máximo 3 por respuesta.

    Args:
        ciudad: Ciudad del cliente (texto libre; se normaliza por dentro). Sin ciudad → solo productos
            marcados para todas las ciudades.

    Devuelve {"ciudad_id", "total_vinos", "total_packs", "vinos": [...], "packs": [...]}.
    """
    log = logger.bind(tool="get_promos", ciudad=(ciudad or "")[:40])
    ciudad_id = _city_product_id(ciudad)
    try:
        items = await _get_products_cached()  # short-TTL cache; one CRM fetch serves every city/turn
        vinos: list[dict[str, Any]] = []
        packs: list[dict[str, Any]] = []
        vistos: set[Any] = set()
        for prod in items:
            pid = prod.get("id")
            if pid in vistos:                                     # dedup por id (n8n `vistos`)
                continue
            if _to_int(prod.get("products")) != 1:               # estaHabilitado: Number(products)===1
                continue
            ciudades = _parse_ciudades(prod.get("ciudad_producto_sucursal"))
            if ciudad_id not in ciudades and _TODAS_CITY_ID not in ciudades:  # matchCiudad
                continue
            tipo = _to_int(prod.get("product_type"))             # 10=vino, 11=pack, otros se ignoran
            if tipo == _TIPO_VINO:
                vinos.append(_clean_product(prod))
                vistos.add(pid)
            elif tipo == _TIPO_PACK:
                packs.append(_clean_product(prod))
                vistos.add(pid)
        log.info(
            "kohlberg_get_promos_ok",
            ciudad_id=ciudad_id, total_items=len(items), vinos=len(vinos), packs=len(packs),
        )
        return json.dumps(
            {
                "ciudad_input": ciudad or "",
                "ciudad_id": ciudad_id,
                "total_vinos": len(vinos),
                "total_packs": len(packs),
                "vinos": vinos,
                "packs": packs,
            },
            ensure_ascii=False,
        )
    except httpx.HTTPStatusError as e:
        log.warning("kohlberg_get_promos_http_error", status=e.response.status_code)
        return json.dumps({"vinos": [], "packs": [], "error": f"api_{e.response.status_code}"}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        log.exception("kohlberg_get_promos_failed", error=str(e))
        return json.dumps({"vinos": [], "packs": [], "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def get_sucursales(ciudad: Optional[str] = None) -> str:
    """Devuelve las sucursales de Kohlberg (warehouses del CRM) filtradas por ciudad.

    Consulta GET /api/v1/settings/warehouses y filtra por nombre == ciudad. Úsala en dos casos:
    1) Tras confirmar el pedido, para indicar la sucursal donde el cliente recoge y cancela.
    2) Cuando el cliente pide hablar con un asesor: pregunta primero la ciudad y comparte SOLO el
       teléfono de esa ciudad (nunca el de otra, nunca inventes un número).

    No se hace delivery ni entregas a domicilio: el cliente siempre recoge en sucursal.

    Args:
        ciudad: Ciudad del cliente (texto libre; se normaliza por dentro). Si se omite, devuelve todas.

    Devuelve {"total_encontrados": <n>, "sucursales": [<warehouse>...]}.
    """
    log = logger.bind(tool="get_sucursales", ciudad=(ciudad or "")[:40])
    # Match on the canonical city name when we recognise it, else on the raw text (mirrors the n8n
    # exact-name filter). Empty city → return every branch.
    filtro = _normalizar(_resolve_city_key(ciudad) or ciudad)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await _request(
                client, "GET", "/api/v1/settings/warehouses", params={"sort": "id"}
            )
            data = _data(resp)
            if isinstance(data, dict):
                data = data.get("data", [])
            warehouses = [w for w in (data if isinstance(data, list) else []) if isinstance(w, dict)]
        if filtro:
            sucursales = [
                w for w in warehouses if _normalizar(w.get("name") or w.get("nombre")) == filtro
            ]
        else:
            sucursales = warehouses
        log.info("kohlberg_get_sucursales_ok", encontrados=len(sucursales), total=len(warehouses))
        return json.dumps(
            {"filtro_input": ciudad or "", "total_encontrados": len(sucursales), "sucursales": sucursales},
            ensure_ascii=False,
        )
    except httpx.HTTPStatusError as e:
        log.warning("kohlberg_get_sucursales_http_error", status=e.response.status_code)
        return json.dumps({"sucursales": [], "error": f"api_{e.response.status_code}"}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        log.exception("kohlberg_get_sucursales_failed", error=str(e))
        return json.dumps({"sucursales": [], "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def registrar_pedido(
    mensaje: str,
    config: RunnableConfig,
    product_id: Optional[list[int]] = None,
    product_name: Optional[list[str]] = None,
    cantidad_product: Optional[list[int]] = None,
    nombre_del_cliente: Optional[str] = None,
    edad_del_cliente: Optional[int] = None,
    titulo_de_pedido: Optional[str] = None,
    ciudad_del_cliente: Optional[str] = None,
    ubicacion_del_cliente: Optional[str] = None,
    descripcion_corta: Optional[str] = None,
    es_pedido_confirmado: bool = False,
    es_pedido_cancelado: bool = False,
) -> str:
    """Registra el pedido del cliente como oportunidad (lead) en el CRM Kohlberg.

    Cada pedido confirmado es un registro propio: el primero enriquece la oportunidad que el CRM abrió
    para la conversación, y un pedido POSTERIOR distinto abre un lead nuevo (pedidos separados). Un
    pedido ya registrado, en proceso de un asesor o ENTREGADO ("Pedidos entregados") es intocable: no
    se edita ni se cancela desde aquí. Tú solo pasas los datos; no manejas ids internos. Los
    `product_id` y `product_name` DEBEN venir de get_promos, en el mismo orden que `cantidad_product`
    (arreglos paralelos). Llámala UNA vez por pedido, al confirmar (`es_pedido_confirmado=True`) o al
    cancelar (`es_pedido_cancelado=True`).

    Nunca combines el mensaje de este paso con el de la sucursal en una misma respuesta.

    Args:
        mensaje: Resumen en texto libre del pedido/estado para dejar registrado en el CRM.
        config: Interno; lo inyecta el sistema. No lo pases.
        product_id: Ids de los vinos (de get_promos), en orden paralelo a cantidad_product.
        product_name: Nombres EXACTOS de los vinos (de get_promos), en el mismo orden.
        cantidad_product: Cantidad de cada vino, en el mismo orden.
        nombre_del_cliente: Nombre del cliente (si no lo pasas, se toma del contexto).
        edad_del_cliente: Edad del cliente (no se vende a menores de 18).
        titulo_de_pedido: Título corto del pedido.
        ciudad_del_cliente: Ciudad del cliente.
        ubicacion_del_cliente: Referencia/ubicación indicada por el cliente, si la hay.
        descripcion_corta: Descripción corta del pedido.
        es_pedido_confirmado: True cuando el cliente confirma el pedido.
        es_pedido_cancelado: True cuando el cliente cancela el pedido.
    """
    lead_ctx, person_ctx = _ctx_ids(config)
    nombre = (nombre_del_cliente or "").strip() or _ctx_contact_name(config)
    ids = list(product_id or [])
    names = list(product_name or [])
    qtys = list(cantidad_product or [])
    log = logger.bind(tool="registrar_pedido", lead_id=lead_ctx, cancelado=es_pedido_cancelado)

    resumen = ", ".join(
        f"{qtys[i] if i < len(qtys) else '?'} x {names[i]}" for i in range(len(names))
    )
    descripcion = " - ".join(p for p in (descripcion_corta, mensaje, resumen) if p and p.strip()) or "Pedido vía WhatsApp"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Only the FRESH auto-created lead (No atendido, sin productos) is enrichable. A lead that
            # already carries an order, was advanced by an advisor, or is DELIVERED ("Pedidos
            # entregados") is immutable -> a new order opens a brand-new lead (pedidos separados).
            fresh_lead: Optional[int] = None
            if lead_ctx:
                lead = await _get_lead(client, lead_ctx)
                if lead is not None and _is_fresh_for_order(lead):
                    fresh_lead = lead_ctx
                elif lead is not None:
                    log.info("registrar_pedido_lead_locked", stage=_lead_stage_name(lead),
                             entregado=_is_delivered(lead))

            # Cancellation only applies to the fresh lead being built now; a concreted/delivered order
            # cannot be cancelled from here.
            if es_pedido_cancelado:
                if fresh_lead is None:
                    log.info("registrar_pedido_cancel_no_editable_lead")
                    return json.dumps(
                        {"lead_id": lead_ctx, "cancelado": False, "note": "sin_pedido_editable"},
                        ensure_ascii=False,
                    )
                try:
                    await _add_lead_note(
                        client, fresh_lead, "Pedido cancelado",
                        [("Cliente", nombre), ("Ciudad", ciudad_del_cliente), ("Detalle", mensaje)],
                    )
                    await _move_lead(client, fresh_lead, ciudad_del_cliente, "cancelado")
                except Exception as e:  # noqa: BLE001
                    log.warning("registrar_pedido_cancel_note_failed", lead_id=fresh_lead, error=str(e))
                log.info("kohlberg_pedido_cancelado", lead_id=fresh_lead)
                return json.dumps(
                    {"lead_id": fresh_lead, "solicitud": f"#{fresh_lead}", "cancelado": True},
                    ensure_ascii=False,
                )

            # Order registration: reuse the fresh lead, else open a NEW lead in the city pipeline so a
            # registered/delivered order is never edited (separate orders => separate leads).
            if fresh_lead is not None:
                lead_id: Optional[int] = fresh_lead
                nuevo = False
            else:
                wa = _ctx_wa_id(config)
                person_id = person_ctx or await _resolve_person(client, wa, nombre)
                lead_id = await _create_lead(
                    client, person_id, wa, nombre, titulo_de_pedido, descripcion, ciudad_del_cliente,
                )
                nuevo = True
            if not lead_id:
                log.error("registrar_pedido_no_lead_id")
                return json.dumps({"lead_id": None, "error": "no_lead_id"}, ensure_ascii=False)

            # Best-effort enrichment - a failure here must not lose the lead.
            adjuntados = 0
            for i, pid in enumerate(ids):
                name_i = names[i] if i < len(names) else ""
                qty_i = qtys[i] if i < len(qtys) else 1
                try:
                    await _add_lead_product(client, lead_id, int(pid), name_i, int(qty_i))
                    adjuntados += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("registrar_pedido_product_failed", lead_id=lead_id, product_id=pid, error=str(e))
            try:
                await _add_lead_note(
                    client, lead_id, "Datos del pedido",
                    [
                        ("Cliente", nombre),
                        ("Edad", edad_del_cliente),
                        ("Ciudad", ciudad_del_cliente),
                        ("Ubicación", ubicacion_del_cliente),
                        ("Pedido", resumen),
                        ("Detalle", mensaje),
                        ("Confirmado", "sí" if es_pedido_confirmado else "no"),
                    ],
                )
            except Exception as e:  # noqa: BLE001
                log.warning("registrar_pedido_note_failed", lead_id=lead_id, error=str(e))
            # On confirmation, advance the lead to the city's "Confirmado" stage (this also anchors it
            # in the right pipeline). Not tagged: tags here are delivery type, not order state.
            if es_pedido_confirmado:
                try:
                    await _move_lead(client, lead_id, ciudad_del_cliente, "confirmado")
                except Exception as e:  # noqa: BLE001
                    log.warning("registrar_pedido_move_failed", lead_id=lead_id, error=str(e))

            log.info(
                "kohlberg_pedido_registered",
                lead_id=lead_id,
                lineas=len(ids),
                adjuntados=adjuntados,
                confirmado=es_pedido_confirmado,
                nuevo_lead=nuevo,
            )
            return json.dumps(
                {"lead_id": lead_id, "solicitud": f"#{lead_id}", "productos_adjuntados": adjuntados,
                 "confirmado": es_pedido_confirmado, "nuevo_lead": nuevo},
                ensure_ascii=False,
            )
    except httpx.HTTPStatusError as e:
        body_text = e.response.text[:800] if e.response is not None else ""
        log.exception("registrar_pedido_http_error", status=e.response.status_code, body=body_text)
        return json.dumps({"lead_id": None, "error": f"api_{e.response.status_code}"}, ensure_ascii=False)
    except Exception as e:
        log.exception("registrar_pedido_failed", error=str(e))
        return json.dumps({"lead_id": None, "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def get_pedidos(config: RunnableConfig) -> str:
    """Consulta el historial de pedidos del contacto (sus leads con productos) en el CRM.

    Úsala cuando el cliente pregunta por sus pedidos anteriores o por el estado de su pedido. No manejas
    ids: el contacto se toma del contexto. Solo devuelve leads que ya tienen un pedido (con productos);
    ignora los leads vacíos "No atendido". Cada pedido trae su etapa (p. ej. "Pedidos entregados"), sus
    productos y su fecha. No modifica nada: es solo lectura.

    Args:
        config: Interno; lo inyecta el sistema. No lo pases.

    Devuelve {"total": <n>, "pedidos": [{lead_id, titulo, etapa, entregado, productos, fecha}]}.
    """
    _, person_id = _ctx_ids(config)
    person_id_int = _to_int(person_id)
    log = logger.bind(tool="get_pedidos", person_id=person_id)
    if not person_id_int:
        return json.dumps({"pedidos": [], "total": 0, "note": "sin_contacto"}, ensure_ascii=False)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await _request(
                client,
                "GET",
                "/api/v1/leads/get",
                params={"search": f"person_id:{person_id_int};", "searchFields": "person_id:=;", "limit": 50},
            )
            leads = _data_list(resp)
        pedidos: list[dict[str, Any]] = []
        for lead in leads:
            # The server-side person filter isn't guaranteed, so narrow client-side: keep the lead only
            # if its person matches (or the person is unknown on the record - then trust the search).
            lead_person = _lead_person_id(lead)
            if lead_person is not None and lead_person != person_id_int:
                continue
            items = _lead_line_items(lead)
            if not items:  # skip empty auto-created leads with no order
                continue
            pedidos.append(
                {
                    "lead_id": lead.get("id"),
                    "titulo": lead.get("title"),
                    "etapa": _lead_stage_display(lead),
                    "entregado": _is_delivered(lead),
                    "productos": items,
                    "fecha": lead.get("created_at"),
                }
            )
        log.info("kohlberg_get_pedidos_ok", leads_recibidos=len(leads), pedidos=len(pedidos))
        return json.dumps({"pedidos": pedidos, "total": len(pedidos)}, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        log.warning("kohlberg_get_pedidos_http_error", status=e.response.status_code)
        return json.dumps({"pedidos": [], "error": f"api_{e.response.status_code}"}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        log.exception("kohlberg_get_pedidos_failed", error=str(e))
        return json.dumps({"pedidos": [], "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def think(pensamiento: str) -> str:
    """Verifica la coherencia del flujo antes de responder (borrador interno, no lo ve el cliente).

    Úsala para razonar en silencio: comprobar que no repites un paso ya avanzado, que no contradices
    datos previos del cliente, que los vinos/precios salen de get_promos y que respetas las reglas
    (máx. 3 vinos por respuesta, no combinar el paso de pedido con el de sucursal, etc.). No realiza
    ninguna acción externa.

    Args:
        pensamiento: Tu razonamiento sobre el estado del flujo y el siguiente paso.
    """
    return json.dumps({"ok": True}, ensure_ascii=False)
