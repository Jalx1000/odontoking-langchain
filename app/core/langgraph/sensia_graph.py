"""SensiaAgent - LangGraph agent for Sensia (Sofía), a bottled-water distributor WhatsApp sales assistant.

Sensia vende botellones de agua de 20 L y accesorios en Jujuy, Argentina (envío a domicilio o retiro).

Flow: WhatsApp -> Krayin CRM (sensia.sofopolis.com) -> agent -> CRM. Same Postgres checkpointer /
Langfuse infrastructure as the other agents; the difference is the prompt (sensia.md) and the tool
set (sensia.py): get_promos (product catalog / única fuente de verdad), get_sucursales (pickup branch /
advisor phone) and registrar_pedido (the confirmed order as a Krayin lead).

The only ids the tools use (conversation_id / lead_id / person_id) are injected via config.metadata and
never reach the model; the ONLY ids the LLM handles are the public product_id values it reads back from
get_promos. A client who wants a person is handed off with derivar_a_asesor: the tool only SIGNALS, and
the webhook POSTs the handoff AFTER the client notice is sent (once derived the CRM 409s any further
/messages, so order matters).
"""

import asyncio
import json
import os as _os
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    trim_messages,
)
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables.config import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import GraphBubbleUp, GraphInterrupt, GraphRecursionError
from langgraph.graph import END, StateGraph
from langgraph.graph.state import Command, CompiledStateGraph
from langgraph.types import RetryPolicy
from psycopg import AsyncConnection, sql
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

from app.core.config import settings
from app.core.langgraph.tools.sensia import (
    actualizar_pedido,
    derivar_a_asesor,
    get_pedidos,
    get_persona,
    get_promos,
    get_sucursales,
    reactivar_recompra,
    registrar_pedido,
    responder_recompra,
)
from app.core.logging import logger
from app.core.observability import langfuse_callback_handler
from app.schemas import GraphState
from app.services.database import database_service
from app.utils import dump_messages, process_llm_response

PostgresConnPool = AsyncConnectionPool[AsyncConnection[DictRow]]

_SENSIA_TOOLS = [
    get_persona,
    get_promos,
    get_sucursales,
    actualizar_pedido,
    registrar_pedido,
    get_pedidos,
    derivar_a_asesor,
    responder_recompra,
    reactivar_recompra,
]

_PROMPT_FILE = _os.path.join(_os.path.dirname(__file__), "..", "prompts", "sensia.md")
# C4: presupuesto de tokens del HISTORIAL por turno. Generoso a propósito — una charla de
# venta típica (decenas de mensajes cortos) no se recorta; solo acota charlas muy largas para
# que no re-envíen todo. Se preservan el system (aparte) y los pares tool_call/ToolMessage.
_HISTORY_MAX_TOKENS = 4000
_TZ_JUJUY = ZoneInfo("America/Argentina/Jujuy")
_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

with open(_PROMPT_FILE, "r") as _f:
    _PROMPT_TEMPLATE = _f.read()


def _as_text(value: Any) -> str:
    """Render a context value as a short readable string. The CRM may send a field as a dict (e.g.
    ultimo_pedido / domicilio come as objects), so flatten dicts to 'k: v' pairs instead of leaking a
    Python repr into the prompt. Lists → comma-joined. Scalars → str."""
    if isinstance(value, dict):
        parts = [f"{k}={v}" for k, v in value.items() if v not in (None, "", [], {})]
        return "; ".join(parts) if parts else ""
    if isinstance(value, list):
        return ", ".join(_as_text(v) for v in value if v not in (None, "", [], {}))
    return str(value)


def _format_precio(v: Any) -> str:
    """Render an ARS price like '$7.500' (miles con punto, sin decimales). Blank → a confirmar."""
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return "precio a confirmar"
    return "$" + f"{n:,}".replace(",", ".")


