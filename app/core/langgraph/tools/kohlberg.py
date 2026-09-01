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

# Canonical city -> sales rep user id (lead owner). The order lead is assigned to the city's rep.
_CITY_SALES_REP: dict[str, int] = {
    "tarija": 14,
    "la paz": 4,
    "santa cruz": 9,
    "oruro": 3,
    "sucre": 7,
    "potosi": 8,
    "cochabamba": 15,
}
_DEFAULT_SALES_REP = _OWNER_USER_ID  # no/unrecognised city -> default owner

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


def _city_sales_rep(ciudad: Optional[str]) -> int:
    """Sales-rep user id (lead owner) for a free-text city; default owner when unrecognised."""
    key = _resolve_city_key(ciudad)
    return _CITY_SALES_REP.get(key, _DEFAULT_SALES_REP) if key else _DEFAULT_SALES_REP


def _to_int(valor: Any) -> Optional[int]:
    """Coerce to int like JS Number() for the enable/type flags; None when not an integer."""
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


def _to_float(valor: Any) -> Optional[float]:
    """Coerce to float; None when not a number (e.g. an empty precio_promocion string)."""
    try:
        return float(str(valor).strip())
    except (TypeError, ValueError):
        return None


def _product_price(prod: dict[str, Any]) -> float:
    """Effective unit price of a catalog product: the promo price if set, else the base price."""
    promo = _to_float(prod.get("precio_promocion"))
    if promo and promo > 0:
        return promo
    return _to_float(prod.get("price")) or 0.0


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


def _is_fresh_for_order(lead: dict[str, Any]) -> bool:
    """A lead is enrichable only while it is the untouched auto-created lead AND has no order yet.

    Anything else - an advisor already advanced it, it was delivered ('Pedidos entregados'), or it
    already carries a registered order - is left intact; a new order goes to a brand-new lead.
    """
    return _is_unattended(lead) and not _lead_has_products(lead)


def _build_products_map(
    ids: list[Any],
    names: list[Any],
    qtys: list[Any],
    price_by_id: dict[int, float],
) -> tuple[dict[str, dict[str, Any]], float]:
    """Build Krayin's `products` object ({product_0: {...}, ...}) and order total.

    Logs every transformation so product mapping problems can be diagnosed.

    Expected parallel arrays:
        ids[i]   -> product id
        names[i] -> product name
        qtys[i]  -> product quantity
    """
    log = logger.bind(
        method="_build_products_map",
        ids_count=len(ids),
        names_count=len(names),
        qtys_count=len(qtys),
        catalog_prices_count=len(price_by_id),
    )

    # LOG 1: Datos completos de entrada.
    log.info(
        "kohlberg_products_map_start",
        ids=ids,
        names=names,
        qtys=qtys,
        price_by_id=price_by_id,
    )

    # LOG 2: Detectar inmediatamente si los arrays paralelos no coinciden.
    if not (len(ids) == len(names) == len(qtys)):
        log.warning(
            "kohlberg_products_map_array_length_mismatch",
            ids_count=len(ids),
            names_count=len(names),
            qtys_count=len(qtys),
            ids=ids,
            names=names,
            qtys=qtys,
        )

    products: dict[str, dict[str, Any]] = {}
    total = 0.0

    for i, pid in enumerate(ids):

        raw_id = pid
        raw_name = names[i] if i < len(names) else None
        raw_qty = qtys[i] if i < len(qtys) else None

        # LOG 3: Datos originales de esta posición.
        log.info(
            "kohlberg_products_map_item_input",
            index=i,
            raw_product_id=raw_id,
            raw_name=raw_name,
            raw_quantity=raw_qty,
        )

        pid_int = _to_int(pid)

        # Si el ID no es válido, actualmente el producto se pierde.
        if pid_int is None:
            log.warning(
                "kohlberg_products_map_invalid_product_id",
                index=i,
                raw_product_id=raw_id,
                raw_name=raw_name,
                raw_quantity=raw_qty,
                action="skipped",
            )
            continue

        # Nombre
        if i < len(names) and names[i] not in (None, ""):
            name_i = str(names[i]).strip()
        else:
            name_i = ""

        if not name_i:
            log.warning(
                "kohlberg_products_map_missing_name",
                index=i,
                product_id=pid_int,
                available_names_count=len(names),
            )

        # Cantidad
        qty_raw_int = _to_int(raw_qty)
        qty_i = qty_raw_int if qty_raw_int and qty_raw_int > 0 else 1

        if qty_raw_int is None or qty_raw_int <= 0:
            log.warning(
                "kohlberg_products_map_invalid_quantity_defaulted",
                index=i,
                product_id=pid_int,
                raw_quantity=raw_qty,
                resolved_quantity=qty_i,
            )

        # Precio
        price_found = pid_int in price_by_id
        raw_price = price_by_id.get(pid_int)

        if not price_found:
            log.error(
                "kohlberg_products_map_price_not_found",
                index=i,
                product_id=pid_int,
                available_product_ids=list(price_by_id.keys())[:100],
                action="using_zero_price",
            )

        unit = round(_to_float(raw_price) or 0.0, 2)

        # LOG 4: Producto después de normalizar los datos.
        log.info(
            "kohlberg_products_map_item_resolved",
            index=i,
            product_id=pid_int,
            name=name_i,
            quantity=qty_i,
            price_found=price_found,
            raw_price=raw_price,
            unit_price=unit,
            subtotal=round(unit * qty_i, 2),
        )

        product_key = f"product_{len(products)}"

        mapped_product = {
            "name": name_i,
            "product_id": str(pid_int),
            "price": f"{unit:.2f}",
            "quantity": qty_i,
        }

        products[product_key] = mapped_product

        total += unit * qty_i

        # LOG 5: Ver exactamente cómo está quedando el objeto acumulado.
        log.info(
            "kohlberg_products_map_item_added",
            index=i,
            product_key=product_key,
            mapped_product=mapped_product,
            running_total=round(total, 2),
            products_so_far=products,
        )

    total = round(total, 2)

    # LOG 6: RESULTADO FINAL EXACTO.
    log.info(
        "kohlberg_products_map_complete",
        input_ids_count=len(ids),
        mapped_products_count=len(products),
        skipped_products_count=len(ids) - len(products),
        products=products,
        total=total,
    )

    return products, total

