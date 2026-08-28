"""ImprimirAgent - LangGraph agent for the IMPRIMIR WhatsApp B2B quotation assistant (Valentina).

Flow: WhatsApp → Krayin CRM → agent → CrmGateway. The agent receives conversation_id + wa_id,
drives the quotation conversation, writes to the CRM via the crm.py tools, and returns plain text
that the CrmGateway sends back to the client. It reuses the same Postgres checkpointer / Langfuse
infrastructure as the retired Odontoking agent, minus the intake/booking/insurance machinery.
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
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
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
from app.core.langgraph.tools.crm import (
    _phone_ask_allowed,
    crear_quote,
    derivar_a_asesor,
    get_person_leads,
    guardar_telefono_contacto,
    mover_lead_por_ciudad,
    register_cotizacion,
    registrar_consulta_postventa,
)
from app.core.logging import logger
from app.core.observability import langfuse_callback_handler
from app.schemas import GraphState
from app.services.database import database_service
from app.utils import dump_messages, process_llm_response

PostgresConnPool = AsyncConnectionPool[AsyncConnection[DictRow]]

_IMPRIMIR_TOOLS = [
    mover_lead_por_ciudad,
    register_cotizacion,
    crear_quote,
    registrar_consulta_postventa,
    get_person_leads,
    guardar_telefono_contacto,
    derivar_a_asesor,
]

_PROMPT_FILE = _os.path.join(_os.path.dirname(__file__), "..", "prompts", "imprimir.md")
_TZ_BOLIVIA = ZoneInfo("America/La_Paz")
_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

with open(_PROMPT_FILE, "r") as _f:
    _PROMPT_TEMPLATE = _f.read()


def _load_imprimir_prompt(
    wa_id: str,
    *,
    conversation_id: str | int | None = None,
    channel: Optional[str] = None,
    phone_prompt: Optional[dict[str, Any]] = None,
    nombre_registrado: Optional[str] = None,
    nombre_whatsapp: Optional[str] = None,
) -> str:
    """Render the IMPRIMIR system prompt with Bolivia local datetime and contact context."""
    now = datetime.now(_TZ_BOLIVIA)
    current_datetime = (
        f"{_DIAS_ES[now.weekday()]} {now.day:02d} {_MESES_ES[now.month - 1]} {now.year} "
        f"{now.strftime('%H:%M')}"
    )

    context_lines = ["# Contexto del contacto", f"wa_id: {wa_id}"]
    if conversation_id:
        context_lines.append(f"conversation_id: {conversation_id}")
    if channel:
        context_lines.append(f"canal: {channel}")
    if nombre_registrado:
        context_lines.append(f"nombre_registrado: {nombre_registrado}")
    elif nombre_whatsapp:
        context_lines.append(
            f"nombre_whatsapp: {nombre_whatsapp}  # nombre del perfil de WhatsApp; úsalo sin volver a preguntarlo"
        )
    else:
        context_lines.append("nombre_registrado: null  # pide el nombre del contacto")

    # Phone-capture block: only when the CRM flags the phone missing (messenger). The ask gate is
    # evaluated in code here so the model just reads sí/no - see "Pedido de teléfono" in imprimir.md.
    pp = phone_prompt or {}
    if pp.get("required"):
        allowed = _phone_ask_allowed(True, pp.get("state"), bool(pp.get("exhausted")))
        context_lines += [
            "",
            "# Teléfono del contacto",
            "telefono_conocido: no  # este canal no trae teléfono (Messenger usa PSID)",
            f"puede_pedir_telefono: {'sí' if allowed else 'no'}  # regla ya evaluada; si es 'no', NO lo pidas",
            f"pedidos_hechos: {pp.get('attempts', 0)} de 3",
        ]

    context = "\n".join(context_lines)
    # Solo sustituimos el marcador conocido. NO usamos str.format: el prompt contiene llaves
    # literales (plantillas de bloque, ejemplos de solicitud como {precio} o {lead_id}) que .format
    # trataría como campos y reventaría con KeyError. .replace toca únicamente {current_datetime}.
    return _PROMPT_TEMPLATE.replace("{current_datetime}", current_datetime) + f"\n\n{context}"


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


class ImprimirAgent:
    """LangGraph agent for the IMPRIMIR WhatsApp B2B quotation assistant (Valentina)."""

    def __init__(self):
        """Initialize the IMPRIMIR agent with its own LLM and CRM tool set."""
        self._llm = ChatOpenAI(
            model=settings.ODONTOKING_LLM_MODEL,
            api_key=SecretStr(settings.OPENAI_API_KEY),
            max_tokens=4096,  # pyright: ignore[reportCallIssue]
            temperature=0.2,
            timeout=settings.LLM_REQUEST_TIMEOUT,
            max_retries=2,
        ).bind_tools(_IMPRIMIR_TOOLS)
        self.tools_by_name = {t.name: t for t in _IMPRIMIR_TOOLS}
        self._connection_pool: Optional[PostgresConnPool] = None
        self._graph: Optional[CompiledStateGraph] = None
        # Tracked background tasks so shutdown can await them and not lose chat history.
        self._persist_tasks: set[asyncio.Task] = set()
        logger.info("imprimir_agent_initialized", tools=list(self.tools_by_name.keys()))

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
                        # TCP keepalives - prevent the remote server from closing idle connections.
                        "keepalives": 1,
                        "keepalives_idle": 30,
                        "keepalives_interval": 10,
                        "keepalives_count": 5,
                    },
                )
                await self._connection_pool.open(wait=True, timeout=30)
                logger.info("imprimir_connection_pool_created")
            except Exception as e:
                logger.exception("imprimir_connection_pool_failed", error=str(e))
                return None
        return self._connection_pool

    async def _chat(self, state: GraphState, config: RunnableConfig) -> Command:
        metadata = config.get("metadata", {})
        wa_id = metadata.get("wa_id", "unknown")
        thread_id = config.get("configurable", {}).get("thread_id")
        system_prompt = _load_imprimir_prompt(
            wa_id,
            conversation_id=metadata.get("conversation_id"),
            channel=metadata.get("channel"),
            phone_prompt=metadata.get("phone_prompt"),
            nombre_registrado=metadata.get("nombre_registrado"),
            nombre_whatsapp=metadata.get("nombre_whatsapp"),
        )
        # Build messages with LangChain types directly - bypasses Message max_length validation.
        langchain_messages = [SystemMessage(content=system_prompt)] + list(state.messages)

        try:
            response_message = await self._llm.ainvoke(
                langchain_messages,
                config={"callbacks": config.get("callbacks", [])},
            )
            response_message = process_llm_response(response_message)
            logger.info("imprimir_llm_response", thread_id=thread_id)

            if isinstance(response_message, AIMessage) and response_message.tool_calls:
                goto = "tool_call"
            else:
                goto = END
            return Command(update={"messages": [response_message]}, goto=goto)
        except Exception as e:
            logger.exception("imprimir_chat_failed", thread_id=thread_id, error=str(e))
            raise

    async def _tool_call(self, state: GraphState, config: RunnableConfig) -> Command:
        tool_calls = state.messages[-1].tool_calls

        async def _execute(tc: dict) -> ToolMessage:
            try:
                # Forward config so tools can read injected metadata (lead_id, person_id) without the
                # LLM ever seeing or passing those ids - avoids the id-hallucination this module warns
                # about. Tools that don't declare a `config` param simply ignore it.
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

                self._graph = builder.compile(checkpointer=checkpointer, name="ImprimirAgent")
                logger.info("imprimir_graph_created", has_checkpointer=checkpointer is not None)
            except Exception as e:
                logger.exception("imprimir_graph_creation_failed", error=str(e))
                raise
        return self._graph

    async def _get_graph(self) -> CompiledStateGraph:
        if self._graph is None:
            self._graph = await self.create_graph()
        if self._graph is None:
            raise RuntimeError("imprimir graph initialization failed")
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
        phone_prompt: Optional[dict[str, Any]] = None,
        nombre_registrado: Optional[str] = None,
        nombre_whatsapp: Optional[str] = None,
        handoff_callback: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
    ) -> str:
        """Process one WhatsApp message and return Valentina's plain-text reply.

        If the agent calls `derivar_a_asesor` this turn, `handoff_callback` is invoked with the
        reason so the caller can POST /handoff AFTER sending the reply (the reply is the client's
        notice; once derived the CRM rejects further messages).
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
                "conversation_id": conversation_id,
                # lead_id / person_id come from the CRM event (contact.lead_id / contact.person_id).
                # Injected here so the city-move and quote tools read real ids from config.metadata
                # instead of the LLM passing (and hallucinating) them.
                "lead_id": lead_id,
                "person_id": person_id,
                # channel + phone_prompt drive the messenger phone-capture flow: the phone-submit tool
                # reads conversation_id from here, and (once the prompt diff lands) the system prompt
                # renders the phone-ask gate from phone_prompt. Absent phone_prompt → no ask.
                "channel": channel,
                "phone_prompt": phone_prompt or {},
                "nombre_registrado": nombre_registrado,
                "nombre_whatsapp": nombre_whatsapp,
            },
            "recursion_limit": 50,
        }

        try:
            state = await graph.aget_state(config)
            existing_count = len(state.values.get("messages", [])) if state and state.values else 0

            if state and state.next:
                logger.info("imprimir_resuming_graph", wa_id=wa_id)
                response = await graph.ainvoke(Command(resume=messages[-1].content), config=config)
            else:
                response = await graph.ainvoke(
                    input={"messages": dump_messages(messages), "long_term_memory": ""},
                    config=config,
                )

            # Persist new messages (human + AI + tool) to the chat history table.
            new_msgs = [
                m for m in response.get("messages", [])[existing_count:] if isinstance(m, BaseMessage)
            ]
            if new_msgs:
                task = asyncio.create_task(_persist_messages_async(wa_id, new_msgs))
                self._persist_tasks.add(task)
                task.add_done_callback(self._persist_tasks.discard)

            # Handoff signal: if the agent called derivar_a_asesor THIS turn, surface the reason so
            # the caller derives AFTER sending the reply (the reply is the client's notice).
            if handoff_callback is not None:
                for m in new_msgs:
                    tool_calls = getattr(m, "tool_calls", None) or []
                    for tc in tool_calls:
                        if tc.get("name") == "derivar_a_asesor":
                            reason = str((tc.get("args") or {}).get("reason") or "").strip()
                            logger.info("imprimir_handoff_signaled", wa_id=wa_id, reason=reason[:80])
                            try:
                                await handoff_callback({"reason": reason})
                            except Exception as e:  # noqa: BLE001
                                logger.warning("imprimir_handoff_callback_failed", wa_id=wa_id, error=str(e))

            ai_messages = [
                m for m in response.get("messages", []) if isinstance(m, AIMessage) and m.content
            ]
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
            logger.warning("imprimir_recursion_limit_hit", wa_id=wa_id)
            try:
                await self.clear_history(wa_id)
                logger.info("imprimir_stuck_state_cleared", wa_id=wa_id)
            except Exception as clear_err:
                logger.warning("imprimir_clear_history_failed", wa_id=wa_id, error=str(clear_err))
            return "Lo siento, ocurrió un error procesando tu solicitud. Por favor, inténtalo de nuevo."
        except Exception as e:
            logger.exception("imprimir_get_response_failed", wa_id=wa_id, error=str(e))
            raise

    async def close(self) -> None:
        """Await pending persist tasks and close the connection pool on shutdown."""
        if self._persist_tasks:
            await asyncio.gather(*self._persist_tasks, return_exceptions=True)
        if self._connection_pool:
            await self._connection_pool.close()
            logger.info("imprimir_connection_pool_closed")

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
        logger.info("imprimir_history_cleared", wa_id=wa_id)


imprimir_agent = ImprimirAgent()