def _load_sensia_prompt(
    wa_id: str,
    *,
    conversation_id: str | int | None = None,
    channel: Optional[str] = None,
    nombre_registrado: Optional[str] = None,
    nombre_whatsapp: Optional[str] = None,
    recompra: Optional[dict[str, Any]] = None,
    cliente: Optional[dict[str, Any]] = None,
    pedidos_pendientes: Optional[list] = None,
    catalogo: Optional[dict[str, Any]] = None,
) -> str:
    """Arma el system prompt: cuerpo ESTÁTICO + bloque VOLÁTIL (fecha/hora + contacto) al final.

    C1 (optimización de tokens): todo lo que cambia por llamada —fecha/hora, ids, nombres—
    vive al FINAL, así el cuerpo estático (`_PROMPT_TEMPLATE`) queda byte-idéntico entre
    llamadas y OpenAI puede cachear ese prefijo (~6k tok). Si algo volátil se colara arriba,
    el cache se rompe; el test test_prefix_is_stable lo caza.
    """
    now = datetime.now(_TZ_JUJUY)
    current_datetime = (
        f"{_DIAS_ES[now.weekday()]} {now.day:02d} {_MESES_ES[now.month - 1]} {now.year} "
        f"{now.strftime('%H:%M')}"
    )

    volatile_lines = [
        "# Ahora (varía por mensaje)",
        f"Fecha y hora actual: {current_datetime} (America/Argentina/Jujuy)",
        "",
        "# Contexto del contacto",
        f"wa_id: {wa_id}",
    ]
    if conversation_id:
        volatile_lines.append(f"conversation_id: {conversation_id}")
    if channel:
        volatile_lines.append(f"canal: {channel}")
    if nombre_registrado:
        volatile_lines.append(f"nombre_registrado: {nombre_registrado}")
    elif nombre_whatsapp:
        volatile_lines.append(
            f"nombre_whatsapp: {nombre_whatsapp}  # nombre del perfil; úsalo sin volver a preguntarlo"
        )
    else:
        volatile_lines.append("nombre_registrado: null  # pide el nombre del contacto si no lo dio")

    # Catálogo por zona (§5): fuente de verdad de productos y precios. Se renderiza acá para que el
    # agente ofrezca el producto/precio correcto sin depender de get_promos (que queda de respaldo).
    if isinstance(catalogo, dict) and catalogo:
        cat_lines = ["", "# Catálogo (fuente de verdad de productos y precios)"]
        zonas_cob = catalogo.get("zonas_con_cobertura") or []
        if zonas_cob:
            cat_lines.append(
                "Zonas con cobertura: " + ", ".join(str(z) for z in zonas_cob)
                + ". NUNCA digas que no hay cobertura ni productos en una de estas zonas."
            )
        if catalogo.get("zona_del_cliente"):
            cat_lines.append(f"Zona que el CRM ya conoce del cliente: {catalogo['zona_del_cliente']}")
        por_zona = catalogo.get("por_zona")
        if isinstance(por_zona, dict) and por_zona:
            cat_lines.append(
                "Productos por zona (ofrecé el precio y el product_id de la ZONA del cliente; no mezcles zonas):"
            )
            for zona, items in por_zona.items():
                if not isinstance(items, list) or not items:
                    continue
                cat_lines.append(f"[{zona}]")
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    precio = it.get("precio_promocion") or it.get("precio")
                    nombre = it.get("nombre") or it.get("name") or "Producto"
                    cat_lines.append(f"- id {it.get('product_id')}: {nombre} — {_format_precio(precio)}")
        volatile_lines += cat_lines

    # Recordatorio de recompra: solo cuando el CRM manda el bloque. Le damos al agente los datos para
    # confirmar el domicilio guardado (en vez de re-preguntarlo) y el nº de intento. La URL para
    # responder NO va acá (la usa responder_recompra desde config.metadata), el LLM no la maneja.
    if isinstance(recompra, dict) and recompra:
        dom = recompra.get("domicilio") or {}
        dom_txt = ", ".join(
            str(x) for x in (dom.get("direccion"), dom.get("referencia")) if x
        ) or "sin domicilio guardado"
        volatile_lines += [
            "",
            "# Recordatorio de recompra (ACTIVO en esta conversación)",
            "El cliente está respondiendo nuestro recordatorio para agendar su próximo pedido. "
            "Seguí la sección RECORDATORIO DE RECOMPRA del prompt.",
            f"recompra.datos_completos: {str(recompra.get('datos_completos', False)).lower()}",
            f"recompra.intento: {recompra.get('intento', 1)}",
            f"recompra.domicilio: {dom_txt}",
            "Al responder su decisión, llamá a responder_recompra(respuesta='si'|'no'|'baja'). "
            "Si pide volver a recibir recordatorios, llamá a reactivar_recompra().",
        ]

    # Cliente conocido (A3): si el CRM dice que ya compró, le damos sus datos para NO rehacer el alta.
    if isinstance(cliente, dict) and cliente.get("es_conocido"):
        datos = []
        if cliente.get("nombre"):
            datos.append(f"nombre: {_as_text(cliente['nombre'])}")
        if cliente.get("ciudad"):
            datos.append(f"zona: {_as_text(cliente['ciudad'])}")
        if cliente.get("domicilio"):
            datos.append(f"domicilio: {_as_text(cliente['domicilio'])}")
        if cliente.get("bidones_en_su_casa") is not None:
            datos.append(f"bidones sin devolver: {cliente['bidones_en_su_casa']}")
        if cliente.get("ultimo_pedido"):
            datos.append(f"último pedido: {_as_text(cliente['ultimo_pedido'])}")
        volatile_lines += [
            "",
            "# Cliente CONOCIDO (ya compró antes)",
            "Tenemos sus datos; NO rehagas el alta. Confirmá el domicilio en una línea y preguntá solo "
            "la cantidad (ver sección ASIGNACIÓN Y CLIENTES CONOCIDOS).",
        ] + [f"cliente.{d}" for d in datos]

    # Pedidos pendientes (A4): si tiene pedidos sin entregar, hay que preguntar antes de abrir otro.
    if isinstance(pedidos_pendientes, list) and pedidos_pendientes:
        resumen = []
        for p in pedidos_pendientes[:5]:
            if isinstance(p, dict):
                desc = p.get("detalle") or p.get("titulo") or f"pedido #{p.get('id')}"
                resumen.append(f"- #{p.get('id')}: {desc}")
        volatile_lines += [
            "",
            "# Pedidos PENDIENTES (sin entregar) de este cliente",
            "Si pide algo nuevo, NO abras otro pedido sin preguntar si quiere sumarlo a uno pendiente o "
            "dejarlo aparte (ver sección ASIGNACIÓN Y CLIENTES CONOCIDOS).",
        ] + resumen

    volatile = "\n".join(volatile_lines)
    # El prefijo estable es exactamente _PROMPT_TEMPLATE.rstrip(); todo lo de abajo es volátil.
    return _PROMPT_TEMPLATE.rstrip() + "\n\n" + volatile