def _build_lead_body(
    person_id: Optional[int],
    wa_id: str,
    nombre: Optional[str],
    titulo: Optional[str],
    descripcion: str,
    ciudad: Optional[str],
    stage_key: str,
    total: float,
    products: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build the FULL lead object for create/update."""
    stages = _city_stages(ciudad)
    etiqueta = (nombre or "Cliente").strip()

    # Aseguramos que total sea un número decimal válido.
    total_decimal = round(float(total or 0), 2)

    person: dict[str, Any] = {
        "name": _clean_name(nombre),
    }

    if person_id:
        person["id"] = person_id
    else:
        person["contact_numbers"] = [
            {
                "value": wa_id,
                "label": "work",
            }
        ]

    body: dict[str, Any] = {
        "title": (titulo or f"Pedido Club del Vino - {etiqueta}").strip(),
        "description": descripcion or "Pedido vía WhatsApp",

        # IMPORTANTE: enviar como número, no como string.
        "lead_value": total_decimal,

        "lead_source_id": _SOURCE_WHATSAPP,
        "lead_type_id": _LEAD_TYPE_VENTA,
        "user_id": _city_sales_rep(ciudad),
        "lead_pipeline_id": stages["pipeline"],
        "lead_pipeline_stage_id": stages.get(
            stage_key,
            stages["no_atendido"],
        ),
        "person": person,
        "entity_type": "leads",
    }

    if products:
        body["products"] = products

    logger.info(
        "kohlberg_build_lead_body",
        person_id=person_id,
        total_input=total,
        total_decimal=total_decimal,
        lead_value=body["lead_value"],
        lead_value_type=type(body["lead_value"]).__name__,
        products_count=len(products),
        products=products,
        body=body,
    )

    return body


async def _upsert_lead(
    client: httpx.AsyncClient, lead_id: Optional[int], body: dict[str, Any]
) -> Optional[int]:
    """PUT the full body to an existing lead, or POST a new one. Returns the lead id."""
    if lead_id:
        await _request(client, "PUT", f"/api/v1/leads/{lead_id}", json=body)
        return lead_id
    resp = await _request(client, "POST", "/api/v1/leads", json=body)
    data = _data(resp)
    return data.get("id") if isinstance(data, dict) else None


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


async def _move_lead(
    client: httpx.AsyncClient,
    lead_id: int,
    ciudad: Optional[str],
    stage_key: str,
    lead_value: Optional[float] = None,
) -> None:
    """Move the lead to the given stage of the city's pipeline and, optionally, set its lead_value.

    Uses the real per-city pipeline + stage ids. Never touches tags: a tag here means delivery type, so
    tagging with a wrong id mislabels the order (e.g. as 'Delivery' when it's pickup-only). Krayin
    accepts this partial PUT (same shape the IMPRIMIR agent uses in production).
    """
    stages = _city_stages(ciudad)
    body: dict[str, Any] = {"lead_pipeline_id": stages["pipeline"]}
    stage_id = stages.get(stage_key)
    if stage_id is not None:
        body["lead_pipeline_stage_id"] = stage_id
    if lead_value is not None:
        body["lead_value"] = str(round(lead_value, 2))
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
    config: RunnableConfig,
    mensaje: Optional[str] = None,
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
    """Registra el pedido del cliente como oportunidad (lead) en el CRM Kohlberg."""
    lead_ctx, person_ctx = _ctx_ids(config)

    log = logger.bind(
        tool="registrar_pedido",
        lead_id=lead_ctx,
        cancelado=es_pedido_cancelado,
    )

    log.info(
        "registrar_pedido_entered",
        mensaje=mensaje,
        product_id=product_id,
        product_name=product_name,
        cantidad_product=cantidad_product,
        nombre_del_cliente=nombre_del_cliente,
        ciudad_del_cliente=ciudad_del_cliente,
        es_pedido_confirmado=es_pedido_confirmado,
        es_pedido_cancelado=es_pedido_cancelado,
    )

    nombre = (nombre_del_cliente or "").strip() or _ctx_contact_name(config)
    ids = list(product_id or [])
    names = list(product_name or [])
    qtys = list(cantidad_product or [])

    resumen = ", ".join(
        f"{qtys[i] if i < len(qtys) else '?'} x {names[i]}"
        for i in range(len(names))
    )
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

            # Real unit prices from the (cached) catalog, so the product lines and lead_value reflect
            # the order - the LLM never passes prices (it could hallucinate them).
            price_by_id: dict[int, float] = {}
            try:
                for p in await _get_products_cached():
                    pidc = _to_int(p.get("id"))
                    if pidc is not None:
                        price_by_id[pidc] = _product_price(p)
            except Exception as e:  # noqa: BLE001
                log.warning("registrar_pedido_price_lookup_failed", error=str(e))

            products_map, total = _build_products_map(ids, names, qtys, price_by_id)

            # Fold all client/order detail into the lead description so we DON'T need a separate note
            # activity - one fewer CRM call per order (matters against the 429 throttle).
            detalle = [
                f"Cliente: {nombre}" if nombre else None,
                f"Edad: {edad_del_cliente}" if edad_del_cliente else None,
                f"Ciudad: {ciudad_del_cliente}" if ciudad_del_cliente else None,
                f"Ubicación: {ubicacion_del_cliente}" if ubicacion_del_cliente else None,
                f"Pedido: {resumen}" if resumen else None,
                f"Total: Bs {total:.2f}" if total else None,
                descripcion_corta or None,
                mensaje or None,
            ]
            descripcion = " | ".join(x for x in detalle if x) or "Pedido vía WhatsApp"

            # Person for the lead body: from context, else find/create by wa_id.
            person_id = person_ctx
            if person_id is None:
                person_id = await _resolve_person(client, _ctx_wa_id(config), nombre)

            # ONE full-object write does everything: product lines (inline), lead_value, the city's
            # pipeline + stage (Confirmado on confirm, else No atendido) and the city's sales rep as
            # owner. Krayin only fills the product lines/value from the WHOLE object (a partial PUT or
            # the /leads/product endpoint did not) - so reuse the fresh lead with a full PUT, or POST a
            # new one for a separate order.
            stage_key = "confirmado" if es_pedido_confirmado else "no_atendido"
            body = _build_lead_body(
                person_id, _ctx_wa_id(config), nombre, titulo_de_pedido, descripcion,
                ciudad_del_cliente, stage_key, total, products_map,
            )
            lead_id = await _upsert_lead(client, fresh_lead, body)
            nuevo = fresh_lead is None
            if not lead_id:
                log.error("registrar_pedido_no_lead_id")
                return json.dumps({"lead_id": None, "error": "no_lead_id"}, ensure_ascii=False)

            log.info(
                "kohlberg_pedido_registered",
                lead_id=lead_id,
                lineas=len(products_map),
                total=total,
                user_id=_city_sales_rep(ciudad_del_cliente),
                confirmado=es_pedido_confirmado,
                nuevo_lead=nuevo,
            )
            return json.dumps(
                {"lead_id": lead_id, "solicitud": f"#{lead_id}", "productos_registrados": len(products_map),
                 "total": total, "confirmado": es_pedido_confirmado, "nuevo_lead": nuevo},
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
    """Consulta TODOS los pedidos del cliente por su número de teléfono.

    Usa GET /api/pedidos/por-telefono y devuelve todos los pedidos en una
    sola llamada.
    """
    telefono = _ctx_wa_id(config)
    metadata = (config or {}).get("metadata") or {}
    digits = "".join(c for c in telefono if c.isdigit())

    log = logger.bind(
        tool="get_pedidos",
        telefono_original=telefono,
        telefono_digits=digits,
        metadata_keys=list(metadata.keys()),
        wa_id_raw=metadata.get("wa_id"),
        person_id=metadata.get("person_id"),
    )

    # LOG 1: confirmar qué información llega realmente al tool
    log.info(
        "kohlberg_get_pedidos_start",
        telefono_original=telefono,
        telefono_digits=digits,
        metadata_keys=list(metadata.keys()),
        wa_id_raw=metadata.get("wa_id"),
    )

    if len(digits) < 7:
        resultado = {
            "telefono": telefono or None,
            "persona": None,
            "pedidos": [],
            "total": 0,
            "note": "sin_telefono_valido",
        }

        log.warning(
            "kohlberg_get_pedidos_invalid_phone",
            resultado=resultado,
        )

        return json.dumps(resultado, ensure_ascii=False)

    try:
        async with httpx.AsyncClient(timeout=20) as client:

            # LOG 2: antes de realizar la petición
            log.info(
                "kohlberg_get_pedidos_request",
                method="GET",
                url=f"{_BASE}/api/pedidos/por-telefono",
                params={"telefono": digits},
            )

            resp = await _request(
                client,
                "GET",
                "/api/pedidos/por-telefono",
                params={"telefono": digits},
            )

            # LOG 3: respuesta HTTP
            log.info(
                "kohlberg_get_pedidos_response",
                status=resp.status_code,
                content_length=len(resp.content or b""),
                response_text=resp.text[:5000],
            )

            payload = resp.json() if resp.content else {}

        # LOG 4: payload ya parseado
        log.info(
            "kohlberg_get_pedidos_payload",
            payload=payload,
            payload_type=type(payload).__name__,
        )

        # El endpoint debería devolver directamente un objeto.
        if not isinstance(payload, dict):
            log.warning(
                "kohlberg_get_pedidos_invalid_response",
                response_type=type(payload).__name__,
                payload=payload,
            )

            resultado = {
                "telefono": digits,
                "persona": None,
                "pedidos": [],
                "total": 0,
                "error": "respuesta_invalida",
            }

            log.info(
                "kohlberg_get_pedidos_output",
                resultado=resultado,
            )

            return json.dumps(resultado, ensure_ascii=False)

        persona = payload.get("persona")
        pedidos = payload.get("pedidos")
        total = payload.get("total")

        # LOG 5: inspeccionar específicamente los campos importantes
        log.info(
            "kohlberg_get_pedidos_fields",
            telefono_response=payload.get("telefono"),
            persona=persona,
            pedidos_type=type(pedidos).__name__,
            pedidos_count=len(pedidos) if isinstance(pedidos, list) else None,
            total_raw=total,
            payload_keys=list(payload.keys()),
        )

        if not isinstance(pedidos, list):
            log.warning(
                "kohlberg_get_pedidos_pedidos_not_list",
                pedidos_value=pedidos,
                pedidos_type=type(pedidos).__name__,
            )
            pedidos = []

        total_int = _to_int(total)

        # Si el endpoint no manda total correctamente, usamos la cantidad real.
        if total_int is None:
            total_int = len(pedidos)

        resultado = {
            "telefono": payload.get("telefono") or digits,
            "persona": persona if isinstance(persona, dict) else None,
            "pedidos": pedidos,
            "total": total_int,
        }

        # LOG 6: RESULTADO FINAL EXACTO QUE SALE DEL TOOL
        log.info(
            "kohlberg_get_pedidos_output",
            resultado=resultado,
            pedidos_count=len(pedidos),
            total=total_int,
        )

        return json.dumps(resultado, ensure_ascii=False)

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        retry_after = e.response.headers.get("Retry-After")

        body_text = e.response.text[:5000] if e.response is not None else ""

        log.warning(
            "kohlberg_get_pedidos_http_error",
            status=status,
            retry_after=retry_after,
            response_body=body_text,
            telefono=digits,
        )

        if status == 429:
            resultado: dict[str, Any] = {
                "telefono": digits,
                "persona": None,
                "pedidos": [],
                "total": 0,
                "error": "api_429",
            }

            if retry_after:
                resultado["retry_after"] = retry_after

            log.info(
                "kohlberg_get_pedidos_output",
                resultado=resultado,
            )

            return json.dumps(resultado, ensure_ascii=False)

        message: Optional[str] = None

        try:
            error_payload = e.response.json()

            log.warning(
                "kohlberg_get_pedidos_error_payload",
                error_payload=error_payload,
            )

            if isinstance(error_payload, dict):
                raw_message = error_payload.get("message")

                if isinstance(raw_message, str):
                    message = raw_message

        except Exception as parse_error:  # noqa: BLE001
            log.warning(
                "kohlberg_get_pedidos_error_parse_failed",
                error=str(parse_error),
                response_body=body_text,
            )

        resultado: dict[str, Any] = {
            "telefono": digits,
            "persona": None,
            "pedidos": [],
            "total": 0,
            "error": f"api_{status}",
        }

        if message:
            resultado["message"] = message

        log.info(
            "kohlberg_get_pedidos_output",
            resultado=resultado,
        )

        return json.dumps(resultado, ensure_ascii=False)

    except Exception as e:  # noqa: BLE001
        log.exception(
            "kohlberg_get_pedidos_failed",
            telefono=digits,
            error=str(e),
            error_type=type(e).__name__,
        )

        resultado = {
            "telefono": digits,
            "persona": None,
            "pedidos": [],
            "total": 0,
            "error": str(e) or type(e).__name__,
        }

        log.info(
            "kohlberg_get_pedidos_output",
            resultado=resultado,
        )

        return json.dumps(resultado, ensure_ascii=False)

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


# ── Handoff (signal only - the webhook POSTs after the client notice) ──────────

@tool
async def derivar_a_asesor(reason: str, ciudad: Optional[str] = None) -> str:
    """Deriva la conversación a un asesor humano de la ciudad del cliente.

    Úsala cuando el cliente pida hablar con una persona/asesor, esté molesto o repita un reclamo, o
    cuando la consulta exceda lo que podés resolver (reclamos por un pedido entregado, temas de pago,
    precios especiales, cambios sobre un pedido ya confirmado). El mensaje que escribís en ESTA misma
    respuesta es el aviso al cliente (breve, natural, sin prometer tiempos). Después de derivar NO
    vuelvas a escribirle: lo atiende una persona. No derives dos veces.

    Args:
        reason: Motivo en una frase (español) para que el asesor entienda el contexto sin leer el chat.
        ciudad: Ciudad del cliente SOLO si la sabés con certeza por la conversación (Tarija, Santa Cruz,
            La Paz, Cochabamba, Sucre, Potosí, Oruro). Omitila si no estás seguro; NO la deduzcas del
            código de área ni del nombre - una ciudad equivocada manda al cliente con el asesor
            equivocado. Sin ciudad, la conversación cae al pool del equipo (igual es válido).
    """
    # Pure signal: the actual POST /handoff is done by the caller AFTER the client notice is sent
    # (once derived the CRM 409s any further /messages, so order matters).
    return json.dumps(
        {"status": "handoff_signaled", "reason": reason, "ciudad": (ciudad or None)},
        ensure_ascii=False,
    )


async def request_handoff(
    conversation_id: int | str, reason: str, ciudad: Optional[str] = None
) -> dict[str, Any]:
    """Derive a conversation to an advisor: POST .../conversations/{id}/handoff {reason, ciudad?}.

    Not a tool - the webhook calls this AFTER sending the client notice (once derived the CRM 409s any
    further /messages, so order matters). The CRM routes to the city's Encargado (or the team pool);
    idempotent CRM-side (a re-request returns changed=false). `assigned_user` in the response is an
    INT (the id), never an object; `assigned_user_name` carries the name. Best-effort: logs and returns
    {} on failure, never raises.
    """
    log = logger.bind(conversation_id=conversation_id, ciudad=(ciudad or "")[:40])
    body: dict[str, Any] = {"reason": reason}
    if ciudad and ciudad.strip():
        body["ciudad"] = ciudad.strip()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await _request(
                client, "POST", f"/api/v1/whatsapp/conversations/{conversation_id}/handoff", json=body
            )
            payload = resp.json() if resp.content else {}
            handoff = payload.get("handoff") if isinstance(payload, dict) else None
            handoff = handoff if isinstance(handoff, dict) else {}
            log.info(
                "kohlberg_handoff_requested",
                state=handoff.get("state"),
                pooled=handoff.get("pooled"),
                city=handoff.get("city"),
                assigned_user=handoff.get("assigned_user"),
                changed=handoff.get("changed"),
            )
            return payload if isinstance(payload, dict) else {}
    except httpx.HTTPStatusError as e:
        log.error("kohlberg_handoff_http_error", status=e.response.status_code)
        return {}
    except Exception as e:  # noqa: BLE001
        log.exception("kohlberg_handoff_failed", error=str(e))
        return {}
