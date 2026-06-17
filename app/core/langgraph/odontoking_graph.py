"""OdontokingAgent — LangGraph agent for WhatsApp dental clinic assistant."""

import asyncio
import json
import os as _os
from datetime import datetime
from typing import (
    Optional,
    cast,
)
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    SystemMessage,
    ToolMessage,
    convert_to_openai_messages,
)
from langchain_core.runnables.config import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import GraphBubbleUp, GraphInterrupt, GraphRecursionError
from langgraph.graph import (
    END,
    StateGraph,
)
from langgraph.graph.state import (
    Command,
    CompiledStateGraph,
)
from langgraph.types import RetryPolicy
from psycopg import (
    AsyncConnection,
    sql,
)
from psycopg.rows import (
    DictRow,
    dict_row,
)
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

from app.core.config import settings
from app.services.database import database_service
from app.core.langgraph.tools.ask_human import ask_human
from app.core.langgraph.tools.crm import get_citas, sync_transcript_to_crm, update_crm
from app.core.langgraph.tools.insurance import verify_insurance
from app.core.langgraph.tools.odontoking import (
    get_disponibilidad,
    get_doctor_schedule,
    get_doctors,
    get_horarios,
    get_services,
    get_specialties,
)
from app.core.langgraph.booking import advance_booking, motivo_is_deterministic
from app.core.langgraph.intake import (
    advance_intake,
    crm_display_name,
    handoff_context,
    intake_store,
    is_booking_intent,
    new_state,
)
from app.core.logging import logger
from app.core.observability import langfuse_callback_handler
from app.schemas import GraphState
from app.utils import (
    dump_messages,
    process_llm_response,
)

PostgresConnPool = AsyncConnectionPool[AsyncConnection[DictRow]]

_ODONTOKING_TOOLS = [
    get_services,
    get_specialties,
    get_doctors,
    get_doctor_schedule,
    get_horarios,
    get_disponibilidad,
    verify_insurance,
    update_crm,
    get_citas,
    sync_transcript_to_crm,
    ask_human,
]

_PROMPT_FILE = _os.path.join(_os.path.dirname(__file__), "..", "prompts", "odontoking.md")
_TZ_BOLIVIA = ZoneInfo("America/La_Paz")
_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

with open(_PROMPT_FILE, "r") as _f:
    _PROMPT_TEMPLATE = _f.read()


def _load_odontoking_prompt(
    wa_id: str,
    *,
    is_new_patient: bool = True,
    ci_paciente: str | None = None,
    seguro_paciente: str | None = None,
    nombre_registrado: str | None = None,
    nombre_whatsapp: str | None = None,
) -> str:
    """Render the odontoking system prompt with Bolivia local datetime and patient context."""
    now = datetime.now(_TZ_BOLIVIA)
    day_name = _DIAS_ES[now.weekday()]
    month_name = _MESES_ES[now.month - 1]
    current_datetime = f"{day_name} {now.day:02d} {month_name} {now.year} {now.strftime('%H:%M')}"

    # A patient without a real registered name is treated as new so the onboarding
    # flow (name, age, insurance) runs even if they already exist in the CRM.
    if not nombre_registrado:
        is_new_patient = True

    context_lines = [
        "# Contexto del paciente",
        f"wa_id: {wa_id}",
        f"paciente_nuevo: {'true' if is_new_patient else 'false'}",
    ]
    # Name resolution: registered CRM name → WhatsApp profile name → ask the patient.
    # (verify_insurance.patient_name takes priority during the flow, per the prompt.)
    if nombre_registrado:
        context_lines.append(f"nombre_registrado: {nombre_registrado}")
    elif nombre_whatsapp:
        context_lines.append(
            f"nombre_whatsapp: {nombre_whatsapp}  # nombre del perfil de WhatsApp; úsalo si no hay nombre del seguro, sin volver a preguntarlo"
        )
    else:
        context_lines.append("nombre_registrado: null  # pedir el nombre completo al paciente")
    if ci_paciente:
        context_lines.append(f"ci_paciente_registrada: {ci_paciente}")
    else:
        context_lines.append("ci_paciente_registrada: null")
    if seguro_paciente:
        context_lines.append(f"seguro_registrado: {seguro_paciente}")
    else:
        context_lines.append("seguro_registrado: null")
    # Insurance is a hard gate before booking. If it is not already verified in context,
    # the agent MUST run the insurance flow (ask + carnet + verify_insurance) before
    # proposing slots or confirming — even for returning patients.
    if not (ci_paciente and seguro_paciente):
        context_lines.append(
            "# ⚠️ SEGURO NO VERIFICADO — OBLIGATORIO antes de agendar (también pacientes recurrentes): "
            "pregunta por el seguro; si elige una aseguradora pide el carnet y llama verify_insurance. "
            "Si la verificación NO da VIGENTE, NO agendes. Solo 'No tengo seguro' puede agendar como particular."
        )

    context = "\n".join(context_lines)
    return _PROMPT_TEMPLATE.format(current_datetime=current_datetime) + f"\n\n{context}"


