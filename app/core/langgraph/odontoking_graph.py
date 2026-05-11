"""OdontokingAgent — LangGraph agent for WhatsApp dental clinic assistant."""

import asyncio
import json
from datetime import datetime
from typing import (
    AsyncGenerator,
    Optional,
    cast,
)
from urllib.parse import quote_plus

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ToolMessage,
    convert_to_openai_messages,
)
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import (
    END,
    StateGraph,
)
from langchain_core.runnables.config import RunnableConfig
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

from langchain_core.messages import SystemMessage

from app.core.config import settings
from app.core.langgraph.tools.crm import update_crm
from app.core.langgraph.tools.insurance import verify_insurance
from app.core.langgraph.tools.odontoking import (
    get_doctor_schedule,
    get_doctors,
    get_services,
    get_specialties,
)
from app.core.logging import logger
from app.core.observability import langfuse_callback_handler
from app.schemas import GraphState
from app.utils import (
    dump_messages,
    extract_text_content,
    process_llm_response,
)

PostgresConnPool = AsyncConnectionPool[AsyncConnection[DictRow]]

_ODONTOKING_TOOLS = [get_services, get_specialties, get_doctors, get_doctor_schedule, verify_insurance, update_crm]

_ODONTOKING_PROMPT_PATH = __file__.replace("odontoking_graph.py", "../prompts/odontoking.md").replace(
    "langgraph/../", ""
)

import os as _os

_PROMPT_FILE = _os.path.join(_os.path.dirname(__file__), "..", "prompts", "odontoking.md")

with open(_PROMPT_FILE, "r") as _f:
    _PROMPT_TEMPLATE = _f.read()


def _load_odontoking_prompt(wa_id: str) -> str:
    """Render the odontoking system prompt with current datetime and wa_id context."""
    tz_aware = datetime.now()
    current_datetime = tz_aware.strftime("%A %d %B %Y %H:%M")
    return _PROMPT_TEMPLATE.format(current_datetime=current_datetime) + f"\n\n# Contexto\nwa_id del paciente actual: {wa_id}"


class OdontokingAgent:
    """LangGraph agent for the Odontoking WhatsApp dental clinic assistant."""

    def __init__(self):
        """Initialize the Odontoking agent with its own LLM and tool set."""
        self._llm = ChatOpenAI(
            model="gpt-4o-mini",
            api_key=SecretStr(settings.OPENAI_API_KEY),
            max_tokens=2000,
            temperature=0.2,
        ).bind_tools(_ODONTOKING_TOOLS)
        self.tools_by_name = {t.name: t for t in _ODONTOKING_TOOLS}
        self._connection_pool: Optional[PostgresConnPool] = None
        self._graph: Optional[CompiledStateGraph] = None
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
                    max_size=settings.POSTGRES_POOL_SIZE,
                    kwargs={
                        "autocommit": True,
                        "connect_timeout": 5,
                        "prepare_threshold": None,
                        "row_factory": dict_row,
                    },
                )
                await self._connection_pool.open()
                logger.info("odontoking_connection_pool_created")
            except Exception as e:
                logger.exception("odontoking_connection_pool_failed", error=str(e))
                return None
        return self._connection_pool

    async def _chat(self, state: GraphState, config: RunnableConfig) -> Command:
        wa_id = config.get("metadata", {}).get("wa_id", "unknown")
        thread_id = config.get("configurable", {}).get("thread_id")
        system_prompt = _load_odontoking_prompt(wa_id)

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
            result = await self.tools_by_name[tc["name"]].ainvoke(tc["args"])
            return ToolMessage(content=result, name=tc["name"], tool_call_id=tc["id"])

        if len(tool_calls) == 1:
            outputs = [await _execute(tool_calls[0])]
        else:
            outputs = list(await asyncio.gather(*[_execute(tc) for tc in tool_calls]))

        return Command(update={"messages": outputs}, goto="chat")

    async def create_graph(self) -> Optional[CompiledStateGraph]:
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

    async def get_response(
        self,
        messages: list,
        wa_id: str,
    ) -> str:
        """Process a WhatsApp message and return the agent's texto response.

        Returns the content of the 'mensaje' JSON field from the LLM response,
        or the raw text if the response is not a valid JSON.
        """
        graph = await self._get_graph()
        callbacks: list[BaseCallbackHandler] = [langfuse_callback_handler] if settings.LANGFUSE_TRACING_ENABLED else []
        config: RunnableConfig = {
            "configurable": {"thread_id": wa_id},
            "callbacks": callbacks,
            "metadata": {"wa_id": wa_id},
        }

        from app.services.memory import memory_service
        from app.utils import dump_messages as _dump

        try:
            if settings.ODONTOKING_MEMORY_ENABLED:
                state_result, memory_result = await asyncio.gather(
                    graph.aget_state(config),
                    memory_service.search(wa_id, messages[-1].content if messages else ""),
                    return_exceptions=True,
                )
                state = state_result if not isinstance(state_result, Exception) else None
                relevant_memory = memory_result if isinstance(memory_result, str) else ""
                if isinstance(memory_result, Exception):
                    logger.warning("odontoking_memory_search_failed", wa_id=wa_id, error=str(memory_result))
            else:
                state = await graph.aget_state(config)
                relevant_memory = ""

            if state and state.next:
                logger.info("odontoking_resuming_graph", wa_id=wa_id)
                response = await graph.ainvoke(Command(resume=messages[-1].content), config=config)
            else:
                response = await graph.ainvoke(
                    input={"messages": _dump(messages), "long_term_memory": relevant_memory},
                    config=config,
                )

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
        except Exception as e:
            logger.exception("odontoking_get_response_failed", wa_id=wa_id, error=str(e))
            raise


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