def _history_to_messages(history: Optional[list]) -> list[BaseMessage]:
    """Mapea el `history` del evento (§4) a mensajes LangChain, usando `sender` como guía:
    contact → Human; ia/human/sistema → AI (un `human`/asesor vale como dicho por Sensia). Entradas
    vacías se omiten. Es la fuente de verdad del contexto de la conversación (reemplaza al checkpointer)."""
    msgs: list[BaseMessage] = []
    for h in history or []:
        if not isinstance(h, dict):
            continue
        content = str(h.get("content") or "").strip()
        if not content:
            continue
        sender = str(h.get("sender") or "").lower()
        role = str(h.get("role") or "").lower()
        is_agent_side = sender in ("ia", "human", "sistema") or (not sender and role == "assistant")
        msgs.append(AIMessage(content=content) if is_agent_side else HumanMessage(content=content))
    return msgs


def _serialize_message(m: BaseMessage) -> str:
    return json.dumps(m.model_dump(), ensure_ascii=False, default=str)


def _persist_messages(wa_id: str, messages: list[BaseMessage]) -> None:
    for m in messages:
        try:
            database_service.save_chat_message(wa_id, _serialize_message(m))
        except Exception as e:
            logger.warning("chat_history_save_failed", wa_id=wa_id, error=str(e))