def _chat_session_id(wa_id: str) -> str:
    return f"{wa_id}@whatsapp.sofopolis.net"


def _serialize_message(m: BaseMessage) -> str:
    return json.dumps(m.model_dump(), ensure_ascii=False, default=str)


def _persist_messages(wa_id: str, messages: list[BaseMessage]) -> None:
    sid = _chat_session_id(wa_id)
    for m in messages:
        try:
            database_service.save_chat_message(sid, _serialize_message(m))
        except Exception as e:
            logger.warning("chat_history_save_failed", wa_id=wa_id, error=str(e))


async def _persist_messages_async(wa_id: str, messages: list[BaseMessage]) -> None:
    await asyncio.to_thread(_persist_messages, wa_id, messages)


class OdontokingAgent:
    """LangGraph agent for the Odontoking WhatsApp dental clinic assistant."""

    def __init__(self):
        """Initialize the Odontoking agent with its own LLM and tool set."""
        self._llm = ChatOpenAI(
            model=settings.ODONTOKING_LLM_MODEL,
            api_key=SecretStr(settings.OPENAI_API_KEY),
            max_tokens=4096,  # pyright: ignore[reportCallIssue]
            temperature=0.2,
            timeout=settings.LLM_REQUEST_TIMEOUT,
            max_retries=2,
        ).bind_tools(_ODONTOKING_TOOLS)
        self.tools_by_name = {t.name: t for t in _ODONTOKING_TOOLS}
        self._connection_pool: Optional[PostgresConnPool] = None
        self._graph: Optional[CompiledStateGraph] = None
        # Tracked background tasks so shutdown can await them and not lose chat history
        self._persist_tasks: set[asyncio.Task] = set()
        logger.info("odontoking_agent_initialized", tools=list(self.tools_by_name.keys()))

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
                        # TCP keepalives — prevent the remote server from closing idle connections
                        "keepalives": 1,
                        "keepalives_idle": 30,
                        "keepalives_interval": 10,
                        "keepalives_count": 5,
                    },
                )
                await self._connection_pool.open(wait=True, timeout=30)
                logger.info("odontoking_connection_pool_created")
            except Exception as e:
                logger.exception("odontoking_connection_pool_failed", error=str(e))
                return None
        return self._connection_pool

    async def _chat(self, state: GraphState, config: RunnableConfig) -> Command:
        metadata = config.get("metadata", {})
        wa_id = metadata.get("wa_id", "unknown")
        thread_id = config.get("configurable", {}).get("thread_id")
        system_prompt = _load_odontoking_prompt(
            wa_id,
            is_new_patient=metadata.get("is_new_patient", True),
            ci_paciente=metadata.get("ci_paciente"),
            seguro_paciente=metadata.get("seguro_paciente"),
            nombre_registrado=metadata.get("nombre_registrado"),
            nombre_whatsapp=metadata.get("nombre_whatsapp"),
        )
        # When the deterministic intake already collected steps 1-6, inject that data so the
        # LLM continues from step 7 and never re-asks.
        intake_handoff = metadata.get("intake_handoff")
        if intake_handoff:
            system_prompt = f"{system_prompt}\n\n{intake_handoff}"

        # Build messages with LangChain types directly — bypasses Message max_length validation
        langchain_messages = [SystemMessage(content=system_prompt)] + list(state.messages)

        try:
            response_message = await self._llm.ainvoke(
                langchain_messages,
                config={"callbacks": config.get("callbacks", [])},
            )
            response_message = process_llm_response(response_message)
            logger.info("odontoking_llm_response", thread_id=thread_id)

            if isinstance(response_message, AIMessage) and response_message.tool_calls:
                goto = "tool_call"
            else:
                goto = END

            return Command(update={"messages": [response_message]}, goto=goto)
        except Exception as e:
            logger.exception("odontoking_chat_failed", thread_id=thread_id, error=str(e))
            raise

    async def _tool_call(self, state: GraphState) -> Command:
        tool_calls = state.messages[-1].tool_calls

        async def _execute(tc: dict) -> ToolMessage:
            try:
                result = await self.tools_by_name[tc["name"]].ainvoke(tc["args"])
            except GraphBubbleUp:
                # ask_human's interrupt() raises GraphInterrupt (a GraphBubbleUp / Exception
                # subclass). It MUST bubble up to pause the graph — never swallow it here.
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

                self._graph = builder.compile(checkpointer=checkpointer, name="OdontokingAgent")
                logger.info("odontoking_graph_created", has_checkpointer=checkpointer is not None)
            except Exception as e:
                logger.exception("odontoking_graph_creation_failed", error=str(e))
                raise
        return self._graph

    async def _get_graph(self) -> CompiledStateGraph:
        if self._graph is None:
            self._graph = await self.create_graph()
        if self._graph is None:
            raise RuntimeError("odontoking graph initialization failed")
        return self._graph

    async def _intake_turn(
        self,
        wa_id: str,
        text: str,
        nombre_whatsapp: Optional[str],
    ) -> tuple[Optional[str], Optional[str]]:
        """Drive the deterministic intake for one turn.

        Returns (reply, handoff):
          - reply not None  → send this question/message and end the turn (intake in progress).
          - reply None      → fall through to the LLM (Phase 2). handoff carries the collected
                              data when the intake is complete, else None (general chat).
        """
        state = await intake_store.get(wa_id)

        async def _start() -> tuple[Optional[str], Optional[str]]:
            # nombre is left unset so the intake always asks for the real name; the WhatsApp
            # profile name is stored only to build the combined CRM name later.
            fresh = new_state(nombre_whatsapp=nombre_whatsapp)
            result = await advance_intake(fresh, None)
            await intake_store.set(wa_id, result.state)
            reply = f"¡Hola! Gracias por escribir a Odontoking 🦷✨, Con gusto le ayudo a agendar su cita. {result.reply}" if result.reply else result.reply
            return reply, None

        if state is None:
            if not is_booking_intent(text):
                return None, None  # greeting / general question → let the LLM handle it
            return await _start()

        # Active deterministic booking (Phase 2) → keep driving it; do NOT restart on a
        # booking keyword that may appear mid-flow.
        booking_phase = state.get("booking_phase")
        if booking_phase and booking_phase != "done":
            return await self._booking_turn(wa_id, state, text)

        if state.get("completo"):
            if is_booking_intent(text):
                return await _start()  # a new booking → re-run from step 1
            # "Otro" / free-text motivo runs Phase 2 on the LLM; a finished booking (or
            # anything else) is plain post-booking chat for the LLM.
            if state.get("motivo") and not motivo_is_deterministic(state.get("motivo")):
                return None, handoff_context(state)
            return None, None

        result = await advance_intake(state, text)
        await intake_store.set(wa_id, result.state)
        if not result.completed:
            return result.reply, None

        logger.info("intake_completed", wa_id=wa_id)
        # Fixed motivo → drive the deterministic booking flow. Free-text "Otro" → best-effort
        # CRM capture + LLM handoff (semantic matching is the LLM's job).
        if motivo_is_deterministic(result.state.get("motivo")):
            return await self._booking_turn(wa_id, result.state, None)
        self._persist_intake_crm(wa_id, result.state)
        return None, handoff_context(result.state)

    async def _booking_turn(
        self, wa_id: str, state: dict, text: Optional[str]
    ) -> tuple[Optional[str], Optional[str]]:
        """Drive one deterministic Phase-2 booking turn and persist the updated state."""
        state["wa_id"] = wa_id
        result = await advance_booking(state, text)
        await intake_store.set(wa_id, result.state)
        return result.reply, None

    def _persist_intake_crm(self, wa_id: str, state: dict) -> None:
        """Fire-and-forget CRM capture of the intake data (lead + person + insurance)."""

        async def _do() -> None:
            try:
                payload: dict = {
                    "wa_id": wa_id,
                    "person_name": crm_display_name(state.get("nombre_whatsapp"), state.get("nombre")),
                    "edad_paciente": state.get("edad"),
                    "is_for_self": bool(state.get("is_for_self")),
                    "paciente_antiguo": bool(state.get("es_antiguo")),
                    "motivo_consulta": state.get("motivo"),
                }
                if state.get("is_for_self") is False:
                    payload["nombre_paciente_de_otra_persona"] = state.get("tercero_nombre")
                    payload["edad_paciente_de_otra_persona"] = state.get("tercero_edad")
                if state.get("seguro") and state.get("seguro") != "No tengo seguro":
                    payload["seguro_de_vida"] = state.get("seguro")
                    payload["numero_carnet"] = state.get("ci")
                    payload["estado_seguro"] = state.get("seguro_estado")
                await update_crm.ainvoke(payload)
            except Exception as e:
                logger.warning("intake_crm_persist_failed", wa_id=wa_id, error=str(e))

        task = asyncio.create_task(_do())
        self._persist_tasks.add(task)
        task.add_done_callback(self._persist_tasks.discard)

    async def get_response(
        self,
        messages: list,
        wa_id: str,
        *,
        is_new_patient: bool = True,
        ci_paciente: str | None = None,
        seguro_paciente: str | None = None,
        nombre_registrado: str | None = None,
        nombre_whatsapp: str | None = None,
    ) -> str:
        """Process a WhatsApp message and return the agent's texto response.

        Returns the content of the 'mensaje' JSON field from the LLM response,
        or the raw text if the response is not a valid JSON.
        """
        last_user_text = messages[-1].content if messages else ""

        # ── Deterministic intake (steps 1-6) — guarantees the flow order ──
        # When active it asks one canonical question per turn and returns early; when the
        # intake completes it hands off to the LLM (steps 7-12) with all data injected.
        intake_handoff: Optional[str] = None
        if settings.INTAKE_ENABLED:
            # The intake always asks for the real name (the WhatsApp profile name is often
            # wrong). nombre_whatsapp is only used to build the CRM name afterwards.
            intake_reply, intake_handoff = await self._intake_turn(
                wa_id, last_user_text, nombre_whatsapp
            )
            if intake_reply is not None:
                return intake_reply

        graph = await self._get_graph()
        callbacks: list[BaseCallbackHandler] = [langfuse_callback_handler] if settings.LANGFUSE_TRACING_ENABLED else []
        config: RunnableConfig = {
            "configurable": {"thread_id": wa_id},
            "callbacks": callbacks,
            "metadata": {
                "wa_id": wa_id,
                "is_new_patient": is_new_patient,
                "ci_paciente": ci_paciente,
                "seguro_paciente": seguro_paciente,
                "nombre_registrado": nombre_registrado,
                "nombre_whatsapp": nombre_whatsapp,
                "intake_handoff": intake_handoff,
            },
            "recursion_limit": 50,
        }

        from app.services.memory import memory_service

        try:
            if settings.ODONTOKING_MEMORY_ENABLED:
                state_result, memory_result = await asyncio.gather(
                    graph.aget_state(config),
                    memory_service.search(wa_id, messages[-1].content if messages else ""),
                    return_exceptions=True,
                )
                state = state_result if not isinstance(state_result, BaseException) else None
                relevant_memory = memory_result if isinstance(memory_result, str) else ""
                if isinstance(memory_result, Exception):
                    logger.warning("odontoking_memory_search_failed", wa_id=wa_id, error=str(memory_result))
            else:
                state = await graph.aget_state(config)
                relevant_memory = ""

            existing_count = len(state.values.get("messages", [])) if state and state.values else 0

            if state and state.next:
                logger.info("odontoking_resuming_graph", wa_id=wa_id)
                response = await graph.ainvoke(Command(resume=messages[-1].content), config=config)
            else:
                response = await graph.ainvoke(
                    input={"messages": dump_messages(messages), "long_term_memory": relevant_memory},
                    config=config,
                )

            # Persist new messages (human + AI + tool) to chat_histories_odonto
            new_msgs = [m for m in response.get("messages", [])[existing_count:] if isinstance(m, BaseMessage)]
            if new_msgs:
                task = asyncio.create_task(_persist_messages_async(wa_id, new_msgs))
                self._persist_tasks.add(task)
                task.add_done_callback(self._persist_tasks.discard)

            # Extract mensaje from JSON response
            ai_messages = [m for m in response.get("messages", []) if isinstance(m, AIMessage) and m.content]
            if not ai_messages:
                return "Disculpe, ocurrió un error. Por favor intente de nuevo."

            last_content = ai_messages[-1].content
            if isinstance(last_content, list):
                last_content = " ".join(
                    block.get("text", "") for block in last_content if isinstance(block, dict)
                )

            # Try to extract "mensaje" from JSON
            try:
                parsed = json.loads(last_content)
                mensaje = parsed.get("mensaje", last_content)
            except (json.JSONDecodeError, AttributeError):
                mensaje = last_content

            # Fire-and-forget memory update (only when memory is enabled)
            if settings.ODONTOKING_MEMORY_ENABLED:
                try:
                    openai_msgs = cast(list[dict], convert_to_openai_messages(response["messages"]))
                    asyncio.create_task(memory_service.add(wa_id, openai_msgs, {"wa_id": wa_id}))
                except Exception as mem_err:
                    logger.warning("odontoking_memory_add_skipped", wa_id=wa_id, error=str(mem_err))

            return str(mensaje)

        except GraphInterrupt:
            state = await graph.aget_state(config)
            interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Aguarde un momento."
            return str(interrupt_value)
        except GraphRecursionError:
            logger.warning("odontoking_recursion_limit_hit", wa_id=wa_id)
            try:
                await self.clear_history(wa_id)
                logger.info("odontoking_stuck_state_cleared", wa_id=wa_id)
            except Exception as clear_err:
                logger.warning("odontoking_clear_history_failed", wa_id=wa_id, error=str(clear_err))
            return "Lo siento, ocurrió un error procesando su solicitud. Por favor, intente nuevamente con su consulta."
        except Exception as e:
            logger.exception("odontoking_get_response_failed", wa_id=wa_id, error=str(e))
            raise


    async def close(self) -> None:
        """Await pending persist tasks and close the connection pool on shutdown."""
        if self._persist_tasks:
            await asyncio.gather(*self._persist_tasks, return_exceptions=True)
        if self._connection_pool:
            await self._connection_pool.close()
            logger.info("odontoking_connection_pool_closed")

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
        logger.info("odontoking_history_cleared", wa_id=wa_id)


odontoking_agent = OdontokingAgent()
