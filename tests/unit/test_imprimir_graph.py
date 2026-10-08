"""Unit tests for imprimir_graph helpers."""

from langchain_core.messages import AIMessage, ToolMessage

from app.core.langgraph.imprimir_graph import _ended_on_menu, _thread_tool_names


def _tool(name: str, content: str) -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id="x")


def _ai_calls(*names: str) -> AIMessage:
    """An AIMessage carrying the given tool calls (as the graph records them)."""
    return AIMessage(
        content="",
        tool_calls=[{"name": n, "args": {}, "id": f"tc_{i}"} for i, n in enumerate(names)],
    )


# NOTE: there is deliberately no post-handoff history guard. Silence while a human handles the
# conversation is decided by handoff.open on the webhook (app/api/v1/crm.py), which is NOT permanent:
# once the CRM resolves the handoff the agent re-engages, even if an advisor wrote in between
# (CRM note 16/09/2026). A checkpoint-history guard silenced threads forever and is gone.


class TestEndedOnMenu:
    """_ended_on_menu must key off the LAST message only, not the last ToolMessage in history."""

    def test_true_when_last_message_is_a_sent_menu(self):
        """A turn that ends on a successful mostrar_opciones → nothing to send on top."""
        msgs = [AIMessage(content=""), _tool("mostrar_opciones", "Opciones enviadas (5). Esperá...")]
        assert _ended_on_menu(msgs) is True

    def test_false_when_text_reply_follows_a_prior_menu(self):
        """THE BUG: a text turn after a menu turn still carries the old menu ToolMessage in history.

        Scanning back to it wrongly suppressed the reply and left the bot silent after a selection.
        """
        msgs = [
            _tool("mostrar_opciones", "Opciones enviadas (5). Esperá..."),  # previous turn
            AIMessage(content="¿Cuántas unidades necesita?"),               # this turn's reply
        ]
        assert _ended_on_menu(msgs) is False

    def test_false_when_menu_was_not_sent(self):
        """A 501/error from mostrar_opciones is not 'Opciones enviadas' → the model still owes a reply."""
        msgs = [_tool("mostrar_opciones", "Este canal no admite botones. Mandá las opciones como texto...")]
        assert _ended_on_menu(msgs) is False

    def test_false_for_other_tools(self):
        """A price lookup is not a menu."""
        assert _ended_on_menu([_tool("precio_producto", '{"estado": "resuelto"}')]) is False

    def test_false_on_empty(self):
        """No messages → nothing sent a menu."""
        assert _ended_on_menu([]) is False


class TestThreadToolNames:
    """_thread_tool_names powers the 'derived without creating the quote' warning."""

    def test_collects_tool_names_across_messages(self):
        """Every AIMessage tool call across the thread is collected."""
        msgs = [
            _ai_calls("precio_producto"),
            _tool("precio_producto", '{"estado": "resuelto"}'),
            _ai_calls("register_cotizacion", "crear_cotizacion"),
        ]
        assert _thread_tool_names(msgs) == {"precio_producto", "register_cotizacion", "crear_cotizacion"}

    def test_detects_close_without_quote(self):
        """A close that registered + derived but never created the quote is detectable."""
        names = _thread_tool_names([_ai_calls("register_cotizacion", "derivar_a_asesor")])
        assert "derivar_a_asesor" in names
        assert "crear_cotizacion" not in names  # the bug we now log in prod

    def test_empty_thread(self):
        """No messages / no tool calls → empty set."""
        assert _thread_tool_names([]) == set()
        assert _thread_tool_names([AIMessage(content="hola")]) == set()
