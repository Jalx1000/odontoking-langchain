"""Red de seguridad de Kohlberg: invariantes de conducta del prompt + contrato de tools.

Estos tests son la garantía anti-regresiones de la optimización de tokens (spec
docs/spec-kohlberg-optimizacion-tokens.md). Son herméticos: no llaman a OpenAI ni a la
red, solo inspeccionan el prompt y los schemas de las tools tal como se cargan.

- test_prompt_contract: marcadores de conducta que el recorte del prompt (C2) NO debe borrar.
- test_tool_set_has_core_tools: las tools que SIEMPRE deben existir (caza remociones accidentales).
- test_prompt_size_budget: techo de tamaño del prompt (frena que vuelva a crecer).
"""

from langchain_core.messages import AIMessage, HumanMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately

from app.core.langgraph.kohlberg_graph import (
    _HISTORY_MAX_TOKENS,
    _KOHLBERG_TOOLS,
    _PROMPT_TEMPLATE,
    _load_kohlberg_prompt,
)


def _trim(msgs):
    """Recorta con los MISMOS parámetros que usa _chat (C4)."""
    return trim_messages(
        msgs,
        max_tokens=_HISTORY_MAX_TOKENS,
        strategy="last",
        token_counter=count_tokens_approximately,
        start_on="human",
        include_system=False,
        allow_partial=False,
    )

# Marcadores de conducta obligatorios: cada uno codifica una regla de negocio que debe
# sobrevivir cualquier recorte del prompt. Si un cambio borra alguno, este test lo caza.
_REQUIRED_PROMPT_MARKERS = [
    "N.-)",              # marcador de menús tocables de WhatsApp (contrato con el CRM)
    "get_persona",       # lookup al inicio para no re-pedir datos
    "get_promos",        # catálogo = única fuente de verdad de vinos/precios
    "get_sucursales",    # info de sucursal / asesor por ciudad
    "actualizar_pedido", # registro en vivo (híbrido) del pedido
    "registrar_pedido",  # cierre/confirmación final del pedido
    "get_pedidos",       # lectura de pedidos del cliente
    "derivar_a_asesor",  # derivación a asesor humano
    "sucursal",          # paso 7 (lo que va a recoger + sucursal)
    "horario de atención",  # respetar horarios de la sucursal
]

# Tools que SIEMPRE deben estar registradas (el set core; `think` se quita en C5, por eso
# no está acá: este test valida presencia de lo esencial, no ausencia de lo opcional).
_REQUIRED_TOOL_NAMES = {
    "get_persona",
    "get_promos",
    "get_sucursales",
    "actualizar_pedido",
    "registrar_pedido",
    "get_pedidos",
    "derivar_a_asesor",
}

# Techo de tamaño del prompt (caracteres). Tras el recorte conservador C2 (~23.2k), lo fijamos
# en 24k para frenar que el prompt vuelva a crecer. Bajalo más si recortás más.
_PROMPT_CHAR_BUDGET = 24_000


def test_prompt_contract():
    """Todos los marcadores de conducta obligatorios están presentes en el prompt."""
    missing = [m for m in _REQUIRED_PROMPT_MARKERS if m not in _PROMPT_TEMPLATE]
    assert not missing, f"El prompt de Kohlberg perdió marcadores de conducta: {missing}"


def test_tool_set_has_core_tools():
    """El set core de tools está registrado (caza remociones accidentales)."""
    names = {t.name for t in _KOHLBERG_TOOLS}
    missing = _REQUIRED_TOOL_NAMES - names
    assert not missing, f"Faltan tools core en _KOHLBERG_TOOLS: {missing}"
    # C5: `think` se quitó (cada uso era un round-trip extra). No debe volver.
    assert "think" not in names, "el tool `think` volvió a _KOHLBERG_TOOLS (C5 lo quitó)"


def test_prompt_size_budget():
    """El prompt no supera el techo de tamaño (frena que el costo por token vuelva a subir)."""
    size = len(_PROMPT_TEMPLATE)
    assert size <= _PROMPT_CHAR_BUDGET, (
        f"El prompt de Kohlberg creció a {size} chars (> {_PROMPT_CHAR_BUDGET}). "
        "Recortá o subí el presupuesto conscientemente."
    )


def test_prefix_is_stable():
    """C1: el cuerpo estático del prompt es cacheable — nada volátil arriba del bloque final.

    Cualquier render debe EMPEZAR con `_PROMPT_TEMPLATE.rstrip()` (el prefijo estable que
    OpenAI cachea), y ese cuerpo estático NO debe contener fecha/hora ni el marcador volátil.
    Si un cambio mete algo dinámico arriba, el cache se rompe y este test falla.
    """
    static = _PROMPT_TEMPLATE.rstrip()
    p1 = _load_kohlberg_prompt("wa-1", conversation_id=1, nombre_registrado="Ana")
    p2 = _load_kohlberg_prompt("wa-2", conversation_id=2, nombre_whatsapp="Beto")
    assert p1.startswith(static), "el render no empieza con el cuerpo estático (prefijo roto)"
    assert p2.startswith(static), "el render no empieza con el cuerpo estático (prefijo roto)"
    assert "{current_datetime}" not in static, "quedó el placeholder de datetime en el cuerpo estático"
    assert "# Ahora" not in static, "el bloque volátil quedó dentro del cuerpo estático"


def test_history_trim_bounds_long_conversation():
    """C4: una charla larga se recorta, arranca en HumanMessage y conserva el último turno."""
    msgs = []
    for i in range(400):
        msgs.append(HumanMessage(content=f"mensaje del cliente numero {i} con texto para sumar tokens"))
        msgs.append(AIMessage(content=f"respuesta de Sofía numero {i} con detalle suficiente para pesar"))
    trimmed = _trim(msgs)
    assert trimmed, "el trim no debe vaciar el historial"
    assert isinstance(trimmed[0], HumanMessage), "debe arrancar en un HumanMessage (no huérfano)"
    assert trimmed[-1].content == msgs[-1].content, "debe conservar el último mensaje (turno actual)"
    assert len(trimmed) < len(msgs), "una charla larga debe recortarse"


def test_history_trim_keeps_short_conversation_intact():
    """C4: una charla corta (bajo presupuesto) NO se recorta."""
    msgs = [HumanMessage(content="hola"), AIMessage(content="¡Hola! ¿De qué ciudad nos escribís?")]
    assert len(_trim(msgs)) == len(msgs)
