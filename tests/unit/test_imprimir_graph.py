"""Unit tests for imprimir_graph helpers."""

from langchain_core.messages import AIMessage, ToolMessage

from app.core.langgraph.imprimir_graph import _ended_on_menu


def _tool(name: str, content: str) -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id="x")


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