async def _persist_messages_async(wa_id: str, messages: list[BaseMessage]) -> None:
    await asyncio.to_thread(_persist_messages, wa_id, messages)


def _log_llm_token_usage(message: BaseMessage, *, wa_id: str, thread_id: Optional[str]) -> None:
    """Log per-call token usage so el costo es visible en los logs (sin depender de Langfuse).

    Lee `usage_metadata` de la AIMessage de LangChain. `cached_tokens > 0` confirma que el
    prompt-cache de OpenAI está pegando en el prefijo estático (system prompt + tools).
    Best-effort: si el modelo no reporta usage, no hace nada.
    """
    usage = getattr(message, "usage_metadata", None)
    if not usage:
        return
    details = usage.get("input_token_details") or {}
    logger.info(
        "sensia_llm_tokens",
        wa_id=wa_id,
        thread_id=thread_id,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cached_tokens=details.get("cache_read"),
    )


class SensiaAgent:
    """LangGraph agent for Sensia (Sofía), a bottled-water distributor WhatsApp sales assistant."""

    def __init__(self):
        """Initialize the Sensia agent with its own LLM and wine-sales tool set."""
        self._llm = ChatOpenAI(
            model=settings.SENSIA_LLM_MODEL,
            api_key=SecretStr(settings.OPENAI_API_KEY),
            # Techo de salida (C6): 1536 tok ≈ 1150 palabras, de sobra para un mensaje de
            # WhatsApp (aun listando promos); frena una generación runaway. No cambia las
            # respuestas normales — solo cobra los tokens realmente generados.
            max_tokens=1536,  # pyright: ignore[reportCallIssue]
            temperature=0.2,
            timeout=settings.LLM_REQUEST_TIMEOUT,
            max_retries=2,
        ).bind_tools(_SENSIA_TOOLS)
        self.tools_by_name = {t.name: t for t in _SENSIA_TOOLS}
        self._connection_pool: Optional[PostgresConnPool] = None
        self._graph: Optional[CompiledStateGraph] = None
        self._persist_tasks: set[asyncio.Task] = set()
        logger.info("sensia_agent_initialized", tools=list(self.tools_by_name.keys()))

    async def _get_connection_pool(self) -> Optional[PostgresConnPool]:
        if self._connection_pool is None:
            try:
                connection_url = (
                    "postgresql://"
                    f"{quote_plus(settings.POSTGRES_USER)}:{quote_plus(settings.POSTGRES_PASSWORD)}"
                    f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
                )
                self._connection_pool = AsyncConnectionPool(
                    connection_url,
                    open=False,
                    min_size=1,
                    max_size=settings.POSTGRES_POOL_SIZE,
                    reconnect_timeout=30,
                    kwargs={
                        "autocommit": True,
                        "connect_timeout": 10,
                        "prepare_threshold": None,
                        "row_factory": dict_row,
                        "keepalives": 1,
                        "keepalives_idle": 30,
                        "keepalives_interval": 10,
                        "keepalives_count": 5,
                    },
                )
                await self._connection_pool.open(wait=True, timeout=30)
                logger.info("sensia_connection_pool_created")
            except Exception as e:
                logger.exception("sensia_connection_pool_failed", error=str(e))
                return None
        return self._connection_pool

    async def _chat(self, state: GraphState, config: RunnableConfig) -> Command:
        metadata = config.get("metadata", {})
        wa_id = metadata.get("wa_id", "unknown")
        thread_id = config.get("configurable", {}).get("thread_id")
        system_prompt = _load_sensia_prompt(
            wa_id,
            conversation_id=metadata.get("conversation_id"),
            channel=metadata.get("channel"),
            nombre_registrado=metadata.get("nombre_registrado"),
            nombre_whatsapp=metadata.get("nombre_whatsapp"),
            recompra=metadata.get("recompra"),
            cliente=metadata.get("cliente"),
            pedidos_pendientes=metadata.get("pedidos_pendientes"),
            catalogo=metadata.get("catalogo"),
        )
        # C4: acota el historial a un presupuesto de tokens (solo recorta charlas largas).
        # strategy="last" conserva lo más reciente; start_on="human" evita arrancar en un
        # ToolMessage/AIMessage huérfano (rompería la API de OpenAI). El system va aparte.
        history = trim_messages(
            list(state.messages),
            max_tokens=_HISTORY_MAX_TOKENS,
            strategy="last",
            token_counter=count_tokens_approximately,
            start_on="human",
            include_system=False,
            allow_partial=False,
        )
        langchain_messages = [SystemMessage(content=system_prompt)] + history

        try:
            response_message = await self._llm.ainvoke(
                langchain_messages,
                config={"callbacks": config.get("callbacks", [])},
            )
            _log_llm_token_usage(response_message, wa_id=wa_id, thread_id=thread_id)
            response_message = process_llm_response(response_message)
            logger.info("sensia_llm_response", thread_id=thread_id)

            if isinstance(response_message, AIMessage) and response_message.tool_calls:
                goto = "tool_call"
            else:
                goto = END
            return Command(update={"messages": [response_message]}, goto=goto)
        except Exception as e:
            logger.exception("sensia_chat_failed", thread_id=thread_id, error=str(e))
            raise

    async def _tool_call(self, state: GraphState, config: RunnableConfig) -> Command:
        tool_calls = state.messages[-1].tool_calls

        async def _execute(tc: dict) -> ToolMessage:
            try:
                result = await self.tools_by_name[tc["name"]].ainvoke(tc["args"], config)
            except GraphBubbleUp:
                raise
            except Exception as e:
                logger.warning("tool_execution_failed", tool=tc["name"], error=str(e))
                result = json.dumps({"error": str(e)})
            return ToolMessage(content=result, name=tc["name"], tool_call_id=tc["id"])

        if len(tool_calls) == 1:
            outputs = [await _execute(tool_calls[0])]
        else:
            outputs = list(await asyncio.gather(*[_execute(tc) for tc in tool_calls]))
        return Command(update={"messages": outputs}, goto="chat")

    async def create_graph(self) -> Optional[CompiledStateGraph]:
        """Build and compile the LangGraph state machine, creating the Postgres checkpointer."""
        if self._graph is None:
            try:
                builder = StateGraph(GraphState)
                builder.add_node("chat", self._chat, destinations=("tool_call", END))
                builder.add_node(
                    "tool_call",
                    self._tool_call,
                    destinations=("chat",),
                    retry_policy=RetryPolicy(max_attempts=3),
                )
                builder.set_entry_point("chat")
                builder.set_finish_point("chat")

                pool = await self._get_connection_pool()
                if pool:
                    checkpointer = AsyncPostgresSaver(pool)
                    await checkpointer.setup()
                else:
                    checkpointer = None

                self._graph = builder.compile(checkpointer=checkpointer, name="SensiaAgent")
                logger.info("sensia_graph_created", has_checkpointer=checkpointer is not None)
            except Exception as e:
                logger.exception("sensia_graph_creation_failed", error=str(e))
                raise
        return self._graph

    async def _get_graph(self) -> CompiledStateGraph:
        if self._graph is None:
            self._graph = await self.create_graph()
        if self._graph is None:
            raise RuntimeError("sensia graph initialization failed")
        return self._graph

    async def get_response(
        self,
        messages: list,
        wa_id: str,
        *,
        conversation_id: str | int | None = None,
        lead_id: Optional[int] = None,
        person_id: Optional[int] = None,
        channel: Optional[str] = None,
        nombre_registrado: Optional[str] = None,
        nombre_whatsapp: Optional[str] = None,
        recompra: Optional[dict[str, Any]] = None,
        asignacion: Optional[dict[str, Any]] = None,
        cliente: Optional[dict[str, Any]] = None,
        pedidos_pendientes: Optional[list] = None,
        catalogo: Optional[dict[str, Any]] = None,
        lead: Optional[dict[str, Any]] = None,
        history: Optional[list] = None,
        handoff_callback: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
    ) -> str:
        """Process one WhatsApp message and return Sofía's plain-text reply.

        If the agent calls `derivar_a_asesor` this turn, `handoff_callback` is invoked with the reason
        (and ciudad) so the caller POSTs /handoff AFTER sending the reply (the reply is the client's
        notice; once derived the CRM 409s any further /messages).
        """
        graph = await self._get_graph()
        callbacks: list[BaseCallbackHandler] = (
            [langfuse_callback_handler] if settings.LANGFUSE_TRACING_ENABLED else []
        )
        config: RunnableConfig = {
            "configurable": {"thread_id": wa_id},
            "callbacks": callbacks,
            "metadata": {
                "wa_id": wa_id,
                # conversation_id / lead_id / person_id come from the CRM event and are injected here so
                # registrar_pedido reuses the auto-created lead instead of duplicating it. The LLM never
                # sees them (weak models hallucinate ids -> corrupt data).
                "conversation_id": conversation_id,
                "lead_id": lead_id,
                "person_id": person_id,
                "channel": channel,
                "nombre_registrado": nombre_registrado,
                "nombre_whatsapp": nombre_whatsapp,
                # Repurchase reminder block (only when a reminder is live). The prompt uses it to
                # confirm the saved address; responder_recompra reads its URL from here.
                "recompra": recompra,
                # Assignment block: tools read asignacion.ciudades (stage/pipeline ids) from here, in
                # code. El CRM asigna el distribuidor; el agente no manda user_id.
                "asignacion": asignacion,
                # Known-client + pending-orders blocks (A3/A4): surfaced in the prompt's volatile tail.
                "cliente": cliente,
                "pedidos_pendientes": pedidos_pendientes,
                # Catálogo por zona (§5): fuente de verdad de productos/precios, se muestra en el prompt.
                # Pedido abierto de la conversación (§8): tools deciden POST vs PUT por lead.is_empty.
                "catalogo": catalogo,
                "lead": lead,
            },
            # Cap del loop ReAct por turno: 15 super-steps ≈ 7 ciclos chat/tool, de sobra
            # para un turno de venta guiado; frena un runaway de tools antes de quemar tokens
            # (50 permitía ~25 saltos ≈ ~400k tokens en una sola respuesta). GraphRecursionError
            # ya se captura más abajo.
            "recursion_limit": 15,
        }

        try:
            # §4/§13: el `history` del evento es la FUENTE DE VERDAD del contexto. Si viene, reseteamos el
            # checkpointer del hilo y sembramos el turno con ese historial + el mensaje actual. Así un turno
            # que el cliente nunca recibió (p. ej. un 404 al contestar) no puede envenenar los siguientes, y
            # el agente nunca "recuerda" algo que no se dijo en la conversación real. Si NO viene el history
            # (CRM viejo/ausente), caemos al checkpointer acumulado de siempre.
            event_msgs = _history_to_messages(history)
            if event_msgs:
                current_text = str(messages[-1].content) if messages else ""
                # Dedup: a veces el CRM incluye el mensaje actual como último del history.
                if (isinstance(event_msgs[-1], HumanMessage)
                        and str(event_msgs[-1].content).strip() == current_text.strip()):
                    event_msgs = event_msgs[:-1]
                input_list: list[BaseMessage] = event_msgs + [HumanMessage(content=current_text)]
                try:
                    await self.clear_history(wa_id)  # el history del evento reemplaza al checkpointer propio
                except Exception as clear_err:  # noqa: BLE001
                    logger.warning("sensia_clear_before_seed_failed", wa_id=wa_id, error=str(clear_err))
                existing_count = len(input_list)
                logger.info("sensia_event_history_seed", wa_id=wa_id, history_msgs=len(input_list))
                response = await graph.ainvoke(
                    input={"messages": input_list, "long_term_memory": ""},
                    config=config,
                )
            else:
                state = await graph.aget_state(config)
                existing_count = len(state.values.get("messages", [])) if state and state.values else 0
                if state and state.next:
                    logger.info("sensia_resuming_graph", wa_id=wa_id)
                    response = await graph.ainvoke(Command(resume=messages[-1].content), config=config)
                else:
                    response = await graph.ainvoke(
                        input={"messages": dump_messages(messages), "long_term_memory": ""},
                        config=config,
                    )

            new_msgs = [
                m for m in response.get("messages", [])[existing_count:] if isinstance(m, BaseMessage)
            ]
            if new_msgs:
                task = asyncio.create_task(_persist_messages_async(wa_id, new_msgs))
                self._persist_tasks.add(task)
                task.add_done_callback(self._persist_tasks.discard)

            # Handoff signal: if derivar_a_asesor was called THIS turn, surface reason + ciudad so the
            # caller derives AFTER sending the reply (the reply is the client's notice).
            if handoff_callback is not None:
                for m in new_msgs:
                    for tc in getattr(m, "tool_calls", None) or []:
                        if tc.get("name") == "derivar_a_asesor":
                            args = tc.get("args") or {}
                            reason = str(args.get("reason") or "").strip()
                            ciudad = args.get("ciudad")
                            logger.info("sensia_handoff_signaled", wa_id=wa_id, ciudad=ciudad, reason=reason[:80])
                            try:
                                await handoff_callback({"reason": reason, "ciudad": ciudad})
                            except Exception as e:  # noqa: BLE001
                                logger.warning("sensia_handoff_callback_failed", wa_id=wa_id, error=str(e))

            # Solo los mensajes GENERADOS este turno (new_msgs): en el path de history sembramos
            # AIMessages previos y no queremos devolver uno viejo como si fuera la respuesta de ahora.
            ai_messages = [m for m in new_msgs if isinstance(m, AIMessage) and m.content]
            if not ai_messages:
                return "Disculpa, ocurrió un error. ¿Puedes intentarlo de nuevo en un momento?"

            last_content = ai_messages[-1].content
            if isinstance(last_content, list):
                last_content = " ".join(
                    block.get("text", "") for block in last_content if isinstance(block, dict)
                )
            return str(last_content).strip()

        except GraphInterrupt:
            state = await graph.aget_state(config)
            interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Aguarda un momento."
            return str(interrupt_value)
        except GraphRecursionError:
            logger.warning("sensia_recursion_limit_hit", wa_id=wa_id)
            try:
                await self.clear_history(wa_id)
                logger.info("sensia_stuck_state_cleared", wa_id=wa_id)
            except Exception as clear_err:
                logger.warning("sensia_clear_history_failed", wa_id=wa_id, error=str(clear_err))
            return "Lo siento, ocurrió un error procesando tu solicitud. Por favor, inténtalo de nuevo."
        except Exception as e:
            logger.exception("sensia_get_response_failed", wa_id=wa_id, error=str(e))
            raise

    async def close(self) -> None:
        """Await pending persist tasks and close the connection pool on shutdown."""
        if self._persist_tasks:
            await asyncio.gather(*self._persist_tasks, return_exceptions=True)
        if self._connection_pool:
            await self._connection_pool.close()
            logger.info("sensia_connection_pool_closed")

    async def clear_history(self, wa_id: str) -> None:
        """Delete all LangGraph checkpoint data for a given WhatsApp ID."""
        pool = await self._get_connection_pool()
        if pool is None:
            raise RuntimeError("connection pool unavailable")
        async with pool.connection() as conn:
            async with conn.pipeline():
                for table in settings.CHECKPOINT_TABLES:
                    await conn.execute(
                        sql.SQL("DELETE FROM {} WHERE thread_id = %s").format(sql.Identifier(table)),
                        (wa_id,),
                    )
        logger.info("sensia_history_cleared", wa_id=wa_id)


sensia_agent = SensiaAgent()
