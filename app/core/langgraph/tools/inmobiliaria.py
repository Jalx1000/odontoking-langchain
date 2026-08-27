"""CENTURY 21 (Sofía) CRM tools — inmuebles, disponibilidad-less v1, leads and handoff.

Flow: WhatsApp → Krayin CRM (c21.sofopolis.com) → agent → CRM. The agent receives conversation_id +
wa_id; the ONLY identifier the LLM handles is the public property `codigo` (it appears in the listing
the client saw). conversation_id / person_id / lead_id are injected server-side via config.metadata
and never touch the LLM surface (weak models hallucinate ids → corrupt data).

Design note — one coarse tool per action, only `codigo` + plain fields. Every action (catalog, leads,
historial, handoff, media, location, telefono) lives under /api/v1/inmobiliaria/conversations/{id}/…
(verified against the live CRM). Base URL + Bearer come from CRM_BASE_URL / CRM_API_KEY (same pair the
reply gateway already uses successfully), NOT the impresión token.

v1 does NOT schedule visits: the agent captures a `visita_preferencia` (free text) and hands off to
the titular advisor, who coordinates the actual time. So there is no get_disponibilidad / agendar_visita
here on purpose.
"""

import json
from typing import Any, Optional

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import logger

_BASE = settings.CRM_BASE_URL
# Every conversation action lives under /api/v1/inmobiliaria/conversations/{id}/… (catalog, leads,
# historial, handoff, media, location, telefono) — verified against the live CRM instance.
_NS_INMO = "/api/v1/inmobiliaria"
_MEDIA_MAX = 8                           # hard cap per the CRM contract (asking for more returns 8)

_HEADERS = {
    "accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {settings.CRM_API_KEY}",
}


# ── Low-level HTTP ────────────────────────────────────────────────────────────

def _is_transient(exc: BaseException) -> bool:
    """Retry only on transient failures: 429, 5xx, network timeouts/connection errors."""
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
    """Call the CRM API with shared headers, retrying only on transient errors."""
    resp = await client.request(method, f"{_BASE}{path}", headers=_HEADERS, **kwargs)
    resp.raise_for_status()
    return resp


def _data(resp: httpx.Response) -> Any:
    """Return the payload's data list/dict (accepts 'resultados' | 'data' | raw)."""
    payload = resp.json() if resp.content else {}
    if isinstance(payload, dict):
        for key in ("resultados", "data"):
            if key in payload:
                return payload[key]
    return payload


def _ctx_conversation_id(config: Optional[RunnableConfig]) -> Optional[int]:
    """conversation_id injected via config.metadata (the LLM never passes it)."""
    metadata = (config or {}).get("metadata") or {}
    return metadata.get("conversation_id")


def _ctx_contact_name(config: Optional[RunnableConfig]) -> Optional[str]:
    """Best contact name from metadata (registered, else WhatsApp/Messenger profile), or None."""
    metadata = (config or {}).get("metadata") or {}
    name = metadata.get("nombre_registrado") or metadata.get("nombre_whatsapp")
    clean = name.strip() if isinstance(name, str) else ""
    return clean or None


def _phone_ask_allowed(
    phone_required: bool, phone_prompt_state: Optional[str], phone_prompt_exhausted: bool
) -> bool:
    """Code-owned gate for whether the agent may ASK for the phone (Messenger only).

    Asking is allowed only when the CRM flags the phone missing, the 3 attempts aren't exhausted, and it
    isn't already captured/refused. Capturing a number the client volunteers is ALWAYS allowed — this
    gates asking only. Absent/false `phone_required` reads as not-required.
    """
    if not phone_required or phone_prompt_exhausted:
        return False
    return (phone_prompt_state or "pending") not in ("captured", "refused")


# ── Catalog tools (codigo is public — the LLM handles it) ─────────────────────

