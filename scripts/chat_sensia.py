#!/usr/bin/env python
"""REPL de terminal para chatear con el agente Sensia (Sofía) desde la consola.

Conectado a PRODUCCIÓN (inyectando el env con Railway, sin copiar secretos a disco):

    railway run -- python scripts/chat_sensia.py

A desarrollo (infra local de `make infra-up`):

    source scripts/set_env.sh development && python scripts/chat_sensia.py

Modo por defecto = DRY-RUN: las tools de LECTURA (get_promos, get_persona, get_pedidos)
pegan al CRM real —ves catálogo y precios verdaderos—, pero las de ESCRITURA
(actualizar_pedido, registrar_pedido, derivar_a_asesor, responder_recompra,
reactivar_recompra) se stubbean: imprimen el payload que HABRÍAN mandado y NO tocan el
CRM. Así podés iterar el prompt sin crear leads basura en producción.

Pasá --live para habilitar escrituras REALES (crea/mueve leads de verdad).

Comandos dentro del chat:
    /reset   borra el historial (checkpointer) de este wa_id de prueba
    /quit    salir (o Ctrl-D)
"""

import argparse
import asyncio
import json
import os
import sys

# El repo root (carpeta que contiene `app/`) no está en sys.path cuando se corre
# `python scripts/chat_sensia.py`. Lo agregamos antes de importar `app` (mismo shim
# que scripts/token_report.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.langgraph.sensia_graph import sensia_agent  # noqa: E402
from app.schemas import Message  # noqa: E402

# Tools que ESCRIBEN en el CRM. En dry-run se reemplazan por stubs.
_WRITE_TOOLS = [
    "actualizar_pedido",
    "registrar_pedido",
    "derivar_a_asesor",
    "responder_recompra",
    "reactivar_recompra",
]

_YEL = "\033[33m"
_RED = "\033[31m"
_CYN = "\033[36m"
_RST = "\033[0m"


class _StubTool:
    """Reemplazo dry-run de una tool de escritura: imprime el payload y no toca el CRM.

    Expone la misma interfaz que usa SensiaAgent._tool_call: `.name` y `await .ainvoke(args, config)`.
    El LLM sigue viendo el schema real de la tool (el bind no cambia); sólo se intercepta la ejecución.
    """

    def __init__(self, name: str):
        self.name = name

    async def ainvoke(self, args, config=None):  # noqa: ANN001
        print(f"  {_YEL}[DRY-RUN] {self.name}({json.dumps(args, ensure_ascii=False)}) — NO enviado al CRM{_RST}")
        return json.dumps({"ok": True, "dry_run": True, "tool": self.name, "args": args}, ensure_ascii=False)


def _apply_dry_run() -> None:
    for name in _WRITE_TOOLS:
        if name in sensia_agent.tools_by_name:
            sensia_agent.tools_by_name[name] = _StubTool(name)


async def _chat_loop(wa_id: str, live: bool) -> None:
    modo = f"{_RED}LIVE — escribe al CRM real{_RST}" if live else f"{_YEL}DRY-RUN — writes stubbeados{_RST}"
    if not live:
        _apply_dry_run()

    print(f"\n💧 {_CYN}Sensia REPL{_RST}  ·  wa_id={wa_id}  ·  modo: {modo}")
    print(f"   Escribí un mensaje como si fueras el cliente. Comandos: {_CYN}/reset{_RST}  {_CYN}/quit{_RST}\n")

    # Historial de la charla del lado del REPL. Lo pasamos por `history` en cada turno: ese es el mismo
    # camino que usa el webhook del CRM en producción (el history del evento es la fuente de verdad) y,
    # a diferencia del checkpointer, NO exige una Postgres levantada —el REPL anda aunque no haya DB.
    # Cada entrada: {"sender": "contact"|"ia", "content": ...} (contact → Human, ia → AI en el grafo).
    convo: list[dict] = []

    try:
        while True:
            try:
                text = await asyncio.to_thread(input, "👤 vos: ")
            except (EOFError, KeyboardInterrupt):
                break
            text = text.strip()
            if not text:
                continue
            if text == "/quit":
                break
            if text == "/reset":
                convo.clear()
                try:
                    await sensia_agent.clear_history(wa_id)
                except Exception:  # noqa: BLE001  (sin DB clear_history falla; el convo local ya se vació)
                    pass
                print("   (historial reiniciado)\n")
                continue
            convo.append({"sender": "contact", "content": text})
            try:
                reply = await sensia_agent.get_response(
                    [Message(role="user", content=text)], wa_id, history=convo
                )
            except Exception as e:  # noqa: BLE001
                print(f"   {_RED}[error] {e}{_RST}\n")
                convo.pop()  # el turno no salió: no lo dejamos en el historial
                continue
            convo.append({"sender": "ia", "content": reply})
            print(f"\n🤖 Sofía: {reply}\n")
    finally:
        await sensia_agent.close()
        print("\nChau 👋")


def main() -> None:
    """Parsea argumentos y arranca el loop de chat."""
    p = argparse.ArgumentParser(description="Chat REPL con el agente Sensia (Sofía)")
    p.add_argument(
        "--wa-id",
        default="549388REPLTEST001",
        help="wa_id de prueba = thread_id del checkpointer (usá uno que no sea de un cliente real)",
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="Habilita escrituras REALES al CRM (por defecto dry-run: no escribe)",
    )
    args = p.parse_args()
    asyncio.run(_chat_loop(args.wa_id, args.live))


if __name__ == "__main__":
    main()