@tool
async def buscar_inmuebles(
    operacion: str,
    tipo: str,
    zonas: Optional[list[str]] = None,
    precio_min: Optional[float] = None,
    precio_max: Optional[float] = None,
    moneda: Optional[str] = None,
    dormitorios_min: Optional[int] = None,
    m2_terreno_min: Optional[int] = None,
    m2_construidos_min: Optional[int] = None,
    parqueos_min: Optional[int] = None,
    limite: int = 3,
) -> str:
    """Busca inmuebles disponibles en la cartera de CENTURY 21. Única fuente de verdad.

    Devuelve solo disponibles (hasta 3 por defecto; subí `limite` si el cliente pide ver más). La
    oficina trabaja una sola plaza: NO se pregunta la ciudad. Para un lote usá `m2_terreno_min` (no
    dormitorios). Cada resultado trae su `codigo` público, precio con su condición y una línea destacada.

    Args:
        operacion: "venta" | "alquiler" | "anticretico".
        tipo: "casa" | "departamento" | "quinta" | "galpon" | "lote" | "oficina" | "local" | "otro".
        zonas: Zonas amplias (p. ej. ["Zona Norte"]). El detalle fino (anillo) va en notas del lead.
        precio_min: Precio mínimo.
        precio_max: Precio máximo.
        moneda: "USD" | "BOB".
        dormitorios_min: Mínimo de dormitorios (para vivienda).
        m2_terreno_min: Superficie de terreno mínima (para lotes/quintas).
        m2_construidos_min: Superficie construida mínima.
        parqueos_min: Mínimo de parqueos.
        limite: Cantidad máxima de resultados (por defecto 3).
    """
    params: dict[str, Any] = {"operacion": operacion, "tipo": tipo, "limite": limite}
    if zonas:
        params["zonas[]"] = zonas
    for key, val in (
        ("precio_min", precio_min), ("precio_max", precio_max), ("moneda", moneda),
        ("dormitorios_min", dormitorios_min), ("m2_terreno_min", m2_terreno_min),
        ("m2_construidos_min", m2_construidos_min), ("parqueos_min", parqueos_min),
    ):
        if val is not None:
            params[key] = val
    log = logger.bind(operacion=operacion, tipo=tipo)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await _request(client, "GET", f"{_NS_INMO}/inmuebles", params=params)
            data = _data(resp)
            resultados = data if isinstance(data, list) else []
            log.info("c21_buscar_inmuebles", count=len(resultados))
            return json.dumps({"resultados": resultados, "total": len(resultados)}, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        log.warning("c21_buscar_inmuebles_http_error", status=e.response.status_code)
        return json.dumps({"resultados": [], "error": f"api_{e.response.status_code}"}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        log.exception("c21_buscar_inmuebles_failed", error=str(e))
        return json.dumps({"resultados": [], "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def get_inmueble(codigo: str) -> str:
    """Ficha completa de un inmueble por su `codigo`.

    Trae descripción, características, precio con su condición publicada, asesor titular y punto de
    encuentro. Usala para presentar el inmueble en detalle (la búsqueda solo trae una línea destacada).

    Args:
        codigo: Código público del inmueble (p. ej. "C21-0002").
    """
    log = logger.bind(codigo=codigo)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await _request(client, "GET", f"{_NS_INMO}/inmuebles/{codigo}")
            data = _data(resp)
            log.info("c21_get_inmueble_ok")
            return json.dumps({"inmueble": data}, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        log.warning("c21_get_inmueble_http_error", status=e.response.status_code)
        return json.dumps({"inmueble": None, "error": f"api_{e.response.status_code}"}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        log.exception("c21_get_inmueble_failed", error=str(e))
        return json.dumps({"inmueble": None, "error": str(e) or type(e).__name__}, ensure_ascii=False)


# ── Messaging tools (send media / location to the client) ─────────────────────

@tool
async def enviar_media(
    codigo: str, config: RunnableConfig, tipo: str = "foto", cantidad: int = 4
) -> str:
    """Envía al cliente los archivos de un inmueble (fotos, plano, video o tour).

    Usala SOLO después de confirmar con `get_inmueble` que el inmueble tiene ese material
    (campos `tiene_fotos` / `tiene_plano` / `tiene_tour`) — si no, promete algo que no puede cumplir.
    La galería vive en el CRM: elige los archivos y el orden (portada primero, caption solo en la
    primera). El agente nunca ve URLs. Para una primera muestra 3-4 fotos alcanzan (tope duro 8).

    Args:
        codigo: Código público del inmueble.
        config: Interno; lo inyecta el sistema. No lo pases.
        tipo: "foto" | "plano" | "video" | "tour" (por defecto "foto").
        cantidad: Cuántos archivos enviar (por defecto 4, máximo 8).
    """
    conversation_id = _ctx_conversation_id(config)
    log = logger.bind(conversation_id=conversation_id, codigo=codigo, tipo=tipo)
    if not conversation_id:
        return json.dumps({"enviado": False, "error": "no_conversation_id"}, ensure_ascii=False)
    body = {"codigo": codigo, "tipo": tipo, "cantidad": max(1, min(int(cantidad or 4), _MEDIA_MAX))}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await _request(
                client, "POST", f"{_NS_INMO}/conversations/{conversation_id}/media", json=body
            )
            data = resp.json() if resp.content else {}
            enviados = data.get("enviados", body["cantidad"]) if isinstance(data, dict) else body["cantidad"]
            log.info("c21_enviar_media_ok", enviados=enviados)
            return json.dumps({"enviado": True, "enviados": enviados, "codigo": codigo}, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        # Non-transient statuses (_request already retried 5xx/429): map each to a result Sofía reacts
        # to. 404 = no material of that type (o código inexistente) → decírselo, NO reintentar; 409 =
        # derivada a un asesor → callarse; 422 = fuera de la ventana 24h; 501 = canal sin media.
        status = e.response.status_code
        try:
            mensaje = (e.response.json() or {}).get("message", "") if e.response.content else ""
        except Exception:  # noqa: BLE001
            mensaje = ""
        log.warning("c21_enviar_media_http_error", status=status, mensaje=mensaje[:120])
        if status == 404:
            return json.dumps(
                {"enviado": False, "motivo": "sin_material",
                 "mensaje": mensaje or "el inmueble no tiene ese material cargado", "codigo": codigo},
                ensure_ascii=False,
            )
        if status == 409:
            return json.dumps({"enviado": False, "motivo": "derivada"}, ensure_ascii=False)
        if status == 422:
            return json.dumps({"enviado": False, "motivo": "ventana_cerrada"}, ensure_ascii=False)
        if status == 501:
            return json.dumps({"enviado": False, "motivo": "canal_sin_media"}, ensure_ascii=False)
        return json.dumps({"enviado": False, "error": f"api_{status}"}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        log.exception("c21_enviar_media_failed", error=str(e))
        return json.dumps({"enviado": False, "error": str(e) or type(e).__name__}, ensure_ascii=False)


@tool
async def enviar_ubicacion(codigo: str, config: RunnableConfig) -> str:
    """Envía la ubicación exacta de un inmueble.

    Solo funciona DESPUÉS de derivar al asesor (si no, el CRM responde 409). En v1 esto entra recién
    al final del flujo, si entra.

    Args:
        codigo: Código público del inmueble.
        config: Interno; lo inyecta el sistema. No lo pases.
    """
    conversation_id = _ctx_conversation_id(config)
    log = logger.bind(conversation_id=conversation_id, codigo=codigo)
    if not conversation_id:
        return json.dumps({"enviado": False, "error": "no_conversation_id"}, ensure_ascii=False)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await _request(
                client, "POST", f"{_NS_INMO}/conversations/{conversation_id}/location", json={"codigo": codigo}
            )
            log.info("c21_enviar_ubicacion_ok")
            return json.dumps({"enviado": True}, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        log.warning("c21_enviar_ubicacion_http_error", status=e.response.status_code)
        return json.dumps({"enviado": False, "error": f"api_{e.response.status_code}"}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        log.exception("c21_enviar_ubicacion_failed", error=str(e))
        return json.dumps({"enviado": False, "error": str(e) or type(e).__name__}, ensure_ascii=False)


# ── Lead tools (UPSERT per conversation — the CRM dedups by conversation_id) ───

async def _post_conversation(path: str, body: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """POST to /inmobiliaria/conversations/{id}/<path>, filling nombre from context when absent.

    Shared by solicitud/captacion/postventa. Returns the CRM json (with lead_id/referencia) or an
    {"error": ...} dict; never raises — a lead failure must not break the reply.
    """
    conversation_id = _ctx_conversation_id(config)
    log = logger.bind(conversation_id=conversation_id, accion=path)
    if not conversation_id:
        log.warning("c21_lead_no_conversation_id")
        return {"lead_id": None, "error": "no_conversation_id"}
    if not (body.get("nombre") or "").strip():
        body["nombre"] = _ctx_contact_name(config) or ""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await _request(
                client, "POST", f"{_NS_INMO}/conversations/{conversation_id}/{path}", json=body
            )
            data = resp.json() if resp.content else {}
            data = data if isinstance(data, dict) else {}
            log.info("c21_lead_registered", lead_id=data.get("lead_id"))
            return data
    except httpx.HTTPStatusError as e:
        log.warning("c21_lead_http_error", status=e.response.status_code)
        return {"lead_id": None, "error": f"api_{e.response.status_code}"}
    except Exception as e:  # noqa: BLE001
        log.exception("c21_lead_failed", error=str(e))
        return {"lead_id": None, "error": str(e) or type(e).__name__}


@tool
async def registrar_solicitud(
    operacion: str,
    tipo: str,
    config: RunnableConfig,
    zonas: Optional[list[str]] = None,
    presupuesto: Optional[float] = None,
    moneda: Optional[str] = None,
    dormitorios_min: Optional[int] = None,
    temperatura: str = "tibio",
    codigos_interes: Optional[list[str]] = None,
    visita_preferencia: Optional[str] = None,
    notas: Optional[str] = None,
    nombre: Optional[str] = None,
) -> str:
    """Registra o actualiza el interés del cliente (lead comprador/inquilino).

    UPSERT por conversación: llamala apenas tengas algo que registrar y actualizala con cada dato
    nuevo — NO crea duplicados, es la misma solicitud de esta conversación. Toda conversación debe
    quedar registrada.

    Args:
        operacion: "venta" | "alquiler" | "anticretico".
        tipo: Tipo de inmueble buscado.
        config: Interno; lo inyecta el sistema. No lo pases.
        zonas: Zonas de interés (amplias).
        presupuesto: Presupuesto aproximado del cliente.
        moneda: "USD" | "BOB".
        dormitorios_min: Dormitorios mínimos.
        temperatura: "caliente" | "tibio" | "frio" (alerta interna; no la infles).
        codigos_interes: Códigos de inmuebles que le interesaron.
        visita_preferencia: Día/horario que el cliente prefiere para la visita (texto libre).
        notas: Detalle en texto libre (p. ej. "6to a 9no anillo", característica indispensable).
        nombre: Nombre del cliente (si no lo pasás, se toma del contexto).
    """
    body: dict[str, Any] = {
        "nombre": nombre, "operacion": operacion, "tipo": tipo,
        "zonas": zonas or [], "presupuesto": presupuesto, "moneda": moneda,
        "dormitorios_min": dormitorios_min, "temperatura": temperatura,
        "codigos_interes": codigos_interes or [], "visita_preferencia": visita_preferencia,
        "notas": notas,
    }
    data = await _post_conversation("solicitud", body, config)
    lead_id = data.get("lead_id")
    return json.dumps(
        {"lead_id": lead_id, "referencia": data.get("referencia") or (f"Solicitud #{lead_id}" if lead_id else None),
         **({"error": data["error"]} if data.get("error") else {})},
        ensure_ascii=False,
    )


@tool
async def registrar_captacion(
    tipo: str,
    zona: str,
    config: RunnableConfig,
    superficie: Optional[str] = None,
    dormitorios: Optional[int] = None,
    precio_esperado: Optional[float] = None,
    moneda: Optional[str] = None,
    estado_documental: Optional[str] = None,
    motivo: Optional[str] = None,
    detalle: Optional[str] = None,
    nombre: Optional[str] = None,
) -> str:
    """Registra a un PROPIETARIO que quiere listar su inmueble (lead de captación).

    No lo trates como comprador. No tases ni cotices comisión — eso lo ve el asesor.

    Args:
        tipo: Tipo de inmueble.
        zona: Zona del inmueble.
        config: Interno; lo inyecta el sistema. No lo pases.
        superficie: Superficie (texto libre).
        dormitorios: Dormitorios.
        precio_esperado: Precio esperado por el propietario.
        moneda: "USD" | "BOB".
        estado_documental: Si los papeles están a su nombre, etc.
        motivo: Por qué vende.
        detalle: Detalle adicional.
        nombre: Nombre del propietario (si no lo pasás, se toma del contexto).
    """
    body: dict[str, Any] = {
        "nombre": nombre, "tipo": tipo, "zona": zona, "superficie": superficie,
        "dormitorios": dormitorios, "precio_esperado": precio_esperado, "moneda": moneda,
        "estado_documental": estado_documental, "motivo": motivo, "detalle": detalle,
    }
    data = await _post_conversation("captacion", body, config)
    lead_id = data.get("lead_id")
    return json.dumps(
        {"lead_id": lead_id, "referencia": data.get("referencia") or (f"Solicitud #{lead_id}" if lead_id else None),
         **({"error": data["error"]} if data.get("error") else {})},
        ensure_ascii=False,
    )


@tool
async def registrar_postventa(
    detalle: str,
    config: RunnableConfig,
    codigo_o_contrato: Optional[str] = None,
    nombre: Optional[str] = None,
) -> str:
    """Registra la consulta de un cliente con una operación EN CURSO (postventa).

    Args:
        detalle: Descripción de la consulta.
        config: Interno; lo inyecta el sistema. No lo pases.
        codigo_o_contrato: Código de inmueble o nº de contrato, si lo tiene.
        nombre: Nombre del cliente (si no lo pasás, se toma del contexto).
    """
    body: dict[str, Any] = {"nombre": nombre, "detalle": detalle, "codigo_o_contrato": codigo_o_contrato}
    data = await _post_conversation("postventa", body, config)
    lead_id = data.get("lead_id")
    return json.dumps(
        {"lead_id": lead_id, "referencia": data.get("referencia") or (f"Solicitud #{lead_id}" if lead_id else None),
         **({"error": data["error"]} if data.get("error") else {})},
        ensure_ascii=False,
    )


@tool
async def get_historial(config: RunnableConfig) -> str:
    """Qué buscó y qué vio este contacto antes.

    Consultalo al inicio si el contacto no es nuevo, para no repreguntar. No manejás ids: la
    conversación se toma del contexto.

    Args:
        config: Interno; lo inyecta el sistema. No lo pases.
    """
    conversation_id = _ctx_conversation_id(config)
    log = logger.bind(conversation_id=conversation_id)
    if not conversation_id:
        return json.dumps({"historial": [], "error": "no_conversation_id"}, ensure_ascii=False)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await _request(client, "GET", f"{_NS_INMO}/conversations/{conversation_id}/historial")
            data = _data(resp)
            log.info("c21_get_historial_ok")
            return json.dumps({"historial": data if isinstance(data, list) else data}, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        log.warning("c21_get_historial_http_error", status=e.response.status_code)
        return json.dumps({"historial": [], "error": f"api_{e.response.status_code}"}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        log.exception("c21_get_historial_failed", error=str(e))
        return json.dumps({"historial": [], "error": str(e) or type(e).__name__}, ensure_ascii=False)


# ── Phone capture (Messenger) ─────────────────────────────────────────────────

@tool
async def guardar_telefono(
    config: RunnableConfig,
    telefono: Optional[str] = None,
    rechazado: bool = False,
) -> str:
    """Registra el teléfono que el cliente escribió, o su negativa a darlo (canal Messenger).

    Pasá el número TAL CUAL lo escribió (sin limpiarlo ni completar el código de país). Si lo dicta en
    palabras, no adivines: pedile que lo escriba y no llames esto. `rechazado=True` si no quiere darlo.

    Args:
        config: Interno; lo inyecta el sistema. No lo pases.
        telefono: El número tal cual lo escribió el cliente. Omitilo si rechazado=True.
        rechazado: True si el cliente no quiere dar el número.
    """
    conversation_id = _ctx_conversation_id(config)
    log = logger.bind(conversation_id=conversation_id)
    if not conversation_id:
        return json.dumps({"status": "error", "error": "no_conversation_id"}, ensure_ascii=False)
    if not rechazado and not (telefono or "").strip():
        return json.dumps({"status": "invalid", "repreguntar": True}, ensure_ascii=False)
    payload: dict[str, Any] = {"rechazado": True} if rechazado else {"telefono": telefono, "source": "ai"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await _request(
                client, "POST", f"{_NS_INMO}/conversations/{conversation_id}/telefono", json=payload
            )
            log.info("c21_guardar_telefono_ok", rechazado=rechazado)
            return json.dumps({"status": "ok", "rechazado": rechazado}, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        # 422 = número inválido, nada guardado → repreguntar una vez si quedan intentos.
        result = "invalid" if status == 422 else "error"
        log.warning("c21_guardar_telefono_http_error", status=status)
        return json.dumps(
            {"status": result, **({"repreguntar": True} if result == "invalid" else {})}, ensure_ascii=False
        )
    except Exception as e:  # noqa: BLE001
        log.exception("c21_guardar_telefono_failed", error=str(e))
        return json.dumps({"status": "error", "error": str(e) or type(e).__name__}, ensure_ascii=False)


# ── Handoff (signal only — the webhook POSTs after the client notice) ──────────

@tool
async def derivar_a_asesor(reason: str, codigo: Optional[str] = None) -> str:
    """Deriva la conversación al asesor TITULAR del inmueble (`codigo`).

    Si el cliente pide un asesor sin haber elegido inmueble, llamala sin `codigo` (va al asesor de
    guardia).

    Usala cuando el cliente pida hablar con una persona, pida rebaja/condiciones especiales, pregunte
    por documentación/gravámenes/riesgos legales, haya un reclamo, o cuando la consulta exceda lo que
    podés resolver. NO derives por precio publicado, características, zonas, requisitos generales ni
    horarios — eso lo resolvés vos.

    El mensaje que escribís en ESTA misma respuesta es el aviso al cliente (breve, sin prometer
    tiempos). Después de derivar NO vuelvas a escribir en esta conversación. No derives dos veces.

    Args:
        reason: Motivo en una frase, para que el asesor entienda el contexto sin leer todo el chat.
        codigo: Código del inmueble cuyo titular debe atender (si aplica).
    """
    # Pure signal: the actual POST /handoff is done by the caller AFTER the client notice is sent
    # (once derived the CRM 409s any further /messages).
    return json.dumps({"status": "handoff_signaled", "codigo": codigo, "reason": reason}, ensure_ascii=False)


async def request_handoff(conversation_id: int, reason: str, codigo: Optional[str] = None) -> dict:
    """Derive a conversation to the property's titular advisor: POST .../handoff {codigo, reason}.

    Not a tool — the webhook calls this AFTER sending the client notice (once derived the CRM 409s any
    further /messages, so order matters). Idempotent CRM-side. Best-effort: logs and returns {} on
    failure, never raises.
    """
    log = logger.bind(conversation_id=conversation_id, codigo=codigo)
    body: dict[str, Any] = {"reason": reason}
    if codigo:
        body["codigo"] = codigo
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await _request(
                client, "POST", f"{_NS_INMO}/conversations/{conversation_id}/handoff", json=body
            )
            payload = resp.json() if resp.content else {}
            log.info("c21_handoff_requested")
            return payload if isinstance(payload, dict) else {}
    except httpx.HTTPStatusError as e:
        log.error("c21_handoff_http_error", status=e.response.status_code)
        return {}
    except Exception as e:  # noqa: BLE001
        log.exception("c21_handoff_failed", error=str(e))
        return {}
